"""Install a staged, checksum-verified GitHub Release after the bot stops.

This helper is deliberately separate from the dashboard.  It waits for the old
supervisor to exit, creates a code-only rollback archive, replaces program
files, and starts the supervisor again.  Customer data is never extracted from
the release and is never replaced.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile


PROTECTED_TOP_LEVEL = {
    '.git', '.venv', 'backups', 'data', 'logs', 'runtime', 'updates',
    'local_credentials.json', '.dashboard_preferences.json',
    '.dashboard_font.json', '.bot_supervisor_status.json', '.bot_supervisor.lock',
}


def _safe_member(name: str) -> Path:
    path = Path(name.replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts:
        raise RuntimeError('Release ZIP contains an unsafe path.')
    return path


def _wait_for_supervisor(root: Path) -> None:
    # The restart helper owns the actual Windows lock check. A short delay here
    # lets its shutdown request finish before code is replaced.
    deadline = time.monotonic() + 30
    status = root / '.bot_supervisor_status.json'
    while time.monotonic() < deadline:
        try:
            if not status.exists() or time.time() - status.stat().st_mtime > 5:
                return
        except OSError:
            return
        time.sleep(.25)


def _release_root(staging: Path) -> Path:
    versions = list(staging.rglob('VERSION.txt'))
    if len(versions) != 1:
        raise RuntimeError('Release ZIP must contain exactly one VERSION.txt file.')
    return versions[0].parent


def install_release(archive: Path, root: Path) -> Path:
    archive, root = Path(archive).resolve(), Path(root).resolve()
    if not archive.is_file() or not zipfile.is_zipfile(archive):
        raise RuntimeError('The staged GitHub Release ZIP is missing or invalid.')
    if not (root / 'bot_supervisor.py').is_file():
        raise RuntimeError('Destination does not look like an Activity Rewards installation.')
    _wait_for_supervisor(root)
    with tempfile.TemporaryDirectory(prefix='activity_rewards_release_') as temp:
        staging = Path(temp)
        with zipfile.ZipFile(archive) as package:
            for entry in package.infolist():
                if entry.is_dir():
                    continue
                relative = _safe_member(entry.filename)
                if relative.parts and relative.parts[0] in PROTECTED_TOP_LEVEL:
                    raise RuntimeError(f'Release ZIP contains protected local content: {relative.parts[0]}')
                destination = staging / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                with package.open(entry) as source, destination.open('xb') as output:
                    shutil.copyfileobj(source, output)
        source_root = _release_root(staging)
        files = [path for path in source_root.rglob('*') if path.is_file()]
        if not files:
            raise RuntimeError('Release ZIP contains no program files.')
        for source in files:
            relative = source.relative_to(source_root)
            if relative.parts[0] in PROTECTED_TOP_LEVEL:
                raise RuntimeError(f'Release ZIP contains protected local content: {relative.parts[0]}')
        rollback_dir = root / 'runtime' / 'release_update_backups'
        rollback_dir.mkdir(parents=True, exist_ok=True)
        previous_version = (root / 'VERSION.txt').read_text(encoding='utf-8').strip().split()[0]
        rollback = rollback_dir / f'pre_v{previous_version}_{time.strftime("%Y%m%d_%H%M%S")}.zip'
        originals: dict[Path, bytes | None] = {}
        with zipfile.ZipFile(rollback, 'w', zipfile.ZIP_DEFLATED) as backup:
            for source in files:
                relative = source.relative_to(source_root)
                target = root / relative
                originals[relative] = target.read_bytes() if target.is_file() else None
                if originals[relative] is not None:
                    backup.writestr(str(relative).replace('\\', '/'), originals[relative])
        changed: list[Path] = []
        try:
            for source in files:
                relative = source.relative_to(source_root)
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix='.release_', dir=target.parent)
                try:
                    with os.fdopen(fd, 'wb') as output:
                        output.write(source.read_bytes())
                    os.replace(temporary, target)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
                changed.append(relative)
            requirements = root / 'requirements.txt'
            if requirements.is_file():
                result = subprocess.run(
                    [sys.executable, '-m', 'pip', 'install', '-r', str(requirements)],
                    cwd=root, text=True, capture_output=True, timeout=300,
                )
                if result.returncode:
                    message = (result.stderr or result.stdout or 'Dependency installation failed.').strip()
                    raise RuntimeError(f'Release code was not activated because dependency installation failed: {message}')
        except Exception:
            for relative in reversed(changed):
                target, original = root / relative, originals[relative]
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(original)
            raise
    return rollback


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    rollback = install_release(args.archive, args.root)
    print(f'Release installed. Code backup: {rollback}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
