"""Best-effort, bounded monitoring history (not reward or score data)."""
import json
import time
from pathlib import Path

MAX_BYTES = 1_500_000
SAMPLE_SECONDS = 300

def record_snapshot(root, payload):
    """Append periodic voice/member counts. Never alter user or guild databases."""
    path = Path(root) / 'logs' / 'analytics_history.jsonl'
    guilds = payload.get('guilds', {})
    if not isinstance(guilds, dict):
        return
    now = float(payload.get('timestamp', time.time()))
    path.parent.mkdir(parents=True, exist_ok=True)
    # Rate limit survives a process restart. A fresh file may start immediately.
    try:
        if now - path.stat().st_mtime < SAMPLE_SECONDS:
            return
    except OSError:
        pass
    sample = {'timestamp': now, 'guilds': {
        str(gid): {'voice': max(0, int(g.get('voice_users') or 0)),
                   'members': max(0, int(g.get('members') or 0))}
        for gid, g in guilds.items() if isinstance(g, dict)
    }}
    if path.exists() and path.stat().st_size > MAX_BYTES:
        # Keep the recent half; log is independent from earned points/history.
        lines = path.read_bytes().splitlines(keepends=True)
        recent = b''.join(lines[-2000:])
        temp = path.with_suffix('.tmp')
        temp.write_bytes(recent)
        temp.replace(path)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(sample, separators=(',', ':')) + '\n')

def history_for_guild(gid, root, limit=288):
    path = Path(root) / 'logs' / 'analytics_history.jsonl'
    try:
        with path.open('rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - MAX_BYTES))
            if size > MAX_BYTES:
                f.readline()
            lines = f.read().splitlines()
    except OSError:
        return []
    values = []
    for line in lines:
        try:
            item = json.loads(line)
            data = item.get('guilds', {}).get(str(gid))
            if isinstance(data, dict):
                values.append((float(item['timestamp']), int(data['voice']), int(data['members'])))
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    return values[-limit:]
