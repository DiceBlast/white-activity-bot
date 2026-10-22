from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from discord.ext import tasks

from backup import create_backup
from config_manager import WEEKDAYS, load_config
from database import (
    leaderboard,
    claim_available_rewards,
    load_scheduler,
    load_users,
    monthly_winners,
    reset_monthly,
    save_monthly_winners,
    save_scheduler,
)
from logger import get_logger, important
from messages import event_end, event_reminder, event_start, monthly_winner_message, weekly_message
from voice import flush_guild_through, flush_guild

log = get_logger('activitybot.scheduler')


def _matches(moment, day, time_value):
    hour, minute = map(int, time_value.split(':'))
    return moment.strftime('%A').lower() == day.lower() and moment.hour == hour and moment.minute == minute


def _next_event_start(now, event_cfg):
    target_weekday = WEEKDAYS[event_cfg['start_day'].lower()]
    hour, minute = map(int, event_cfg['start_time'].split(':'))
    days_ahead = (target_weekday - now.weekday()) % 7
    event_start_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=days_ahead)
    if event_start_time <= now:
        event_start_time += timedelta(days=7)
    return event_start_time


def leaderboard_ranking_text(guild_id, config):
    rows = leaderboard(guild_id, config)
    if not rows:
        return 'No scores yet.'
    medals = ['🥇', '🥈', '🥉']
    lines = []
    for index, user in enumerate(rows):
        prefix = medals[index] if index < 3 else f'{index + 1}.'
        lines.append(f"{prefix} {user['name']} — {user['score']} {config['branding']['currency_name']}")
    return '\n'.join(lines)


def leaderboard_message(guild_id, config):
    return weekly_message(config, leaderboard_ranking_text(guild_id, config))


async def run_scheduler_iteration(bot):
    for guild in bot.guilds:
        try:
            await _run_guild_scheduler_iteration(guild)
        except Exception:
            log.exception('Scheduler iteration failed for guild | guild_id=%s', guild.id)


