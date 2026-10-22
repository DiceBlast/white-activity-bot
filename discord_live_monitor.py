"""Read-only Discord-side monitoring, scoped to the invoking guild.

Optional persistent panels are stored independently of existing user data.
"""
import asyncio
import json
import logging
import os
import time
from pathlib import Path

import discord
from discord import app_commands

from config_manager import load_config
from database import leaderboard as get_leaderboard
from event_logic import is_event_active

log = logging.getLogger('activitybot.live_monitor')
ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / 'logs' / 'discord_live_monitors.json'
REFRESH_SECONDS = 30


def _read_state():
    try:
        raw = json.loads(STATE_FILE.read_text(encoding='utf-8'))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(value):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_name(STATE_FILE.name + '.tmp')
    try:
        tmp.write_text(json.dumps(value, indent=2), encoding='utf-8')
        os.replace(tmp, STATE_FILE)
    finally:
        if tmp.exists():
            tmp.unlink()


def _is_manager(interaction):
    if interaction.guild is None:
        return False
    permissions = interaction.user.guild_permissions
    return bool(permissions.manage_guild or permissions.administrator)


def build_embed(guild):
    """Only guild-scoped, read-only views. Never writes to score/config files."""
    config = load_config(guild.id)
    users_in_voice = [m for channel in guild.voice_channels for m in channel.members if not m.bot]
    tracked = len(users_in_voice)
    try:
        rows = get_leaderboard(guild.id, config)[:5]
        currency = str(config['branding'].get('currency_name', 'points'))
        lines = [f"{i}. {discord.utils.escape_markdown(str(u.get('name', 'Member'))[:45])} — {u.get('score', 0)} {currency}"
                 for i, u in enumerate(rows, 1)]
    except Exception:
        log.exception('Could not load leaderboard for guild %s', guild.id)
        lines = ['Leaderboard temporarily unavailable']
    try:
        event_active = is_event_active(config)
        multiplier = int(config['event'].get('multiplier', 1)) if event_active else 1
        event_text = f"{'Active' if event_active else 'Inactive'} • x{multiplier} now"
    except Exception:
        event_text = 'Event status unavailable'
    embed = discord.Embed(title='📡 Activity Rewards — Server Monitor', color=discord.Color.blurple())
    embed.add_field(name='Server', value=discord.utils.escape_markdown(guild.name), inline=True)
    embed.add_field(name='Members', value=str(guild.member_count if guild.member_count is not None else 'Unknown'), inline=True)
    embed.add_field(name='In voice', value=str(tracked), inline=True)
    embed.add_field(name='Event', value=event_text, inline=False)
    embed.add_field(name='Top 5', value='\n'.join(lines)[:1000] if lines else 'No scores yet', inline=False)
    embed.set_footer(text='Voice count shows channel occupancy, not guaranteed reward eligibility. Updates about every 30 seconds.')
    embed.timestamp = discord.utils.utcnow()
    return embed


class SnapshotView(discord.ui.View):
    def __init__(self, guild_id, user_id):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.user_id = user_id

    @discord.ui.button(label='Refresh', emoji='🔄', style=discord.ButtonStyle.primary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != self.guild_id or interaction.user.id != self.user_id or not _is_manager(interaction):
            await interaction.response.send_message('This panel belongs to another administrator or server.', ephemeral=True)
            return
        await interaction.response.edit_message(embed=build_embed(interaction.guild), view=self)


class LiveMonitorGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name='livemonitor', description='Manage the server live monitor')

    @app_commands.command(name='start', description='Post a self-updating monitor in a private administrator channel')
    @app_commands.describe(channel='Private text channel where the live dashboard should appear')
    async def start(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not _is_manager(interaction):
            await interaction.response.send_message('Manage Server or Administrator permission required.', ephemeral=True)
            return
        if channel.guild.id != interaction.guild_id:
            await interaction.response.send_message('Choose a channel from this server.', ephemeral=True)
            return
        if channel.permissions_for(interaction.guild.default_role).view_channel:
            await interaction.response.send_message('Choose a private admin channel: @everyone currently has View Channel access.', ephemeral=True)
            return
        me = interaction.guild.me
        perms = channel.permissions_for(me)
        if not perms.view_channel or not perms.send_messages or not perms.embed_links or not perms.read_message_history:
            await interaction.response.send_message('Bot needs View Channel, Send Messages, Embed Links, and Read Message History there.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        state = _read_state()
        key = str(interaction.guild_id)
        if key in state:
            await interaction.followup.send('A live monitor is already configured. Use `/livemonitor stop` first.', ephemeral=True)
            return
        try:
            message = await channel.send(embed=build_embed(interaction.guild))
        except discord.HTTPException:
            await interaction.followup.send('Could not post the monitor. Please check bot channel permissions.', ephemeral=True)
            return
        state[key] = {'channel_id': channel.id, 'message_id': message.id}
        _save_state(state)
        await interaction.followup.send(f'Live monitoring enabled in {channel.mention}. Updates every {REFRESH_SECONDS} seconds.', ephemeral=True)

    @app_commands.command(name='stop', description='Stop updating this server live monitor')
    async def stop(self, interaction: discord.Interaction):
        if not _is_manager(interaction):
            await interaction.response.send_message('Manage Server or Administrator permission required.', ephemeral=True)
            return
        state = _read_state()
        previous = state.pop(str(interaction.guild_id), None)
        if previous is None:
            await interaction.response.send_message('No live monitor is enabled for this server.', ephemeral=True)
            return
        _save_state(state)
        await interaction.response.send_message('Live monitoring stopped. The existing message is left as the last snapshot.', ephemeral=True)


def register_monitor_commands(bot):
    @bot.tree.command(name='adminpanel', description='Private, read-only activity dashboard for server administrators')
    async def adminpanel(interaction: discord.Interaction):
        if not _is_manager(interaction):
            await interaction.response.send_message('Manage Server or Administrator permission required.', ephemeral=True)
            return
        await interaction.response.send_message(embed=build_embed(interaction.guild),
                                                view=SnapshotView(interaction.guild_id, interaction.user.id), ephemeral=True)

    bot.tree.add_command(LiveMonitorGroup())


async def monitor_loop(bot):
    """Edits existing messages; never creates replacement messages automatically."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        await asyncio.sleep(REFRESH_SECONDS)
        for guild_id, ref in list(_read_state().items()):
            try:
                guild = bot.get_guild(int(guild_id))
                if guild is None:
                    continue
                channel = guild.get_channel(int(ref['channel_id']))
                if not isinstance(channel, discord.TextChannel):
                    continue
                message = await channel.fetch_message(int(ref['message_id']))
                await message.edit(embed=build_embed(guild))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError, KeyError, TypeError):
                log.warning('Cannot refresh Discord live monitor for guild %s; check channel/message access', guild_id)
            except Exception:
                log.exception('Unexpected live monitor refresh error for guild %s', guild_id)
