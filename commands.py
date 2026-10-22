import csv
import io

import discord
from discord import app_commands

from config_manager import load_config, save_config, setup_status
from database import (
    add_score,
    ensure_user,
    leaderboard as get_leaderboard,
    load_users,
    load_winners,
    remove_score,
    reward_credit_weighted_seconds,
)
from event_logic import is_event_active
from logger import get_logger
from setup_wizard import setup_panel
from voice import active_sessions

log = get_logger('activitybot.commands')


def _is_admin(interaction: discord.Interaction, config: dict) -> bool:
    permissions = interaction.user.guild_permissions
    if permissions.manage_guild or permissions.administrator:
        return True
    role_id = int(config.get('admin_role_id', 0))
    return bool(role_id) and any(role.id == role_id for role in interaction.user.roles)


async def _require_admin(interaction: discord.Interaction):
    if interaction.guild is None or interaction.guild_id is None:
        await interaction.response.send_message('❌ This command can only be used inside a server.', ephemeral=True)
        return None
    config = load_config(interaction.guild_id)
    if _is_admin(interaction, config):
        return config
    await interaction.response.send_message('❌ You need Manage Server, Administrator, or the configured admin role.', ephemeral=True)
    return None


def _rank_for(guild_id, user_id, config):
    excluded = {str(x) for x in config['leaderboard'].get('excluded_user_ids', [])}
    rows = [(uid, u) for uid, u in load_users(guild_id).items() if uid not in excluded]
    rows.sort(key=lambda item: (-int(item[1].get('score', 0)), -float(item[1].get('voice_seconds', 0))))
    for index, (uid, _) in enumerate(rows, 1):
        if uid == str(user_id):
            return index
    return None


def _active_for(guild_id, user_id):
    session = active_sessions.get((guild_id, user_id))
    if not session:
        return None
    return session.get('channel_name', 'Voice')


