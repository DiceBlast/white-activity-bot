from datetime import datetime
from functools import wraps
from threading import Lock, RLock
from pathlib import Path
from zoneinfo import ZoneInfo

from config_manager import guild_dir
from storage import load_json, save_json


# Serialize complete read-modify-write operations within this Python process.
# Each server has an independent reentrant lock; nested database calls are safe.
# This does not coordinate separate running bot processes or direct file edits.
_GUILD_LOCKS = {}
_GUILD_LOCKS_GUARD = Lock()


def _guild_lock(guild_id):
    key = str(guild_id)
    with _GUILD_LOCKS_GUARD:
        if key not in _GUILD_LOCKS:
            _GUILD_LOCKS[key] = RLock()
        return _GUILD_LOCKS[key]


def _serialized(func):
    @wraps(func)
    def guarded(guild_id, *args, **kwargs):
        with _guild_lock(guild_id):
            return func(guild_id, *args, **kwargs)
    return guarded


def _users_path(guild_id):
    return guild_dir(guild_id) / 'users.json'


def _winners_path(guild_id):
    return guild_dir(guild_id) / 'winners.json'


def _scheduler_path(guild_id):
    return guild_dir(guild_id) / 'scheduler.json'


def _now_text(config):
    return datetime.now(ZoneInfo(config['timezone'])).strftime('%Y-%m-%d %H:%M:%S')


def _period(config):
    return datetime.now(ZoneInfo(config['timezone'])).strftime('%Y-%m')


@_serialized
def load_users(guild_id):
    return load_json(_users_path(guild_id), {})


@_serialized
def save_users(guild_id, users):
    save_json(_users_path(guild_id), users)


def _migrate_user(user, config):
    """Add v2 fields without discarding existing scores or lifetime statistics."""
    user.setdefault('voice_seconds_monthly', 0.0)
    user.setdefault('normal_credit_seconds', 0.0)
    user.setdefault('event_credit_seconds', 0.0)
    if 'reward_credit_buckets' not in user:
        buckets = []
        normal = max(0.0, float(user.get('normal_credit_seconds', 0.0)))
        event = max(0.0, float(user.get('event_credit_seconds', 0.0)))
        if normal:
            buckets.append({'seconds': normal, 'multiplier': 1})
        if event:
            # Legacy event credits did not store their multiplier. Preserve them
            # using the configured multiplier at upgrade time as the best available estimate.
            buckets.append({'seconds': event, 'multiplier': max(1, int(config['event'].get('multiplier', 1)))})
        user['reward_credit_buckets'] = buckets
    return user


@_serialized
def ensure_user(guild_id, user_id, name, config):
    users = load_users(guild_id)
    key = str(user_id)
    if key not in users:
        users[key] = {
            'discord_id': key,
            'name': str(name),
            'score': 0,
            'voice_seconds': 0.0,
            'voice_seconds_monthly': 0.0,
            'normal_credit_seconds': 0.0,
            'event_credit_seconds': 0.0,
            'reward_credit_buckets': [],
            'pending_normal_points': 0,
            'pending_event_points': 0,
            'first_bonus_claimed': False,
            'score_period': _period(config),
            'first_seen': _now_text(config),
            'last_seen': _now_text(config),
        }
    else:
        _migrate_user(users[key], config)
        users[key]['name'] = str(name)
        users[key]['last_seen'] = _now_text(config)
    save_users(guild_id, users)
    return users[key]


@_serialized
def give_first_bonus(guild_id, user_id, config):
    users = load_users(guild_id)
    key = str(user_id)
    user = users.get(key)
    if user is None or user.get('first_bonus_claimed'):
        return 0
    if not config['rewards'].get('first_join_bonus_enabled', True):
        user['first_bonus_claimed'] = True
        save_users(guild_id, users)
        return 0
    points = int(config['rewards'].get('first_join_bonus', 0))
    user['score'] = int(user.get('score', 0)) + points
    user['first_bonus_claimed'] = True
    user['last_seen'] = _now_text(config)
    save_users(guild_id, users)
    return points


