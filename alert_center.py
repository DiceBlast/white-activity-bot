"""Read-only operational alerts. Never accesses or mutates guild scores/settings."""
import json
import os
import time
from pathlib import Path

ALERTS_NAME = 'monitor_alerts.jsonl'
SETTINGS_NAME = '.monitor_alert_settings.json'
DEFAULTS = {'recovery': True, 'reward_failures': True, 'discord': True, 'resources': True}
MAX_BYTES = 512 * 1024

def alert_path(root):
    return Path(root) / 'logs' / ALERTS_NAME

def settings(root):
    try:
        raw = json.loads((Path(root) / SETTINGS_NAME).read_text(encoding='utf-8'))
        return {k: bool(raw.get(k, v)) for k, v in DEFAULTS.items()}
    except (OSError, ValueError, TypeError, AttributeError):
        return dict(DEFAULTS)

def save_settings(root, values):
    path = Path(root) / SETTINGS_NAME
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps({k: bool(values.get(k, v)) for k,v in DEFAULTS.items()}, indent=2), encoding='utf-8')
    os.replace(tmp, path)

def _write_alert(root, category, message, severity='warning'):
    if category not in DEFAULTS or not settings(root)[category]:
        return False
    path = alert_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    # Bound operational log size without touching any existing Discord data.
    if path.exists() and path.stat().st_size > MAX_BYTES:
        with path.open('rb') as handle:
            handle.seek(-MAX_BYTES // 2, 2)
            handle.readline()
            tail = handle.read()
        tmp = path.with_suffix('.rotating')
        tmp.write_bytes(tail)
        os.replace(tmp, path)
    entry = {'time': time.time(), 'category': category, 'severity': severity, 'message': str(message)[:400]}
    with path.open('a', encoding='utf-8') as out:
        out.write(json.dumps(entry, ensure_ascii=False) + '\n')
    return True

def recent_alerts(root, limit=120):
    path = alert_path(root)
    try:
        with path.open('rb') as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - MAX_BYTES))
            if size > MAX_BYTES:
                handle.readline()
            lines = handle.read().decode('utf-8', errors='replace').splitlines()
    except OSError:
        return []
    result = []
    for line in lines[-max(limit * 3, limit):]:
        try:
            entry = json.loads(line)
            if isinstance(entry, dict) and 'message' in entry:
                result.append(entry)
        except ValueError:
            continue
    return result[-limit:]

class FailureWatcher:
    """Detect new failures from log tail; skips historical content at startup."""
    def __init__(self, path):
        self.path = Path(path)
        self.offset = None
        self.file_key = None
        self.buffer = b''

    def poll(self):
        try:
            st = self.path.stat()
            key = (st.st_dev, st.st_ino)
            if self.offset is None:
                self.offset, self.file_key = st.st_size, key
                return []
            if key != self.file_key or st.st_size < self.offset:
                self.offset, self.file_key, self.buffer = 0, key, b''
            with self.path.open('rb') as f:
                f.seek(self.offset)
                data = f.read(128 * 1024)
                self.offset += len(data)
            self.buffer += data
            lines = self.buffer.split(b'\n')
            self.buffer = lines.pop()[-2048:]
            results = []
            for raw in lines:
                line = raw.decode('utf-8', errors='replace')
                lower = line.lower()
                if 'reward' in lower and ('failed' in lower or 'exception' in lower):
                    results.append(('reward_failures', line[-350:]))
                elif ('discord' in lower or 'gateway' in lower or 'websocket' in lower) and ('disconnect' in lower or 'connection lost' in lower or 'reconnect' in lower):
                    results.append(('discord', line[-350:]))
            return results
        except OSError:
            return []


def add_alert(root, category, message, severity='warning'):
    try:
        return _write_alert(root, category, message, severity)
    except (OSError, ValueError, TypeError):
        return False
