"""Crash-resistant JSON persistence with automatic recovery from the last good copy."""
import json
import os
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path

_LOCK = threading.RLock()


def _backup_path(path):
    return path.with_suffix(path.suffix + '.bak')


def load_json(path, default):
    path = Path(path)
    with _LOCK:
        if not path.exists():
            save_json(path, default)
            return json.loads(json.dumps(default))
        try:
            with path.open('r', encoding='utf-8') as handle:
                return json.load(handle)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as primary_error:
            backup = _backup_path(path)
            if backup.exists():
                try:
                    with backup.open('r', encoding='utf-8') as handle:
                        recovered = json.load(handle)
                    # Preserve the damaged file for later diagnosis.
                    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    corrupt_path = path.with_name(f'{path.name}.corrupt_{stamp}')
                    try:
                        shutil.copy2(path, corrupt_path)
                    except OSError:
                        pass
                    save_json(path, recovered)
                    return recovered
                except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                    pass
            raise RuntimeError(
                f'Could not read {path}; no valid recovery copy was available. '
                'The file was not overwritten.'
            ) from primary_error


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        fd, temp_name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(value, handle, indent=4, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            # Keep the previous valid version before atomically replacing it.
            if path.exists():
                try:
                    with path.open('r', encoding='utf-8') as current:
                        json.load(current)
                    backup_tmp = _backup_path(path).with_suffix(_backup_path(path).suffix + '.tmp')
                    shutil.copy2(path, backup_tmp)
                    os.replace(backup_tmp, _backup_path(path))
                except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                    # Never replace a known-good recovery copy with an invalid file.
                    pass
            os.replace(temp_name, path)
            # Best-effort directory flush improves durability on POSIX.
            try:
                dir_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
