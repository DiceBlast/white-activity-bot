from copy import deepcopy
from pathlib import Path
from zoneinfo import ZoneInfo

from storage import load_json, save_json

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_ROOT = PROJECT_ROOT / 'data' / 'guilds'

WEEKDAYS = {
    'monday': 0,
    'tuesday': 1,
    'wednesday': 2,
    'thursday': 3,
    'friday': 4,
    'saturday': 5,
    'sunday': 6,
}

DEFAULT_TEMPLATES = {
    'first_bonus': '🎁 {mention} received **+{amount} {currency_name}** as a first voice bonus!',
    'reward_normal': '🎉 {mention} earned **+{amount} {currency_name}**\n{currency_emoji} Total: **{total_score}**',
    'reward_event': '🔥 **{event_name} Reward!**\n\n{mention} earned **+{amount} {currency_name}**\n⚡ Event multiplier: **x{multiplier}**\n{currency_emoji} Total: **{total_score}**',
    'reward_mixed': '🎉 {mention} earned **+{amount} {currency_name}**\n\nNormal: **+{normal_points}**\n{event_name}: **+{event_points}**\n{currency_emoji} Total: **{total_score}**',
    'event_reminder': '⏰ **{event_name} starts soon!**\n\n🔥 Starts in **{reminder_minutes} minutes**\n{currency_emoji} Multiplier: **x{multiplier}**\n🎙️ Get ready to earn more {currency_name}!',
    'event_start': '🔥 **{event_name} is LIVE!**\n\nRewards are now **x{multiplier}**.\nRuns until **{end_day} {end_time}**.',
    'event_end': '🏁 **{event_name} has ended!**\n\nThe **x{multiplier} multiplier** is now finished.\n🎙️ Normal {currency_name} rewards are active again.',
    'weekly': '🏆 **{leaderboard_name}**\n\n{ranking}',
    'monthly_winner': '👑 **{monthly_title}**\n\n{winners}',
}

DEFAULT_CONFIG = {
    'setup': {
        'completed': False,
        'sections': {
            'branding': False,
            'rewards': False,
            'event': False,
            'voice': False,
            'weekly': False,
            'monthly': False,
            'channels': False,
        },
    },
    'branding': {
        'bot_nickname': 'Activity Rewards',
        'currency_name': 'Points',
        'currency_emoji': '⭐',
        'event_name': 'Rush Hour',
        'leaderboard_name': 'Activity Leaderboard',
        'monthly_title': 'Monthly Champion',
    },
    'rewards': {
        'interval_minutes': 60,
        'points_per_interval': 1,
        'first_join_bonus_enabled': True,
        'first_join_bonus': 5,
    },
    'event': {
        'enabled': True,
        'multiplier': 2,
        'start_day': 'Friday',
        'start_time': '18:00',
        'end_day': 'Monday',
        'end_time': '00:00',
        'reminder_enabled': True,
        'reminder_minutes_before': 60,
        'end_announcement_enabled': True,
    },
    'voice': {
        'ignore_afk_channel': True,
        'ignore_deafened_users': True,
        'minimum_members_in_channel': 1,
        'excluded_channel_ids': [],
    },
    'channels': {
        'reward_channel': 0,
        'announcement_channel': 0,
    },
    'admin_role_id': 0,
    'timezone': 'Asia/Beirut',
    'leaderboard': {
        'enabled': True,
        'limit': 10,
        'excluded_user_ids': [],
    },
    'monthly': {
        'enabled': True,
        'winner_count': 1,
        'excluded_user_ids': [],
        'prizes': {
            '1': 'Custom Prize',
            '2': 'Second Place Prize',
            '3': 'Third Place Prize',
        },
    },
    'weekly_announcement': {
        'enabled': True,
        'day': 'Monday',
        'time': '12:00',
    },
    'templates': deepcopy(DEFAULT_TEMPLATES),
    'backups': {
        'enabled': True,
        'hour': 3,
        'interval_hours': 6,
        'retention_days': 14,
    },
}


def guild_dir(guild_id):
    return DATA_ROOT / str(guild_id)


def config_path(guild_id):
    return guild_dir(guild_id) / 'config.json'


