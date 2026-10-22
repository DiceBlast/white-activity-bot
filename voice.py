import time

from logger import get_logger

log = get_logger('activitybot.voice')

from config_manager import load_config
from database import ensure_user, give_first_bonus, record_voice_segments
from event_logic import split_interval

active_sessions = {}
# Only lives in process memory: segments whose persistence failed on session close.
# No extra JSON files or changes to existing records are required.
pending_intervals = []


def _commit_interval(member, start, end):
    if end <= start:
        return True
    config = load_config(member.guild.id)
    segments = split_interval(start, end, config)
    return bool(record_voice_segments(member.guild.id, member.id, segments, config))


def retry_pending_intervals():
    """Retry closed intervals without inventing activity during an outage."""
    for item in list(pending_intervals):
        member, start, end = item
        try:
            if _commit_interval(member, start, end):
                pending_intervals.remove(item)
        except Exception:
            log.exception('Pending voice save failed | guild_id=%s user_id=%s',
                          member.guild.id, member.id)
    return len(pending_intervals)


def _key(member):
    return (member.guild.id, member.id)


def _human_count(channel):
    return sum(1 for member in channel.members if not member.bot)


def can_earn(member, state, config):
    if member.bot or state is None or state.channel is None:
        return False
    voice_cfg = config['voice']
    excluded = {int(x) for x in voice_cfg.get('excluded_channel_ids', [])}
    if state.channel.id in excluded:
        return False
    if voice_cfg.get('ignore_afk_channel', True):
        afk = member.guild.afk_channel
        if afk is not None and state.channel.id == afk.id:
            return False
    if voice_cfg.get('ignore_deafened_users', True) and (state.self_deaf or state.deaf):
        return False
    if _human_count(state.channel) < int(voice_cfg.get('minimum_members_in_channel', 1)):
        return False
    return True


def start_session(member, channel, start_time=None):
    key = _key(member)
    if key in active_sessions:
        return False
    timestamp = time.time() if start_time is None else float(start_time)
    active_sessions[key] = {
        'member': member,
        'start': timestamp,
        'last_tick': timestamp,
        'channel_id': channel.id,
        'channel_name': channel.name,
    }
    return True


def flush_session(member, current_time=None):
    key = _key(member)
    session = active_sessions.get(key)
    if session is None:
        return 0.0
    end = time.time() if current_time is None else float(current_time)
    start = float(session.get('last_tick', end))
    if end <= start:
        return 0.0
    try:
        saved = _commit_interval(member, start, end)
    except Exception:
        log.exception('Voice checkpoint failed | guild_id=%s user_id=%s',
                      member.guild.id, member.id)
        return 0.0
    if saved:
        session['last_tick'] = end
        return end - start
    log.error('Voice checkpoint rejected | guild_id=%s user_id=%s',
              member.guild.id, member.id)
    return 0.0


def stop_session(member, current_time=None):
    key = _key(member)
    if key not in active_sessions:
        return
    session = active_sessions[key]
    end = time.time() if current_time is None else float(current_time)
    start = float(session['last_tick'])
    if end > start and flush_session(member, current_time=end) < end - start - 0.000001:
        # Keep the exact known end time; a subsequent retry must not credit
        # disconnected/offline time, nor silently drop a failed checkpoint.
        pending_intervals.append((member, start, end))
        log.warning('Voice checkpoint queued in memory | guild_id=%s user_id=%s',
                    member.guild.id, member.id)
    active_sessions.pop(key, None)


def reconcile_channel(channel, current_time=None):
    """Re-evaluate every human member when channel eligibility may have changed.

    Returns first-bonus awards for other members who become eligible because the
    channel's human-member count changed.
    """
    awards = []
    if channel is None:
        return awards
    timestamp = time.time() if current_time is None else float(current_time)
    for member in list(channel.members):
        if member.bot:
            continue
        config = load_config(member.guild.id)
        ensure_user(member.guild.id, member.id, member.display_name, config)
        key = _key(member)
        eligible = can_earn(member, member.voice, config)
        if key in active_sessions and not eligible:
            stop_session(member, current_time=timestamp)
        elif key not in active_sessions and eligible:
            start_session(member, member.voice.channel, start_time=timestamp)
            bonus = give_first_bonus(member.guild.id, member.id, config)
            if bonus > 0:
                awards.append((member, bonus))
        elif key in active_sessions and eligible:
            session = active_sessions[key]
            session['channel_id'] = member.voice.channel.id
            session['channel_name'] = member.voice.channel.name
    return awards


