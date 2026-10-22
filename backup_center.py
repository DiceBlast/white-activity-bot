"""Non-destructive per-guild backup management for the desktop UI.

Existing scheduler backups are untouched. Restoration is intentionally offline-only.
"""
import json
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

FILES = ('config.json', 'users.json', 'winners.json', 'scheduler.json')


def guild_ids(root):
    folder = Path(root) / 'data' / 'guilds'
    return sorted((p.name for p in folder.iterdir() if p.is_dir() and p.name.isdecimal()), key=int) if folder.exists() else []


def _guild_id(guild_id):
    value = str(guild_id)
    if not value.isdecimal():
        raise ValueError('Invalid guild ID')
    return value


def backup_folders(root, guild_id):
    parent = Path(root) / 'backups' / _guild_id(guild_id)
    return sorted((p for p in parent.iterdir() if p.is_dir() and not p.is_symlink()), key=lambda p: p.name, reverse=True) if parent.exists() else []


def validate(folder):
    folder = Path(folder)
    files = []
    for name in FILES:
        p = folder / name
        if p.exists():
            if not p.is_file() or p.is_symlink():
                raise ValueError(f'Unsafe backup file: {name}')
            with p.open('r', encoding='utf-8') as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                raise ValueError(f'Invalid backup data: {name}')
            files.append(name)
    if not {'config.json', 'users.json'}.issubset(files):
        raise ValueError('Backup missing config.json or users.json')
    return files


def create_manual(root, guild_id, reason='manual_center'):
    guild_id = _guild_id(guild_id)
    source = Path(root) / 'data' / 'guilds' / guild_id
    if not source.is_dir():
        raise ValueError('No guild data directory')
    # Fresh folder; never overwrite existing backups and never apply retention cleanup.
    target = Path(root) / 'backups' / guild_id / (datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f') + '_' + reason + '_' + uuid4().hex[:8])
    target.mkdir(parents=True, exist_ok=False)
    try:
        for name in FILES:
            src = source / name
            if src.is_file() and not src.is_symlink():
                shutil.copy2(src, target / name)
        validate(target)
    except BaseException:
        # A new incomplete folder is kept for investigation; no original is removed.
        raise
    return target


def restore_offline(root, guild_id, selected_folder, running):
    """Restore only when bot is stopped; preserve a pre-restore snapshot first."""
    if running:
        raise RuntimeError('Stop Bot before restoring. Restoring while running is not allowed.')
    guild_id = _guild_id(guild_id)
    folder = Path(selected_folder).resolve()
    parent = (Path(root) / 'backups' / guild_id).resolve()
    if Path(selected_folder).is_symlink() or folder.parent != parent or not folder.is_dir():
        raise ValueError('Select a direct backup folder for this guild')
    validate(folder)
    safety = create_manual(root, guild_id, 'before_restore_DO_NOT_DELETE')
    # Reuse existing transactional/staged restore implementation.
    from backup import restore_backup
    restored = restore_backup(guild_id, folder)
    return safety, restored


def delete_backup(root, guild_id, selected_folder):
    """Permanently remove one explicitly selected backup, never guild data or safety copies."""
    guild_id = _guild_id(guild_id)
    parent = (Path(root) / 'backups' / guild_id).resolve()
    candidate = Path(selected_folder)
    if candidate.is_symlink() or not candidate.is_dir():
        raise ValueError('Selected backup is not a regular directory')
    folder = candidate.resolve()
    if folder.parent != parent or folder == parent:
        raise ValueError('Selected folder is not a direct backup for this guild')
    if 'before_restore_DO_NOT_DELETE' in folder.name:
        raise ValueError('Pre-restore safety backups are protected from deletion')
    # Refuse suspicious content rather than risking deletions outside the backup tree.
    for entry in folder.rglob('*'):
        if entry.is_symlink():
            raise ValueError('Cannot delete a backup containing symbolic links')
    validate(folder)
    shutil.rmtree(folder)
    return folder.name
