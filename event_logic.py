from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config_manager import WEEKDAYS


def _minutes(value):
    hour, minute = map(int, value.split(':'))
    return hour * 60 + minute


def _week_position(moment):
    return moment.weekday() * 1440 + moment.hour * 60 + moment.minute + moment.second / 60


def is_event_active(config, moment=None):
    event = config['event']
    if not event.get('enabled', True) or int(event.get('multiplier', 1)) <= 1:
        return False

    tz = ZoneInfo(config['timezone'])
    moment = moment or datetime.now(tz)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=tz)
    else:
        moment = moment.astimezone(tz)

    start = WEEKDAYS[event['start_day'].lower()] * 1440 + _minutes(event['start_time'])
    end = WEEKDAYS[event['end_day'].lower()] * 1440 + _minutes(event['end_time'])
    current = _week_position(moment)

    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def current_multiplier(config, moment=None):
    return int(config['event']['multiplier']) if is_event_active(config, moment) else 1


def _next_boundary(moment, config):
    tz = ZoneInfo(config['timezone'])
    event = config['event']
    candidates = []
    for day_name, time_value in (
        (event['start_day'], event['start_time']),
        (event['end_day'], event['end_time']),
    ):
        target_weekday = WEEKDAYS[day_name.lower()]
        hour, minute = map(int, time_value.split(':'))
        for offset in range(0, 9):
            date = (moment + timedelta(days=offset)).date()
            if date.weekday() != target_weekday:
                continue
            candidate = datetime(date.year, date.month, date.day, hour, minute, tzinfo=tz)
            if candidate > moment:
                candidates.append(candidate)
                break
    return min(candidates) if candidates else moment + timedelta(days=7)


def split_interval(start_timestamp, end_timestamp, config):
    if end_timestamp <= start_timestamp:
        return []
    if not config['event'].get('enabled', True) or int(config['event'].get('multiplier', 1)) <= 1:
        return [(end_timestamp - start_timestamp, 1)]

    tz = ZoneInfo(config['timezone'])
    start = datetime.fromtimestamp(float(start_timestamp), tz)
    end = datetime.fromtimestamp(float(end_timestamp), tz)
    result = []
    cursor = start
    while cursor < end:
        boundary = _next_boundary(cursor, config)
        segment_end = min(boundary, end)
        seconds = segment_end.timestamp() - cursor.timestamp()
        result.append((seconds, current_multiplier(config, cursor)))
        cursor = segment_end
    return result
