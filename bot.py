import os
import asyncio
import json
import time
import math
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord.ext import commands

from commands import register_commands
from discord_live_monitor import register_monitor_commands, monitor_loop
from config_manager import load_config
from messages import first_bonus
from logger import get_logger, important, setup_logging
from reward_loop import start_reward_loop
from scheduler import start_scheduler
from analytics_history import record_snapshot
from connection_health import public_connection_status
from voice import pause_all, process_voice_update, restore_active_sessions, active_sessions

setup_logging()
log = get_logger('activitybot.bot')
_startup_backup_done = set()
_monitor_started = False
_monitor_start = time.monotonic()
_connection = {'state': 'starting', 'reconnects': 0, 'last_ready': None,
               'last_disconnect': None, 'last_error': None}


def _connection_snapshot(now=None):
    return public_connection_status(_connection, bot.is_ready(), bot.latency, now=now)


def _persist_monitor_snapshot(path, payload):
    """Blocking filesystem writes run in a worker thread, not on the gateway loop."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(payload), encoding='utf-8')
    temp.replace(path)
    try:
        record_snapshot(path.parent.parent, payload)
    except (OSError, ValueError, TypeError):
        log.warning('Monitoring history sample unavailable')



async def write_monitor_status():
    """Separate ephemeral monitoring heartbeat; never writes scores or settings."""
    path = Path(__file__).resolve().parent / 'logs' / 'dashboard_status.json'
    while not bot.is_closed():
        try:
            guilds = {}
            for guild in bot.guilds:
                guilds[str(guild.id)] = {
                    'name': guild.name,
                    'members': guild.member_count,
                    'voice_users': sum(
                        1 for channel in guild.voice_channels
                        for member in channel.members if not member.bot
                    ),
                }
            # Read-only snapshot of VERIFIED tracked sessions; no additional activity credit.
            now = time.time()
            for (guild_id, user_id), session in list(active_sessions.items()):
                entry = guilds.get(str(guild_id))
                if entry is None:
                    continue
                member = session.get('member')
                entry.setdefault('tracked_sessions', []).append({
                    'user_id': str(user_id),
                    'name': str(getattr(member, 'display_name', user_id))[:80],
                    'channel': str(session.get('channel_name', 'Unknown'))[:80],
                    'started_at': float(session.get('start', now)),
                })
            payload = {'timestamp': now,
                       'uptime_seconds': int(time.monotonic() - _monitor_start),
                       'guilds': guilds, 'discord_connection': _connection_snapshot(now)}
            await asyncio.to_thread(_persist_monitor_snapshot, path, payload)
        except Exception:
            log.exception('Monitoring heartbeat failed')
        await asyncio.sleep(10)



class ActivityBot(commands.Bot):
    async def setup_hook(self):
        register_commands(self)
        register_monitor_commands(self)
        # Sync globally only; avoid creating duplicate guild-scoped commands.
        synced = await self.tree.sync()
        log.info('Synced %s global application commands', len(synced))


intents = discord.Intents.default()
intents.members = True
intents.voice_states = True

bot = ActivityBot(command_prefix='!', intents=intents)


@bot.event
async def on_ready():
    global _monitor_started
    _connection['state'] = 'connected'
    _connection['last_ready'] = time.time()
    _connection['last_error'] = None
    if not _monitor_started:
        _monitor_started = True
        asyncio.create_task(write_monitor_status())
        asyncio.create_task(monitor_loop(bot))
    for guild in bot.guilds:
        config = load_config(guild.id)
        if guild.id not in _startup_backup_done:
            try:
                from backup import create_backup
                await asyncio.to_thread(create_backup, guild.id, config, reason='startup')
                _startup_backup_done.add(guild.id)
            except Exception:
                log.exception('Startup backup failed | guild_id=%s', guild.id)
        nickname = config['branding'].get('bot_nickname', '').strip()
        if nickname:
            try:
                await guild.me.edit(nick=nickname[:32])
            except discord.Forbidden:
                pass
    for guild in bot.guilds:
        log.info('GUILD PRESENT | guild_id=%s | name=%s | members=%s', guild.id, guild.name.replace('\n', ' '), guild.member_count)
    restored = restore_active_sessions(bot)
    start_reward_loop(bot)
    start_scheduler(bot)
    # Show the friendly name configured in /setup, not Discord's internal bot username/ID.
    configured_names = []
    for guild in bot.guilds:
        configured_name = str(load_config(guild.id)['branding'].get('bot_nickname', '')).strip()
        if configured_name and configured_name not in configured_names:
            configured_names.append(configured_name)
    display_name = ' / '.join(configured_names) if configured_names else 'Discord Activity Bot'
    important('ONLINE as %s | restored %s voice sessions', display_name, restored)
    log.info('Bot ready in %s guild(s)', len(bot.guilds))


@bot.event
async def on_voice_state_update(member, before, after):
    # Keep the terminal useful: show real voice joins/leaves, but avoid noisy
    # mute/deafen-only state changes. Every important line also goes to bot.log.
    if not member.bot:
        if before.channel is None and after.channel is not None:
            important(
                'VOICE CONNECT | guild_id=%s | %s -> %s',
                member.guild.id,
                member.display_name,
                after.channel.name,
            )
        elif before.channel is not None and after.channel is None:
            important(
                'VOICE DISCONNECT | guild_id=%s | %s <- %s',
                member.guild.id,
                member.display_name,
                before.channel.name,
            )
        elif (
            before.channel is not None
            and after.channel is not None
            and before.channel.id != after.channel.id
        ):
            log.info(
                'VOICE MOVE | guild_id=%s | %s | %s -> %s',
                member.guild.id,
                member.display_name,
                before.channel.name,
                after.channel.name,
            )

    result = process_voice_update(member, before, after)
    bonus_awards = list(result.get('bonus_awards', []))
    current_bonus = int(result.get('first_bonus', 0))
    if current_bonus > 0:
        bonus_awards.insert(0, (member, current_bonus))
    for bonus_member, bonus in bonus_awards:
        config = load_config(bonus_member.guild.id)
        channel_id = int(config['channels'].get('reward_channel', 0))
        channel = bonus_member.guild.get_channel(channel_id) if channel_id else None
        if channel:
            try:
                await channel.send(first_bonus(bonus_member, bonus, config))
            except Exception:
                log.exception('First bonus notification failed | guild_id=%s user_id=%s',
                              bonus_member.guild.id, bonus_member.id)
            else:
                log.info('First bonus sent | guild=%s user=%s amount=%s', bonus_member.guild.id, bonus_member.id, bonus)


@bot.event
async def on_guild_join(guild):
    log.info('GUILD PRESENT | guild_id=%s | name=%s | members=%s', guild.id, guild.name.replace('\n', ' '), guild.member_count)


@bot.event
async def on_guild_remove(guild):
    log.info('GUILD REMOVED | guild_id=%s | name=%s', guild.id, guild.name.replace('\n', ' '))


@bot.event
async def on_disconnect():
    _connection['state'] = 'reconnecting'
    _connection['last_disconnect'] = time.time()
    _connection['reconnects'] += 1
    paused = pause_all()
    important('BOT DISCONNECTED | paused %s voice sessions', paused)


@bot.event
async def on_resumed():
    _connection['state'] = 'connected'
    _connection['last_ready'] = time.time()
    restored = restore_active_sessions(bot)
    important('BOT RECONNECTED | restored %s voice sessions', restored)


TOKEN = os.environ.get('DISCORD_BOT_TOKEN', '').strip()
if not TOKEN:
    raise RuntimeError('DISCORD_BOT_TOKEN environment variable is not set.')

try:
    bot.run(TOKEN, log_handler=None)
except Exception:
    _connection['state'] = 'disconnected'
    log.exception('Fatal bot error')
    raise
