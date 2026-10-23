"""Conservative, code-only upgrade installer for Activity Rewards Windows.

Run this module FROM the extracted release folder, with the existing bot folder
as destination. Live guild data, credentials, settings and backups are never read
or modified. A code rollback archive is created before any replacement.
"""
import argparse
import json
import os
from pathlib import Path
import py_compile
import shutil
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parent
# Explicit allowlist; never use shutil.copytree into an existing installation.
CODE_FILES = (
    'alert_center.py', 'analytics_history.py', 'analytics_reports.py',
    'backup.py', 'backup_center.py', 'bot.py', 'bot_supervisor.py',
    'supervisor_storage.py', 'connection_health.py',
    'commands.py', 'config_manager.py', 'customer_invite.py',
    'customer_launch.py', 'customer_setup.py', 'dashboard_metrics.py',
    'dashboard_preferences.py', 'database.py', 'discord_live_monitor.py',
    'event_logic.py', 'guild_dashboard.py', 'guild_log_parser.py',
    'logger.py', 'messages.py', 'multi_window_dashboard.py',
    'reward_loop.py', 'scheduler.py', 'setup_wizard.py', 'storage.py',
    'voice.py', 'requirements.txt', 'INSTALL.bat', 'INVITE_BOT.bat',
    'RUN_MULTI_WINDOWS.bat', 'SETUP.bat', 'START_BOT.bat',
    'VERSION.txt', 'README_V403.txt', 'START_HERE_WINDOWS.txt',
    'release_preflight.py', 'update_manager.py', 'CHECK_RELEASE.bat',
)
OBSOLETE_CODE_FILES = ('dota_test.py',)
PROTECTED = ('data', 'backups', 'logs', '.venv', 'local_credentials.json',
             '.dashboard_preferences.json', '.bot_supervisor_status.json', 'runtime')


def validate_destination(target):
    target = Path(target).resolve()
    if target == ROOT.resolve():
        raise RuntimeError('Choose the EXISTING installation, not this release folder.')
    if not (target / 'bot.py').is_file() or not (target / 'bot_supervisor.py').is_file():
        raise RuntimeError('Destination does not look like an existing Activity Rewards installation.')
    if (target / '.bot_supervisor_status.json').exists():
        try:
            status = json.loads((target / '.bot_supervisor_status.json').read_text(encoding='utf-8'))
            if abs(time.time() - float(status.get('timestamp', 0))) < 20:
                raise RuntimeError('Bot supervisor appears active. Use Shutdown All first.')
        except (ValueError, TypeError, OSError):
            raise RuntimeError('Cannot verify supervisor status. Close the bot and retry.')
    # Check both previous JSON heartbeat and new SQLite heartbeat (no DB writes).
    from supervisor_storage import get_status
    status = get_status(target)
    if status:
        try:
            if abs(time.time() - float(status.get('timestamp', 0))) < 20:
                raise RuntimeError('Bot supervisor appears active. Use Shutdown All first.')
        except (ValueError, TypeError):
            raise RuntimeError('Cannot verify supervisor status. Close the bot and retry.')
    return target


def run_upgrade(destination, source=ROOT):
    source = Path(source).resolve()
    destination = validate_destination(destination)
    for filename in CODE_FILES:
        path = source / filename
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f'Missing or invalid release file: {filename}')
        if filename.endswith('.py'):
            # Compile in-memory to avoid modifying source or destination caches.
            compile(path.read_bytes(), str(path), 'exec')
        dest = destination / filename
        if dest.is_symlink():
            raise RuntimeError(f'Refusing to replace symlink: {filename}')
    for filename in OBSOLETE_CODE_FILES:
        if (destination / filename).is_symlink():
            raise RuntimeError(f'Refusing to remove symlink: {filename}')
    snapshot_dir = destination / '.upgrade_code_backups'
    snapshot_dir.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='pre_v403_', suffix='.zip', dir=snapshot_dir, delete=False) as temp:
        snapshot = Path(temp.name)
    tracked_files = (*CODE_FILES, *OBSOLETE_CODE_FILES)
    previous = {name: (destination / name).read_bytes() if (destination / name).exists() else None for name in tracked_files}
    try:
        with zipfile.ZipFile(snapshot, 'w', zipfile.ZIP_DEFLATED) as out:
            for name, content in previous.items():
                if content is not None:
                    out.writestr(name, content)
        modified = []
        try:
            for name in CODE_FILES:
                dst = destination / name
                fd, tmp = tempfile.mkstemp(prefix='.upgrade_', dir=destination)
                try:
                    with os.fdopen(fd, 'wb') as f:
                        f.write((source / name).read_bytes())
                    os.replace(tmp, dst)
                finally:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                modified.append(name)
            for name in OBSOLETE_CODE_FILES:
                dst = destination / name
                if dst.exists():
                    dst.unlink()
                modified.append(name)
        except Exception:
            for name in reversed(modified):
                dst = destination / name
                old = previous[name]
                if old is None:
                    dst.unlink(missing_ok=True)
                else:
                    dst.write_bytes(old)
            raise
    except Exception:
        snapshot.unlink(missing_ok=True)
        raise
    return snapshot


def main():
    parser = argparse.ArgumentParser(description='Safely install v4.0.7 code into an existing bot folder.')
    parser.add_argument('destination', type=Path, help='Existing Activity Rewards installation directory')
    args = parser.parse_args()
    snapshot = run_upgrade(args.destination)
    print('Upgrade completed. Previous code archived to:', snapshot)
    print('Existing data, backups, logs, credentials, preferences and .venv were untouched.')
    print('If needed, run INSTALL.bat to update dependencies. Then START_BOT.bat.')


if __name__ == '__main__':
    main()