async def _run_guild_scheduler_iteration(guild):
    config = load_config(guild.id)
    tz = ZoneInfo(config['timezone'])
    now = datetime.now(tz)
    state = load_scheduler(guild.id)
    announcement_id = int(config['channels'].get('announcement_channel', 0))
    channel = guild.get_channel(announcement_id) if announcement_id else None

    # Periodic backups (default every 6 hours) plus a daily marker. The
    # timestamp marker is persisted below, so restarts do not trigger a burst.
    backup_cfg = config.get('backups', {})
    if backup_cfg.get('enabled', True):
        interval_hours = max(1, min(24, int(backup_cfg.get('interval_hours', 6))))
        last_backup = state.get('last_periodic_backup')
        due = True
        if last_backup:
            try:
                last_dt = datetime.fromisoformat(last_backup)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=tz)
                due = now - last_dt >= timedelta(hours=interval_hours)
            except (TypeError, ValueError):
                due = True
        if due:
            try:
                create_backup(guild.id, config, reason='periodic')
                state['last_periodic_backup'] = now.isoformat()
                state['last_daily_backup'] = now.strftime('%Y-%m-%d')
                log.info('Periodic backup completed | guild_id=%s interval_hours=%s', guild.id, interval_hours)
            except Exception:
                log.exception('Periodic backup failed | guild_id=%s', guild.id)

    event_cfg = config['event']
    if channel and event_cfg.get('enabled', True):
        if event_cfg.get('reminder_enabled', True):
            event_start_time = _next_event_start(now, event_cfg)
            reminder_minutes = int(event_cfg.get('reminder_minutes_before', 60))
            reminder_time = event_start_time - timedelta(minutes=reminder_minutes)
            reminder_marker = f'event_reminder:{event_start_time.isoformat()}'
            if reminder_time <= now < event_start_time and state.get('last_event_reminder') != reminder_marker:
                await channel.send(event_reminder(config, reminder_minutes))
                state['last_event_reminder'] = reminder_marker
                important('EVENT REMINDER sent | guild=%s | starts=%s', guild.name, event_start_time.strftime('%A %H:%M'))
                log.info('Event reminder sent | guild_id=%s marker=%s', guild.id, reminder_marker)

        date_key = now.strftime('%Y-%m-%d')
        if _matches(now, event_cfg['start_day'], event_cfg['start_time']):
            marker = f'event_start:{date_key}:{event_cfg["start_time"]}'
            if state.get('last_event_start') != marker:
                await channel.send(event_start(config))
                state['last_event_start'] = marker
                important('EVENT START announced | guild=%s | x%s', guild.name, event_cfg['multiplier'])
                log.info('Event start announced | guild_id=%s marker=%s', guild.id, marker)

        if event_cfg.get('end_announcement_enabled', True) and _matches(now, event_cfg['end_day'], event_cfg['end_time']):
            marker = f'event_end:{date_key}:{event_cfg["end_time"]}'
            if state.get('last_event_end') != marker:
                await channel.send(event_end(config))
                state['last_event_end'] = marker
                important('EVENT END announced | guild=%s', guild.name)
                log.info('Event end announced | guild_id=%s marker=%s', guild.id, marker)

    weekly = config['weekly_announcement']
    if channel and weekly.get('enabled', True) and _matches(now, weekly['day'], weekly['time']):
        marker = now.strftime('%Y-%m-%d')
        if state.get('last_weekly') != marker:
            await channel.send(leaderboard_message(guild.id, config))
            state['last_weekly'] = marker
            important('WEEKLY LEADERBOARD sent | guild=%s', guild.name)
            log.info('Weekly leaderboard sent | guild_id=%s marker=%s', guild.id, marker)

    if config['monthly'].get('enabled', True):
        users = load_users(guild.id)
        periods = {str(u.get('score_period', '')) for u in users.values() if u.get('score_period')}
        current_period = now.strftime('%Y-%m')
        old_periods = sorted(p for p in periods if p and p != current_period)
        if old_periods:
            old_period = old_periods[0]
            # Only split at a known, adjacent month boundary. Older/mixed
            # periods require manual inspection, not a destructive reset.
            previous_month = (now.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
            if len(old_periods) != 1 or old_period != previous_month:
                log.error('Monthly reset deferred: ambiguous periods | guild_id=%s periods=%s current=%s',
                          guild.id, sorted(periods), current_period)
                save_scheduler(guild.id, state)
                return
            boundary = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            # Persist pre-boundary minutes BEFORE selecting winners and resetting.
            # Do not reset if any session failed to persist.
            if not flush_guild_through(guild.id, boundary.timestamp()):
                log.error('Monthly reset deferred: voice flush failed | guild_id=%s', guild.id)
                save_scheduler(guild.id, state)
                return
            try:
                create_backup(guild.id, config, reason=f'before_monthly_reset_{old_period}')
            except Exception:
                log.exception('Pre-monthly backup failed; reset deferred | guild_id=%s period=%s', guild.id, old_period)
                save_scheduler(guild.id, state)
                return

            # Materialize complete pre-boundary intervals before choosing winners.
            # Remaining partial interval credit carries forward intact.
            claim_available_rewards(guild.id, config)
            winners = monthly_winners(guild.id, config)
            if winners:
                save_monthly_winners(guild.id, old_period, winners, config['monthly'].get('prizes', {}))
                if channel and state.get('monthly_announced') != old_period:
                    await channel.send(monthly_winner_message(config, winners))
                    state['monthly_announced'] = old_period
                    important('MONTHLY WINNERS announced | guild=%s | count=%s', guild.name, len(winners))
                    log.info('Monthly winners announced | guild_id=%s period=%s count=%s', guild.id, old_period, len(winners))
            reset_monthly(guild.id, current_period)
            # Credit post-midnight activity to the NEW month only.
            flush_guild(guild.id, current_time=now.timestamp())
            state['monthly_reset'] = current_period
            log.info('Monthly scores reset | guild_id=%s new_period=%s', guild.id, current_period)

    save_scheduler(guild.id, state)


def start_scheduler(bot):
    existing = getattr(bot, 'scheduler_task', None)
    if existing is not None and existing.is_running():
        return existing

    @tasks.loop(minutes=1, reconnect=True)
    async def scheduler_loop():
        await run_scheduler_iteration(bot)

    @scheduler_loop.before_loop
    async def before_scheduler():
        await bot.wait_until_ready()

    @scheduler_loop.error
    async def scheduler_error(error):
        log.error('Scheduler loop error: %s', error, exc_info=(type(error), error, error.__traceback__))

    bot.scheduler_task = scheduler_loop
    scheduler_loop.start()
    return scheduler_loop
