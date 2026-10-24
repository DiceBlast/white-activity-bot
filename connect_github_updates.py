"""Safely attach a legacy extracted Activity Rewards install to GitHub updates."""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import shutil
import subprocess


REPOSITORY_URL = 'https://github.com/DiceBlast/white-activity-bot.git'
PRESERVE_NAMES = ('data', 'backups', 'logs', 'runtime', 'local_credentials.json')


def run(git: str, root: Path, *args: str) -> None:
    result = subprocess.run([git, *args], cwd=root, text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or 'Git command failed.').strip())


def backup_local_state(root: Path) -> Path:
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    destination = root.parent / f'{root.name}-before-github-{stamp}'
    destination.mkdir(parents=True, exist_ok=False)
    for name in PRESERVE_NAMES:
        source = root / name
        if not source.exists():
            continue
        if source.is_dir():
            shutil.copytree(source, destination / name)
        else:
            shutil.copy2(source, destination / name)
    return destination


def connect(root: Path) -> Path:
    root = root.resolve()
    if not (root / 'bot.py').is_file() or not (root / 'requirements.txt').is_file():
        raise ValueError('Choose the existing Activity Rewards folder containing bot.py and requirements.txt.')
    git = shutil.which('git')
    if not git:
        raise RuntimeError('Git is not installed. Install Git for Windows, then run this tool again.')
    backup = backup_local_state(root)
    if not (root / '.git').is_dir():
        run(git, root, 'init')
    remote_check = subprocess.run([git, 'remote', 'get-url', 'origin'], cwd=root, capture_output=True, text=True)
    if remote_check.returncode:
        run(git, root, 'remote', 'add', 'origin', REPOSITORY_URL)
    else:
        run(git, root, 'remote', 'set-url', 'origin', REPOSITORY_URL)
    run(git, root, 'fetch', '--prune', 'origin')
    run(git, root, 'reset', '--hard', 'origin/main')
    run(git, root, 'branch', '-M', 'main')
    run(git, root, 'branch', '--set-upstream-to=origin/main', 'main')
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description='Connect a legacy Activity Rewards installation to GitHub updates.')
    parser.add_argument('target', help='Existing Activity Rewards folder')
    args = parser.parse_args()
    try:
        backup = connect(Path(args.target))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f'Connection failed: {exc}')
        return 1
    print('GitHub updates are connected.')
    print(f'Local token/data/log backup: {backup}')
    print('Run INSTALL.bat in the existing bot folder once, then START_BOT.bat.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