def register_commands(bot):
    @bot.tree.command(name='setup', description='Open the guided setup wizard for this server')
    async def setup(interaction: discord.Interaction):
        config = await _require_admin(interaction)
        if config is None:
            return
        text, view = setup_panel(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(text + '\n\nUse the buttons below to configure everything.', view=view, ephemeral=True)

    @bot.tree.command(name='help', description='Show available bot commands')
    async def help_command(interaction: discord.Interaction):
        if interaction.guild_id is None:
            await interaction.response.send_message('❌ Use this command inside a server.', ephemeral=True)
            return
        config = load_config(interaction.guild_id)
        b = config['branding']; currency = b['currency_name']
        text = (
            f"🤖 **{b['bot_nickname']} Commands**\n\n"
            "**Member Commands**\n"
            "`/profile` — Full activity profile and rank\n"
            "`/stats` — Score and lifetime voice time\n"
            "`/progress` — Progress toward the next reward\n"
            "`/leaderboard` — Current activity leaderboard\n"
            "`/event` — Event status and schedule\n"
            "`/winners` — Monthly winners\n"
            "`/settings` — Current server configuration\n"
            "`/help` — This command list"
        )
        if _is_admin(interaction, config):
            text += (
                "\n\n**Admin Commands**\n"
                "`/setup` — Configure the entire bot\n"
                "`/adminpanel` — Private monitoring snapshot\n"
                "`/livemonitor start` / `stop` — Private live monitoring channel\n"
                "`/userinfo` — Inspect a member's activity data\n"
                "`/exportdata` — Export member activity as CSV\n"
                "`/exclude` / `/include` — Manage leaderboard/monthly eligibility\n"
                "`/announce` — Send an announcement\n"
                f"`/add` / `/remove` — Modify {currency}"
            )
        await interaction.response.send_message(text, ephemeral=True)

    @bot.tree.command(name='exclude', description='Exclude a member from leaderboard and/or monthly prize')
    @app_commands.describe(member='Member to exclude', leaderboard='Exclude from public leaderboard', monthly='Exclude from monthly winner eligibility')
    async def exclude(interaction: discord.Interaction, member: discord.Member, leaderboard: bool = True, monthly: bool = True):
        config = await _require_admin(interaction)
        if config is None:
            return
        for key, should_add in (('leaderboard', leaderboard), ('monthly', monthly)):
            ids = config[key]['excluded_user_ids']
            if should_add and member.id not in ids:
                ids.append(member.id)
        save_config(interaction.guild_id, config)
        log.info('USER EXCLUDED | guild_id=%s admin_id=%s user_id=%s leaderboard=%s monthly=%s', interaction.guild_id, interaction.user.id, member.id, leaderboard, monthly)
        await interaction.response.send_message(f'✅ Exclusion updated for {member.mention}.', ephemeral=True)

    @bot.tree.command(name='include', description='Remove a member from leaderboard and monthly exclusion lists')
    async def include(interaction: discord.Interaction, member: discord.Member):
        config = await _require_admin(interaction)
        if config is None:
            return
        for key in ('leaderboard', 'monthly'):
            config[key]['excluded_user_ids'] = [x for x in config[key]['excluded_user_ids'] if int(x) != member.id]
        save_config(interaction.guild_id, config)
        log.info('USER INCLUDED | guild_id=%s admin_id=%s user_id=%s', interaction.guild_id, interaction.user.id, member.id)
        await interaction.response.send_message(f'✅ {member.mention} can appear on the leaderboard and win again.', ephemeral=True)

    @bot.tree.command(name='announce', description='Send an announcement to the configured announcement channel')
    async def announce(interaction: discord.Interaction, message: str):
        config = await _require_admin(interaction)
        if config is None:
            return
        channel_id = int(config['channels'].get('announcement_channel', 0))
        channel = interaction.guild.get_channel(channel_id) if channel_id else None
        if channel is None:
            await interaction.response.send_message('❌ Announcement channel is not configured. Run `/setup`.', ephemeral=True)
            return
        try:
            await channel.send(f'📢 **Announcement**\n\n{message}')
        except discord.Forbidden:
            await interaction.response.send_message('❌ I cannot send messages in that channel.', ephemeral=True)
            return
        log.info('ANNOUNCEMENT SENT | guild_id=%s admin_id=%s channel_id=%s', interaction.guild_id, interaction.user.id, channel.id)
        await interaction.response.send_message(f'✅ Announcement sent to {channel.mention}.', ephemeral=True)

    @bot.tree.command(name='add', description='Add reward points to a member')
    async def add_points(interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 100000]):
        config = await _require_admin(interaction)
        if config is None:
            return
        total = add_score(interaction.guild_id, member.id, int(amount), member.display_name, config)
        if total is None:
            await interaction.response.send_message('❌ Could not update that member.', ephemeral=True)
            return
        b = config['branding']
        log.info('ADMIN ADD | guild_id=%s admin_id=%s user_id=%s amount=%s total=%s', interaction.guild_id, interaction.user.id, member.id, amount, total)
        await interaction.response.send_message(f"✅ Added **{amount} {b['currency_name']}** to {member.mention}.\n{b['currency_emoji']} New total: **{total}**", ephemeral=True)

    @bot.tree.command(name='remove', description='Remove reward points from a member')
    async def remove_points(interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 100000]):
        config = await _require_admin(interaction)
        if config is None:
            return
        total = remove_score(interaction.guild_id, member.id, int(amount), config)
        if total is None:
            await interaction.response.send_message('❌ That member does not have a score record yet.', ephemeral=True)
            return
        b = config['branding']
        log.info('ADMIN REMOVE | guild_id=%s admin_id=%s user_id=%s amount=%s total=%s', interaction.guild_id, interaction.user.id, member.id, amount, total)
        await interaction.response.send_message(f"✅ Removed **{amount} {b['currency_name']}** from {member.mention}.\n{b['currency_emoji']} New total: **{total}**", ephemeral=True)

    @bot.tree.command(name='userinfo', description='View detailed activity data for a member')
    async def userinfo(interaction: discord.Interaction, member: discord.Member):
        config = await _require_admin(interaction)
        if config is None:
            return
        user = ensure_user(interaction.guild_id, member.id, member.display_name, config)
        rank = _rank_for(interaction.guild_id, member.id, config)
        active = _active_for(interaction.guild_id, member.id)
        lb_ex = member.id in {int(x) for x in config['leaderboard'].get('excluded_user_ids', [])}
        mo_ex = member.id in {int(x) for x in config['monthly'].get('excluded_user_ids', [])}
        b = config['branding']
        rank_text = f"#{rank}" if rank else "Excluded / Unranked"
        text = (
            f"👤 **{member.display_name}**\n"
            f"🆔 `{member.id}`\n"
            f"{b['currency_emoji']} Score: **{user['score']} {b['currency_name']}**\n"
            f"🏆 Rank: **{rank_text}**\n"
            f"🎙️ Lifetime voice: **{float(user.get('voice_seconds', 0))/3600:.2f} hours**\n"
            f"⏱️ Normal credit: **{int(float(user.get('normal_credit_seconds', 0)))} sec**\n"
            f"🔥 Event credit: **{int(float(user.get('event_credit_seconds', 0)))} sec**\n"
            f"🎁 First bonus claimed: **{user.get('first_bonus_claimed', False)}**\n"
            f"🔊 Active voice session: **{active or 'No'}**\n"
            f"🚫 Leaderboard excluded: **{lb_ex}**\n"
            f"🚫 Monthly excluded: **{mo_ex}**"
        )
        await interaction.response.send_message(text, ephemeral=True)

    @bot.tree.command(name='exportdata', description='Export activity data as a CSV file')
    async def exportdata(interaction: discord.Interaction):
        config = await _require_admin(interaction)
        if config is None:
            return
        users = load_users(interaction.guild_id)
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(['discord_id', 'name', 'score', 'voice_hours', 'normal_credit_seconds', 'event_credit_seconds', 'first_bonus_claimed', 'score_period', 'leaderboard_excluded', 'monthly_excluded'])
        lb_ex = {str(x) for x in config['leaderboard'].get('excluded_user_ids', [])}
        mo_ex = {str(x) for x in config['monthly'].get('excluded_user_ids', [])}
        for uid, user in sorted(users.items(), key=lambda x: x[1].get('name', '').lower()):
            writer.writerow([uid, user.get('name',''), int(user.get('score',0)), f"{float(user.get('voice_seconds',0))/3600:.4f}", int(float(user.get('normal_credit_seconds',0))), int(float(user.get('event_credit_seconds',0))), bool(user.get('first_bonus_claimed',False)), user.get('score_period',''), uid in lb_ex, uid in mo_ex])
        data = output.getvalue().encode('utf-8-sig')
        file = discord.File(io.BytesIO(data), filename=f'activity_export_{interaction.guild_id}.csv')
        log.info('DATA EXPORTED | guild_id=%s admin_id=%s users=%s', interaction.guild_id, interaction.user.id, len(users))
        await interaction.response.send_message(f'✅ Exported **{len(users)}** user record(s).', file=file, ephemeral=True)

    @bot.tree.command(name='settings', description='Show the current bot configuration')
    async def settings(interaction: discord.Interaction):
        config = load_config(interaction.guild_id)
        b=config['branding']; r=config['rewards']; e=config['event']; v=config['voice']; m=config['monthly']; w=config['weekly_announcement']; s=setup_status(config)
        monthly_extra = ''
        if int(m.get('winner_count', 1)) == 3:
            monthly_extra = (
                f"🥈 2nd Prize: **{m.get('prizes', {}).get('2', '')}**\n"
                f"🥉 3rd Prize: **{m.get('prizes', {}).get('3', '')}**\n"
            )
        await interaction.response.send_message(
            f"🤖 Nickname: **{b['bot_nickname']}**\n"
            f"{b['currency_emoji']} Currency: **{b['currency_name']}**\n"
            f"📋 Setup: **{s['done']}/{s['total']}** ({'Complete' if s['complete'] else 'Incomplete'})\n"
            f"⏱️ Reward: **{r['points_per_interval']} every {r['interval_minutes']} min**\n"
            f"🎙️ Minimum VC members: **{v['minimum_members_in_channel']}**\n"
            f"🚫 Excluded voice channels: **{len(v.get('excluded_channel_ids', []))}**\n"
            f"🔥 Event: **{b['event_name']} x{e['multiplier']}** ({'ON' if e['enabled'] else 'OFF'})\n"
            f"📅 {e['start_day']} {e['start_time']} → {e['end_day']} {e['end_time']}\n"
            f"🏆 Weekly: **{'ON' if w['enabled'] else 'OFF'}** — {w['day']} {w['time']}\n"
            f"👑 Monthly: **{'ON' if m['enabled'] else 'OFF'}** — Top **{m.get('winner_count', 1)}**\n"
            f"🥇 1st Prize: **{m.get('prizes', {}).get('1', '')}**\n"
            f"{monthly_extra}"
            f"💾 Backups: **{'ON' if config['backups']['enabled'] else 'OFF'}** at {config['backups']['hour']:02d}:00\n"
            f"🌍 Timezone: **{config['timezone']}**",
            ephemeral=True,
        )

    @bot.tree.command(name='profile', description='Show your full activity profile')
    async def profile(interaction: discord.Interaction):
        config = load_config(interaction.guild_id)
        user = ensure_user(interaction.guild_id, interaction.user.id, interaction.user.display_name, config)
        rank = _rank_for(interaction.guild_id, interaction.user.id, config)
        interval = int(config['rewards']['interval_minutes']) * 60
        progress = int(reward_credit_weighted_seconds(user))
        b=config['branding']
        rank_text = f"#{rank}" if rank else "Unranked"
        text = (
            f"👤 **{interaction.user.display_name}'s Profile**\n\n"
            f"{b['currency_emoji']} Score: **{user['score']} {b['currency_name']}**\n"
            f"🏆 Rank: **{rank_text}**\n"
            f"🎙️ Lifetime voice: **{float(user.get('voice_seconds',0))/3600:.2f} hours**\n"
            f"⏱️ Combined reward progress: **{progress}/{interval} reward-equivalent sec**\n"
            f"🎁 First bonus: **{'Claimed' if user.get('first_bonus_claimed') else 'Available'}**"
        )
        await interaction.response.send_message(text, ephemeral=True)

    @bot.tree.command(name='stats', description='Show your activity stats')
    async def stats(interaction: discord.Interaction):
        config=load_config(interaction.guild_id); user=ensure_user(interaction.guild_id,interaction.user.id,interaction.user.display_name,config); b=config['branding']
        await interaction.response.send_message(f"{b['currency_emoji']} **{user['score']} {b['currency_name']}**\n🎙️ Lifetime voice: **{float(user['voice_seconds'])/3600:.2f} hours**",ephemeral=True)

    @bot.tree.command(name='progress', description='Show progress toward your next reward')
    async def progress(interaction: discord.Interaction):
        config=load_config(interaction.guild_id); user=ensure_user(interaction.guild_id,interaction.user.id,interaction.user.display_name,config); interval=int(config['rewards']['interval_minutes'])*60; progress=reward_credit_weighted_seconds(user)
        await interaction.response.send_message(f"⏱️ Combined reward progress: **{progress:.0f}/{interval} reward-equivalent seconds** — **{max(0,interval-progress):.0f} sec remaining**\n🔥 Event activity counts toward progress using the multiplier active when it was recorded.",ephemeral=True)

    @bot.tree.command(name='leaderboard', description='Show the server activity leaderboard')
    async def leaderboard(interaction: discord.Interaction):
        config=load_config(interaction.guild_id); rows=get_leaderboard(interaction.guild_id,config); b=config['branding']; medals=['🥇','🥈','🥉']
        lines=[f"{medals[i] if i<3 else str(i+1)+'.'} {u['name']} — {u['score']} {b['currency_name']}" for i,u in enumerate(rows)]
        await interaction.response.send_message(f"🏆 **{b['leaderboard_name']}**\n\n"+('\n'.join(lines) if lines else 'No scores yet.'))

    @bot.tree.command(name='event', description='Show the current event status')
    async def event(interaction: discord.Interaction):
        config=load_config(interaction.guild_id); e=config['event']; b=config['branding']; active=is_event_active(config)
        await interaction.response.send_message(f"🔥 **{b['event_name']}** — {'ACTIVE' if active else 'INACTIVE'}\nMultiplier: **x{e['multiplier']}**\nSchedule: **{e['start_day']} {e['start_time']} → {e['end_day']} {e['end_time']}**")

    @bot.tree.command(name='winners', description='Show monthly winners')
    async def winners(interaction: discord.Interaction):
        config=load_config(interaction.guild_id); data=load_winners(interaction.guild_id); b=config['branding']
        if not data:
            await interaction.response.send_message('👑 No monthly winners yet.'); return
        medals=['🥇','🥈','🥉']; blocks=[]
        for month, entry in sorted(data.items(), reverse=True)[:12]:
            rows = entry if isinstance(entry, list) else [entry]
            lines=[f"📅 **{month}**"]
            for i,winner in enumerate(rows[:3]):
                prize=winner.get('prize','')
                lines.append(f"{medals[i]} {winner['name']} — {winner['score']} {b['currency_name']}" + (f" — 🎁 {prize}" if prize else ''))
            blocks.append('\n'.join(lines))
        await interaction.response.send_message(f"👑 **{b['monthly_title']} - Winners**\n\n"+'\n\n'.join(blocks))
