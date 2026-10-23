"""Relaunch the supervisor after a dashboard-installed source update.

This short-lived helper survives the dashboard shutdown, waits for the old
supervisor to release its single-instance lock, then starts a fresh supervisor
which opens a dashboard running the newly downloaded code.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent
LOCK = ROOT / '.bot_supervisor.lock'
PYTHON = ROOT / '.venv' / 'Scripts' / 'python.exe'
SUPERVISOR = ROOT / 'bot_supervisor.py'


def supervisor_lock_is_free() -> bool:
    """Return whether the Windows single-instance lock can be acquired."""
    if os.name != 'nt':
        return True
    import msvcrt
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open('a+b') as handle:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            return False
    return True


def main() -> int:
    if not PYTHON.is_file() or not SUPERVISOR.is_file():
        return 1
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if supervisor_lock_is_free():
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            subprocess.Popen([str(PYTHON), str(SUPERVISOR)], cwd=str(ROOT), creationflags=flags)
            return 0
        time.sleep(0.25)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