def _deep_merge(defaults, custom):
    result = deepcopy(defaults)
    for key, value in (custom or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def validate_time(value):
    parts = str(value).split(':')
    if len(parts) != 2:
        raise ValueError('Time must use HH:MM format')
    hour, minute = map(int, parts)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError('Invalid time')
    return f'{hour:02d}:{minute:02d}'


def validate_day(value):
    name = str(value).strip().lower()
    if name not in WEEKDAYS:
        raise ValueError('Day must be Monday-Sunday')
    return name.title()


def setup_status(config):
    sections = config.get('setup', {}).get('sections', {})
    names = ('branding', 'rewards', 'event', 'voice', 'weekly', 'monthly', 'channels')
    completed = [name for name in names if bool(sections.get(name, False))]
    missing = [name for name in names if name not in completed]
    channels_ok = bool(int(config.get('channels', {}).get('reward_channel', 0))) and bool(
        int(config.get('channels', {}).get('announcement_channel', 0))
    )
    if not channels_ok and 'channels' not in missing:
        missing.append('channels')
        completed = [x for x in completed if x != 'channels']
    return {
        'done': len(completed),
        'total': len(names),
        'missing': missing,
        'complete': not missing,
    }


def mark_setup_section(config, section):
    config.setdefault('setup', {}).setdefault('sections', {})[section] = True
    status = setup_status(config)
    config['setup']['completed'] = bool(status['complete'])
    return status


def validate_config(config):
    ZoneInfo(config.get('timezone', 'Asia/Beirut'))

    event = config['event']
    event['start_day'] = validate_day(event['start_day'])
    event['end_day'] = validate_day(event['end_day'])
    event['start_time'] = validate_time(event['start_time'])
    event['end_time'] = validate_time(event['end_time'])
    event['multiplier'] = max(1, min(20, int(event['multiplier'])))
    event['reminder_enabled'] = bool(event.get('reminder_enabled', True))
    event['reminder_minutes_before'] = max(1, min(10080, int(event.get('reminder_minutes_before', 60))))
    event['end_announcement_enabled'] = bool(event.get('end_announcement_enabled', True))

    rewards = config['rewards']
    rewards['interval_minutes'] = max(1, min(1440, int(rewards['interval_minutes'])))
    rewards['points_per_interval'] = max(1, int(rewards['points_per_interval']))
    rewards['first_join_bonus'] = max(0, int(rewards['first_join_bonus']))

    voice = config['voice']
    voice['minimum_members_in_channel'] = max(1, min(99, int(voice['minimum_members_in_channel'])))
    voice['excluded_channel_ids'] = sorted({int(x) for x in voice.get('excluded_channel_ids', []) if int(x) > 0})

    config['leaderboard']['limit'] = max(1, min(50, int(config['leaderboard']['limit'])))
    config['leaderboard']['excluded_user_ids'] = sorted({int(x) for x in config['leaderboard'].get('excluded_user_ids', [])})
    monthly = config['monthly']
    # Backward compatibility with v9's single prize_text field.
    if 'prize_text' in monthly and not monthly.get('prizes'):
        monthly['prizes'] = {'1': str(monthly.get('prize_text') or 'Custom Prize'), '2': 'Second Place Prize', '3': 'Third Place Prize'}
    monthly['winner_count'] = 3 if int(monthly.get('winner_count', 1)) == 3 else 1
    prizes = monthly.setdefault('prizes', {})
    prizes['1'] = str(prizes.get('1', 'Custom Prize'))[:300] or 'Custom Prize'
    prizes['2'] = str(prizes.get('2', 'Second Place Prize'))[:300] or 'Second Place Prize'
    prizes['3'] = str(prizes.get('3', 'Third Place Prize'))[:300] or 'Third Place Prize'
    monthly.pop('prize_text', None)
    monthly['excluded_user_ids'] = sorted({int(x) for x in monthly.get('excluded_user_ids', [])})
    config['weekly_announcement']['day'] = validate_day(config['weekly_announcement']['day'])
    config['weekly_announcement']['time'] = validate_time(config['weekly_announcement']['time'])

    config['templates'] = _deep_merge(DEFAULT_TEMPLATES, config.get('templates', {}))
    for key, value in config['templates'].items():
        config['templates'][key] = str(value)[:1800]

    backups = config['backups']
    backups['enabled'] = bool(backups.get('enabled', True))
    backups['hour'] = max(0, min(23, int(backups.get('hour', 3))))
    backups['interval_hours'] = max(1, min(24, int(backups.get('interval_hours', 6))))
    backups['retention_days'] = max(1, min(365, int(backups.get('retention_days', 14))))

    config['setup']['completed'] = bool(setup_status(config)['complete'])
    return config


def load_config(guild_id):
    raw = load_json(config_path(guild_id), {})
    config = _deep_merge(DEFAULT_CONFIG, raw)
    validate_config(config)
    if raw != config:
        save_json(config_path(guild_id), config)
    return config


def save_config(guild_id, config):
    config = validate_config(_deep_merge(DEFAULT_CONFIG, config))
    # Flush activity under the old rules before changing reward/event settings.
    # Local import avoids a module-level circular dependency.
    try:
        from voice import flush_guild
        flush_guild(guild_id)
    except ImportError:
        pass
    save_json(config_path(guild_id), config)
    return config


def update_config(guild_id, mutator):
    config = load_config(guild_id)
    mutator(config)
    return save_config(guild_id, config)