def process_voice_update(member, before, after):
    if member.bot:
        return {'first_bonus': 0}
    config = load_config(member.guild.id)
    ensure_user(member.guild.id, member.id, member.display_name, config)
    key = _key(member)
    had_session = key in active_sessions
    before_channel = before.channel
    after_channel = after.channel
    moved_channels = (
        before_channel is not None
        and after_channel is not None
        and before_channel.id != after_channel.id
    )

    # Close the previous channel's accounting segment before changing channels.
    if had_session and (after_channel is None or moved_channels):
        stop_session(member)
        had_session = False

    should_earn = can_earn(member, after, config)
    if should_earn and key not in active_sessions:
        start_session(member, after_channel)
    elif not should_earn and key in active_sessions:
        stop_session(member)

    # Award the one-time bonus only on transition into eligible activity, not
    # merely on joining an excluded/AFK/deafened/under-populated channel.
    bonus = 0
    if should_earn and not had_session:
        bonus = give_first_bonus(member.guild.id, member.id, config)

    # Channel membership changes can make other members eligible or ineligible.
    now = time.time()
    bonus_awards = []
    if before_channel is not None and (after_channel is None or before_channel.id != after_channel.id):
        bonus_awards.extend(reconcile_channel(before_channel, current_time=now))
    if after_channel is not None:
        bonus_awards.extend(reconcile_channel(after_channel, current_time=now))

    return {'first_bonus': bonus, 'bonus_awards': bonus_awards}


def flush_all(current_time=None):
    retry_pending_intervals()
    for session in list(active_sessions.values()):
        flush_session(session['member'], current_time=current_time)


def flush_guild(guild_id, current_time=None):
    """Flush a guild's sessions before its reward/event configuration changes."""
    for session in list(active_sessions.values()):
        member = session['member']
        if member.guild.id == int(guild_id):
            flush_session(member, current_time=current_time)


def pause_all():
    # Close all known intervals at disconnect. Failed writes remain in memory
    # with their original end timestamp, rather than being erased.
    end = time.time()
    count = len(active_sessions)
    for session in list(active_sessions.values()):
        stop_session(session['member'], current_time=end)
    retry_pending_intervals()
    return count


def restore_active_sessions(bot):
    now = time.time()
    restored = 0
    # on_ready can run again while sessions exist. Close them, preserving any
    # failed checkpoints, before re-starting from the current observed state.
    for session in list(active_sessions.values()):
        stop_session(session['member'], current_time=now)
    retry_pending_intervals()
    for guild in bot.guilds:
        config = load_config(guild.id)
        for channel in guild.voice_channels:
            for member in channel.members:
                ensure_user(guild.id, member.id, member.display_name, config)
                if can_earn(member, member.voice, config):
                    if start_session(member, channel, start_time=now):
                        restored += 1
    return restored


def flush_guild_through(guild_id, boundary_time):
    """Persist eligible voice seconds strictly before a month boundary.

    Return False if any active session cannot be committed. The caller must
    abort the reset in that case, rather than silently discard voice activity.
    Sessions begun after the boundary are left for the new month.
    """
    retry_pending_intervals()
    # Never reset a month while a known closed interval for that guild
    # remains uncommitted. Retried intervals retain their original timestamps.
    if any(member.guild.id == int(guild_id) and start < boundary_time
           for member, start, end in pending_intervals):
        return False
    for session in list(active_sessions.values()):
        member = session['member']
        if member.guild.id != int(guild_id):
            continue
        if float(session['last_tick']) >= boundary_time:
            continue
        expected = boundary_time - float(session['last_tick'])
        if flush_session(member, current_time=boundary_time) < expected - 0.000001:
            return False
    return True