@_serialized
def record_voice_segments(guild_id, user_id, segments, config):
    users = load_users(guild_id)
    key = str(user_id)
    user = users.get(key)
    if user is None:
        return False
    _migrate_user(user, config)
    raw = 0.0
    normal = 0.0
    event = 0.0
    buckets = user['reward_credit_buckets']
    for seconds, multiplier in segments:
        seconds = max(0.0, float(seconds))
        multiplier = max(1, int(multiplier))
        if seconds <= 0:
            continue
        raw += seconds
        if multiplier > 1:
            event += seconds
        else:
            normal += seconds
        # Store the multiplier alongside the activity. Later config changes
        # must never reprice already-recorded activity.
        if buckets and int(buckets[-1].get('multiplier', 1)) == multiplier:
            buckets[-1]['seconds'] = float(buckets[-1].get('seconds', 0.0)) + seconds
        else:
            buckets.append({'seconds': seconds, 'multiplier': multiplier})
    user['voice_seconds'] = float(user.get('voice_seconds', 0)) + raw
    user['voice_seconds_monthly'] = float(user.get('voice_seconds_monthly', 0)) + raw
    user['normal_credit_seconds'] = float(user.get('normal_credit_seconds', 0)) + normal
    user['event_credit_seconds'] = float(user.get('event_credit_seconds', 0)) + event
    user['last_seen'] = _now_text(config)
    save_users(guild_id, users)
    return True


def reward_credit_weighted_seconds(user):
    """Return remaining reward progress in multiplier-weighted seconds."""
    return sum(
        max(0.0, float(bucket.get('seconds', 0.0))) * max(1, int(bucket.get('multiplier', 1)))
        for bucket in user.get('reward_credit_buckets', [])
    )


