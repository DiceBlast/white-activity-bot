"""Read-only view of persisted scores/config and the bot's transient monitor heartbeat."""
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from event_logic import is_event_active
from config_manager import WEEKDAYS

ROOT = Path(__file__).resolve().parent

# Performance: reuse unchanged JSON files rather than parsing every guild's
# user list/configuration on every dashboard tick. Bounded cache, read-only.
_JSON_CACHE = {}
_CACHE_MAX = 256


def _load_json(path, default):
    path = Path(path)
    try:
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        key = str(path)
        cached = _JSON_CACHE.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        with path.open(encoding='utf-8') as handle:
            value = json.load(handle)
        if len(_JSON_CACHE) >= _CACHE_MAX:
            _JSON_CACHE.clear()
        _JSON_CACHE[key] = (signature, value)
        return value
    except (OSError, ValueError, TypeError):
        _JSON_CACHE.pop(str(path), None)
        return default



def event_schedule(config, now=None):
    """Read-only local-time event state and next transition (weekly repeat)."""
    event = config.get('event', {})
    multiplier = max(1, int(event.get('multiplier', 1)))
    if not event.get('enabled', False) or multiplier <= 1:
        return {'active': False, 'next': None, 'multiplier': 1, 'reason': 'Event disabled'}
    try:
        tz = ZoneInfo(config.get('timezone', 'UTC'))
        now = now or datetime.now(tz)
        now = now.astimezone(tz)
        active = is_event_active(config, now)
        day = event['end_day'] if active else event['start_day']
        clock = event['end_time'] if active else event['start_time']
        hour, minute = (int(x) for x in clock.split(':'))
        weekday = WEEKDAYS[day.lower()]
        days = (weekday - now.weekday()) % 7
        date = (now + timedelta(days=days)).date()
        boundary = datetime(date.year, date.month, date.day, hour, minute, tzinfo=tz)
        if boundary <= now:
            boundary += timedelta(days=7)
        seconds = max(0, int(boundary.timestamp() - now.timestamp()))
        return {'active': active, 'next': boundary.isoformat(timespec='minutes'),
                'seconds_left': seconds, 'multiplier': multiplier, 'reason': None}
    except (ValueError, KeyError, TypeError, OverflowError):
        return {'active': False, 'next': None, 'multiplier': multiplier, 'reason': 'Schedule unavailable'}


def recent_reward_activity(gid, root=ROOT, max_lines=12):
    """Read recent *logged* delivery outcomes, not database transaction history."""
    path = Path(root) / 'logs' / 'bot.log'
    try:
        with path.open('rb') as f:
            f.seek(0, 2)
            end = f.tell()
            f.seek(max(0, end - 256_000))
            if end > 256_000:
                f.readline()
            lines = f.read().decode('utf-8', 'replace').splitlines()
    except OSError:
        return []
    return [line[:240] for line in lines
            if f'guild_id={gid}' in line and
            ('Reward sent' in line or 'Reward notification failed' in line or
             'Reward acknowledgement failed' in line)][-max_lines:]

def guild_metrics(gid, root=ROOT):
    """Return display information. No database writes, migrations, or config mutations."""
    root = Path(root)
    guild_dir = root / 'data' / 'guilds' / str(gid)
    users = _load_json(guild_dir / 'users.json', {})
    cfg = _load_json(guild_dir / 'config.json', {})
    live = _load_json(root / 'logs' / 'dashboard_status.json', {})
    live_guild = live.get('guilds', {}).get(str(gid), {}) if isinstance(live, dict) else {}
    if not isinstance(users, dict):
        users = {}
    ranked = []
    for uid, entry in users.items():
        if not isinstance(entry, dict):
            continue
        try:
            score = int(entry.get('score', 0))
        except (ValueError, TypeError):
            score = 0
        ranked.append((str(entry.get('name') or uid), score))
    ranked.sort(key=lambda row: (-row[1], row[0].casefold()))
    event = cfg.get('event', {}) if isinstance(cfg, dict) else {}
    try:
        multiplier = max(1, int(event.get('multiplier', 1)))
    except (TypeError, ValueError):
        multiplier = 1
    try:
        freshness = time.time() - float(live.get('timestamp', 0))
    except (ValueError, TypeError, AttributeError):
        freshness = float('inf')
    healthy = 0 <= freshness < 40 and bool(live_guild)
    schedule = event_schedule(cfg) if isinstance(cfg, dict) else {}
    sessions = live_guild.get('tracked_sessions', []) if healthy else []
    if not isinstance(sessions, list):
        sessions = []
    sessions = [x for x in sessions[:100] if isinstance(x, dict)]
    return {
        'sessions': sessions,
        'schedule': schedule,
        'top': ranked[:5],
        'multiplier_configured': multiplier,
        'event_enabled': bool(event.get('enabled', False)),
        'voice_users': live_guild.get('voice_users') if healthy else None,
        'members': live_guild.get('members') if healthy else None,
        'name': live_guild.get('name') if healthy else None,
        'online': healthy,
        'uptime_seconds': int(live.get('uptime_seconds', 0)) if healthy else None,
    }
