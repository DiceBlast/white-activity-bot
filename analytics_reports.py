"""Read-only time-series report generation from independent monitoring snapshots."""
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path

RANGE_SECONDS = {'24 hours': 86400, '7 days': 604800, '30 days': 2592000, 'All history': None}
MAX_READ_BYTES = 1_500_000


def load_samples(root, guild_id, period='24 hours', now=None):
    """Read bounded monitoring samples, never touching points or reward databases."""
    if period not in RANGE_SECONDS:
        raise ValueError('Unknown report period')
    path = Path(root) / 'logs' / 'analytics_history.jsonl'
    try:
        with path.open('rb') as file:
            file.seek(0, 2)
            length = file.tell()
            file.seek(max(0, length - MAX_READ_BYTES))
            if length > MAX_READ_BYTES:
                file.readline()
            lines = file.read().splitlines()
    except OSError:
        return []
    cutoff = (time.time() if now is None else now) - RANGE_SECONDS[period] if RANGE_SECONDS[period] else float('-inf')
    rows = []
    for line in lines:
        try:
            record = json.loads(line)
            stamp = float(record['timestamp'])
            data = record['guilds'][str(guild_id)]
            voice = max(0, int(data['voice']))
            members = max(0, int(data['members']))
            if stamp >= cutoff:
                rows.append((stamp, voice, members))
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            continue
    return sorted(rows)


def summarize(rows):
    return {'samples': len(rows),
            'avg_voice': sum(x[1] for x in rows) / len(rows) if rows else 0,
            'peak_voice': max((x[1] for x in rows), default=0),
            'latest_members': rows[-1][2] if rows else 0,
            'start_members': rows[0][2] if rows else 0}


def export_csv(path, rows, guild_name, guild_id, period):
    """CSV uses formula-safe text cells to prevent spreadsheet injection."""
    def safe(value):
        value = str(value)
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
    stats = summarize(rows)

    def display_time(stamp):
        """Format local time, retaining exports on hosts without a usable local zone."""
        try:
            return datetime.fromtimestamp(stamp).astimezone().isoformat(timespec='seconds')
        except (OSError, OverflowError, ValueError):
            return datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec='seconds')

    # Exclusive create: never overwrite an existing exported report.
    with Path(path).open('x', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['Activity Rewards - Guild Analytics Report'])
        writer.writerow(['Guild', safe(guild_name)])
        writer.writerow(['Guild ID', safe(guild_id)])
        writer.writerow(['Period', period])
        writer.writerow(['Samples', stats['samples']])
        writer.writerow(['Average voice occupancy', round(stats['avg_voice'], 2)])
        writer.writerow(['Peak voice occupancy', stats['peak_voice']])
        writer.writerow(['First member count', stats['start_members']])
        writer.writerow(['Latest member count', stats['latest_members']])
        writer.writerow([])
        writer.writerow(['Local time', 'Unix timestamp', 'Voice users', 'Members'])
        for stamp, voice, members in rows:
            writer.writerow([display_time(stamp), int(stamp), voice, members])
