"""Local single-instance supervisor for Activity Rewards on Windows.

The dashboard may request start/restart/stop using files in the installation
folder. No network listener, no Discord token access, no changes to guild data.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from alert_center import add_alert, FailureWatcher
import supervisor_storage as storage

ROOT = Path(__file__).resolve().parent
CONTROL = ROOT / '.bot_control_request.json'
STATUS = ROOT / '.bot_supervisor_status.json'
LOCK = ROOT / '.bot_supervisor.lock'
PYTHON = ROOT / '.venv' / 'Scripts' / 'python.exe'
LAUNCHER = ROOT / 'customer_launch.py'
DASHBOARD = ROOT / 'multi_window_dashboard.py'

def atomic_json(path, data, attempts=6, delay=0.12):
    """Publish a small JSON file safely across transient Windows file locks.

    Each attempt has a unique temporary path so competing writers cannot
    corrupt one another's temporary files. Never delete the destination if
    replacement fails: readers can keep using the last complete snapshot.
    """
    path = Path(path)
    last_error = None
    for attempt in range(attempts):
        temp = path.with_name(f'.{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp')
        try:
            with temp.open('w', encoding='utf-8') as out:
                json.dump(data, out)
            os.replace(temp, path)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(delay)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
    if last_error is not None:
        raise last_error


def publish_status(data):
    """A temporary SQLite write error must not terminate the supervisor."""
    try:
        storage.put_status(ROOT, data)
        return True
    except (OSError, storage.sqlite3.Error) as exc:
        print(f'WARNING: Could not update supervisor status: {exc}', flush=True)
        return False

# Heartbeat writes can briefly race with reads on Windows. Allow one short
# retry window, while still refusing requests to a genuinely dead supervisor.
HEARTBEAT_TIMEOUT_SECONDS = 15
STATUS_READ_ATTEMPTS = 5
STATUS_READ_DELAY_SECONDS = 0.15

def get_live_supervisor_status(root=ROOT):
    root = Path(root)
    for attempt in range(STATUS_READ_ATTEMPTS):
        status = storage.get_status(root)
        try:
            age = time.time() - float(status['timestamp'])
            if -2 <= age <= HEARTBEAT_TIMEOUT_SECONDS:
                return status
        except (KeyError, TypeError, ValueError, OverflowError):
            pass
        if attempt + 1 < STATUS_READ_ATTEMPTS:
            time.sleep(STATUS_READ_DELAY_SECONDS)
    raise RuntimeError(
        'Supervisor heartbeat unavailable or outdated. '
        'Check the START_BOT.bat console and try again.'
    )

def request_action(action, root=ROOT):
    if action not in ('stop', 'restart', 'shutdown'):
        raise ValueError('Unsupported action')
    root = Path(root)
    get_live_supervisor_status(root)
    storage.send_command(root, action)

def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return {}

def stop_process(process, wait=12):
    if process is None or process.poll() is not None:
        return
    # CTRL_BREAK lets Python handle KeyboardInterrupt/cleanup before hard stop.
    if os.name == 'nt':
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
            process.wait(timeout=wait)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            process.send_signal(signal.SIGINT)
            process.wait(timeout=wait)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()

def main():
    if not PYTHON.is_file() or not LAUNCHER.is_file():
        raise SystemExit('Missing installation. Run INSTALL.bat first.')
    if not (ROOT / 'local_credentials.json').is_file():
        raise SystemExit('Missing credentials. Run SETUP.bat first.')
    # Windows-specific exclusivity; blocks a second supervisor in the same folder.
    lock_file = LOCK.open('a+b')
    if os.name == 'nt':
        import msvcrt
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit('Bot supervisor already running for this folder.')
    # A command left by a previous supervisor must never affect a new launch.
    storage.clear_commands(ROOT)
    child = None
    stopping = False
    last_error = ''
    restart_count = 0
    crash_times = []
    retry_at = 0.0
    dashboard = None
    watcher = FailureWatcher(ROOT / 'logs' / 'bot.log')
    next_log_scan = 0.0
    try:
        child = subprocess.Popen([str(PYTHON), str(LAUNCHER)], cwd=str(ROOT), creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0))
        print('Bot started. PID:', child.pid, flush=True)
        dashboard = subprocess.Popen([str(PYTHON), str(DASHBOARD)], cwd=str(ROOT))
        print('Dashboard opened. Stop/restart using dashboard buttons.', flush=True)
        while True:
            request = storage.take_command(ROOT)
            if request:
                action = request.get('action')
                timestamp = request.get('timestamp', 0)
                if action in ('stop', 'restart', 'shutdown') and isinstance(timestamp, (int, float)) and 0 <= time.time() - timestamp < 30:
                    print('Dashboard command:', action, flush=True)
                    stopping = True
                    stop_process(child)
                    child = None
                    if action == 'shutdown':
                        publish_status({'timestamp': time.time(), 'running': False, 'stopped_by_user': True, 'shutdown_requested': True, 'pid': None})
                        # Give the dashboard time to close its own Tk windows.
                        if dashboard is not None:
                            try:
                                dashboard.wait(timeout=4)
                            except subprocess.TimeoutExpired:
                                dashboard.terminate()
                                try:
                                    dashboard.wait(timeout=3)
                                except subprocess.TimeoutExpired:
                                    dashboard.kill()
                        break
                    if action == 'restart':
                        child = subprocess.Popen([str(PYTHON), str(LAUNCHER)], cwd=str(ROOT), creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0))
                        stopping = False
                        last_error = ''
                        retry_at = 0.0
            if child is not None and child.poll() is not None:
                rc = child.returncode
                when = time.monotonic()
                crash_times = [t for t in crash_times if when - t < 300]
                crash_times.append(when)
                restart_count += 1
                delay = min(120, 5 * 2 ** min(len(crash_times) - 1, 5))
                print(f'Bot exited ({rc}). Recovery in {delay} seconds.', flush=True)
                add_alert(ROOT, 'recovery', f'Bot exited unexpectedly (code {rc}); restart scheduled in {delay}s')
                last_error = f'Bot exited with code {rc}; recovery in {delay}s'
                child = None
                retry_at = when + delay
            if child is None and not stopping and time.monotonic() >= retry_at:
                child = subprocess.Popen([str(PYTHON), str(LAUNCHER)], cwd=str(ROOT), creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0))
                add_alert(ROOT, 'recovery', f'Automatic bot restart launched (PID {child.pid})', 'info')
                last_error = ''
                retry_at = 0.0
            # Skip log checks between polls; dashboard heartbeat remains 1s.
            if time.monotonic() >= next_log_scan:
                for category, message in watcher.poll():
                    add_alert(ROOT, category, message)
                next_log_scan = time.monotonic() + 3
            publish_status({'timestamp': time.time(), 'running': child is not None and child.poll() is None, 'stopped_by_user': stopping, 'pid': child.pid if child else None, 'error': last_error, 'restart_count': restart_count, 'retry_seconds': max(0, int(retry_at - time.monotonic())) if child is None and not stopping else 0})
            time.sleep(1)
    except KeyboardInterrupt:
        print('Shutting down supervisor and bot...', flush=True)
    finally:
        stop_process(child)
        if dashboard is not None and dashboard.poll() is None:
            dashboard.terminate()
            try:
                dashboard.wait(timeout=3)
            except subprocess.TimeoutExpired:
                dashboard.kill()
                dashboard.wait()
        # Keep last status in SQLite for diagnostics; no legacy JSON writes.
        lock_file.close()

if __name__ == '__main__':
    main()
