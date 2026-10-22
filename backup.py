import shutil
from datetime import datetime, timedelta
from uuid import uuid4
from pathlib import Path
from zoneinfo import ZoneInfo

from config_manager import PROJECT_ROOT, guild_dir
from logger import get_logger

log = get_logger('activitybot.backup')
BACKUP_ROOT = PROJECT_ROOT / 'backups'


def create_backup(guild_id, config, reason='manual'):
    source = guild_dir(guild_id)
    if not source.exists():
        return None
    now = datetime.now(ZoneInfo(config['timezone']))
    stamp = now.strftime('%Y-%m-%d_%H-%M-%S')
    target = BACKUP_ROOT / str(guild_id) / f'{stamp}_{reason}_{uuid4().hex[:8]}'
    target.mkdir(parents=True, exist_ok=False)
    for filename in ('config.json', 'users.json', 'winners.json', 'scheduler.json'):
        src = source / filename
        if src.exists():
            shutil.copy2(src, target / filename)
    _cleanup(guild_id, int(config.get('backups', {}).get('retention_days', 14)), now)
    log.info('Backup created | guild_id=%s reason=%s path=%s', guild_id, reason, target)
    return target


def _cleanup(guild_id, retention_days, now):
    root = BACKUP_ROOT / str(guild_id)
    if not root.exists():
        return
    cutoff = now - timedelta(days=retention_days)
    for folder in root.iterdir():
        if not folder.is_dir() or folder.is_symlink():
            continue
        # Recovery safety copies must survive automated retention cleanup.
        if 'before_restore_DO_NOT_DELETE' in folder.name:
            continue
        try:
            modified = datetime.fromtimestamp(folder.stat().st_mtime, tz=now.tzinfo)
            if modified < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            continue


def restore_backup(guild_id, backup_path):
    """Explicit validated restore with staged writes and rollback on I/O failure.

    Never deletes the source backup or unrelated live files. For strict power-loss
    atomicity across multiple JSON files, an offline database is required.
    """
    import json
    import os
    import tempfile

    source = Path(backup_path).resolve()
    root = (BACKUP_ROOT.resolve() / str(guild_id))
    if Path(backup_path).is_symlink() or not source.is_dir() or source.parent != root:
        raise ValueError('Backup path must be a backup directory belonging to this server.')
    names = ('config.json', 'users.json', 'winners.json', 'scheduler.json')
    payloads = {}
    for name in names:
        candidate = source / name
        if candidate.is_symlink():
            raise ValueError(f'Unsafe backup file: {name}')
        if candidate.exists():
            with candidate.open('r', encoding='utf-8') as handle:
                payloads[name] = json.load(handle)
    if 'config.json' not in payloads or 'users.json' not in payloads:
        raise ValueError('Backup is incomplete: config.json and users.json are required.')
    if any(not isinstance(value, dict) for value in payloads.values()):
        raise ValueError('Backup JSON root must be an object for every file.')

    destination = guild_dir(guild_id)
    destination.mkdir(parents=True, exist_ok=True)
    # Keep original bytes, including encoding and whitespace, for exact rollback.
    originals = {name: (destination / name).read_bytes() if (destination / name).exists() else None
                 for name in payloads}
    staged = {}

    def stage_bytes(name, data):
        fd, temp_name = tempfile.mkstemp(prefix=f'.{name}.restore.', suffix='.tmp', dir=destination)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            return Path(temp_name)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

    replaced = []
    try:
        for name, payload in payloads.items():
            encoded = (json.dumps(payload, indent=4, ensure_ascii=False) + '\n').encode('utf-8')
            staged[name] = stage_bytes(name, encoded)
        for name in payloads:
            os.replace(staged.pop(name), destination / name)
            replaced.append(name)
    except Exception:
        rollback_errors = []
        for name in reversed(replaced):
            try:
                original = originals[name]
                if original is None:
                    (destination / name).unlink(missing_ok=True)
                else:
                    rollback_file = stage_bytes(name, original)
                    try:
                        os.replace(rollback_file, destination / name)
                    finally:
                        rollback_file.unlink(missing_ok=True)
            except Exception as error:
                rollback_errors.append(f'{name}: {error}')
        if rollback_errors:
            raise RuntimeError('Restore failed and rollback was incomplete: ' + '; '.join(rollback_errors))
        raise
    finally:
        for staged_file in staged.values():
            staged_file.unlink(missing_ok=True)
    log.warning('Backup restored | guild_id=%s files=%s source=%s', guild_id, ','.join(replaced), source.name)
    return replaced