@_serialized
def claim_available_rewards(guild_id, config):
    users = load_users(guild_id)
    interval = max(1, int(config['rewards']['interval_minutes']) * 60)
    base_points = max(0, int(config['rewards']['points_per_interval']))
    claims = []

    for key, user in users.items():
        _migrate_user(user, config)
        buckets = user['reward_credit_buckets']
        weighted_seconds = reward_credit_weighted_seconds(user)
        units = int(weighted_seconds // interval)
        if units <= 0 or base_points <= 0:
            continue

        weighted_to_consume = float(units * interval)
        remaining_weight = weighted_to_consume
        consumed_normal_raw = 0.0
        consumed_event_raw = 0.0
        new_buckets = []
        for bucket in buckets:
            seconds = max(0.0, float(bucket.get('seconds', 0.0)))
            multiplier = max(1, int(bucket.get('multiplier', 1)))
            bucket_weight = seconds * multiplier
            take_weight = min(bucket_weight, remaining_weight)
            take_raw = take_weight / multiplier
            if multiplier > 1:
                consumed_event_raw += take_raw
            else:
                consumed_normal_raw += take_raw
            left_raw = max(0.0, seconds - take_raw)
            remaining_weight -= take_weight
            if left_raw > 1e-7:
                new_buckets.append({'seconds': left_raw, 'multiplier': multiplier})
        user['reward_credit_buckets'] = new_buckets

        total_points = units * base_points
        raw_consumed = consumed_normal_raw + consumed_event_raw
        if raw_consumed > 0:
            # Categorize the notification by raw activity share; the multiplier
            # has already been applied to the total reward-equivalent credit.
            event_points = int(round(total_points * consumed_event_raw / raw_consumed))
        else:
            event_points = 0
        event_points = max(0, min(total_points, event_points))
        normal_points = total_points - event_points

        user['normal_credit_seconds'] = max(0.0, float(user.get('normal_credit_seconds', 0.0)) - consumed_normal_raw)
        user['event_credit_seconds'] = max(0.0, float(user.get('event_credit_seconds', 0.0)) - consumed_event_raw)
        user['score'] = int(user.get('score', 0)) + total_points
        user['pending_normal_points'] = int(user.get('pending_normal_points', 0)) + normal_points
        user['pending_event_points'] = int(user.get('pending_event_points', 0)) + event_points
        claims.append({
            'user_id': key,
            'name': user.get('name', key),
            'points': total_points,
            'normal_points': normal_points,
            'event_points': event_points,
            'score': user['score'],
        })

    # Persist v2 migration even if no complete interval was claimed.
    save_users(guild_id, users)
    return claims


@_serialized
def pending_notifications(guild_id):
    return [
        dict(user)
        for user in load_users(guild_id).values()
        if int(user.get('pending_normal_points', 0)) + int(user.get('pending_event_points', 0)) > 0
    ]


@_serialized
def acknowledge_notification(guild_id, user_id, normal_points, event_points):
    users = load_users(guild_id)
    user = users.get(str(user_id))
    if user is None:
        return False
    user['pending_normal_points'] = max(0, int(user.get('pending_normal_points', 0)) - int(normal_points))
    user['pending_event_points'] = max(0, int(user.get('pending_event_points', 0)) - int(event_points))
    save_users(guild_id, users)
    return True



@_serialized
def add_score(guild_id, user_id, amount, name, config):
    """Add admin-granted points to a user's score."""
    amount = int(amount)
    if amount <= 0:
        return None

    ensure_user(guild_id, user_id, name, config)
    users = load_users(guild_id)
    user = users.get(str(user_id))
    if user is None:
        return None

    user['score'] = int(user.get('score', 0)) + amount
    user['last_seen'] = _now_text(config)
    save_users(guild_id, users)
    return int(user['score'])


@_serialized
def remove_score(guild_id, user_id, amount, config):
    """Remove admin-selected points without allowing a negative score."""
    amount = int(amount)
    if amount <= 0:
        return None

    users = load_users(guild_id)
    user = users.get(str(user_id))
    if user is None:
        return None

    user['score'] = max(0, int(user.get('score', 0)) - amount)
    user['last_seen'] = _now_text(config)
    save_users(guild_id, users)
    return int(user['score'])

@_serialized
def leaderboard(guild_id, config):
    excluded = {str(x) for x in config['leaderboard'].get('excluded_user_ids', [])}
    users = [u for uid, u in load_users(guild_id).items() if uid not in excluded]
    users.sort(key=lambda u: (-int(u.get('score', 0)), -float(u.get('voice_seconds', 0))))
    return users[: int(config['leaderboard']['limit'])]


@_serialized
def monthly_winners(guild_id, config):
    excluded = {str(x) for x in config['monthly'].get('excluded_user_ids', [])}
    candidates = [u for uid, u in load_users(guild_id).items() if uid not in excluded and int(u.get('score', 0)) > 0]
    candidates.sort(key=lambda u: (-int(u.get('score', 0)), -float(u.get('voice_seconds_monthly', 0)), str(u.get('discord_id', ''))))
    count = 3 if int(config['monthly'].get('winner_count', 1)) == 3 else 1
    return [dict(u) for u in candidates[:count]]


@_serialized
def monthly_winner(guild_id, config):
    winners = monthly_winners(guild_id, config)
    return winners[0] if winners else None

@_serialized
def load_winners(guild_id):
    return load_json(_winners_path(guild_id), {})


@_serialized
def save_monthly_winners(guild_id, month_key, monthly_winner_rows, prizes):
    history = load_winners(guild_id)
    if month_key not in history:
        saved = []
        for index, winner in enumerate(monthly_winner_rows, start=1):
            saved.append({
                'position': index,
                'discord_id': winner['discord_id'],
                'name': winner['name'],
                'score': int(winner['score']),
                'prize': str(prizes.get(str(index), '')),
            })
        history[month_key] = saved
        save_json(_winners_path(guild_id), history)
    return history[month_key]


@_serialized
def save_monthly_winner(guild_id, month_key, winner, prize_text):
    # Compatibility helper for older callers.
    return save_monthly_winners(guild_id, month_key, [winner], {'1': prize_text})[0]

@_serialized
def reset_monthly(guild_id, new_period):
    users = load_users(guild_id)
    for user in users.values():
        user['score'] = 0
        # Keep earned-but-unclaimed progress across month boundaries.
        # Claim eligible previous-month rewards before calling this function.
        user['voice_seconds_monthly'] = 0.0
        # Pending notifications are already-earned points, not new-month scores.
        # Keep them so Discord delivery failures do not erase messages.
        user['score_period'] = new_period
    save_users(guild_id, users)


@_serialized
def load_scheduler(guild_id):
    return load_json(_scheduler_path(guild_id), {})


@_serialized
def save_scheduler(guild_id, state):
    save_json(_scheduler_path(guild_id), state)
