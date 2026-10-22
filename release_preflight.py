"""Read-only preflight for Windows releases (no Discord connection)."""
from pathlib import Path
import ast
import importlib.util
import json
import sys
import zipfile

ROOT = Path(__file__).resolve().parent
MODULES = ('discord', 'psutil', 'nacl', 'tzdata')
README = 'README_V403.txt'
REQUIRED = ('bot.py', 'bot_supervisor.py', 'supervisor_storage.py', 'multi_window_dashboard.py',
            'backup_center.py', 'backup.py', 'safe_upgrade.py', 'START_BOT.bat')


def inspect_install(root=ROOT, check_dependencies=True):
    root = Path(root)
    findings = []
    def check(ok, label):
        findings.append((bool(ok), label))
    for file in REQUIRED:
        check((root / file).is_file(), f'Required application file: {file}')
    for code in sorted(root.glob('*.py')):
        try:
            ast.parse(code.read_text(encoding='utf-8'), filename=str(code))
            check(True, f'Python parse: {code.name}')
        except (SyntaxError, UnicodeError, OSError) as exc:
            check(False, f'Python parse: {code.name}: {exc}')
    check((root / README).is_file(), f'Current release notes: {README}')
    # Never print or parse credentials: existence only.
    check((root / 'local_credentials.json').is_file(), 'Local credentials configured (fresh installs run SETUP.bat)')
    if check_dependencies:
        for module in MODULES:
            check(importlib.util.find_spec(module) is not None, f'Dependency available: {module}')
    return findings


def main():
    print('Activity Rewards v4.0.3 — read-only local preflight')
    print('No bot startup, Discord requests, or data modifications are performed.\n')
    findings = inspect_install()
    for passed, label in findings:
        print(('PASS' if passed else 'CHECK') + ' | ' + label)
    failed = [x for x in findings if not x[0]]
    print(f'\n{len(findings)-len(failed)}/{len(findings)} checks passed.')
    if failed:
        print('A CHECK may be expected in a newly extracted release without credentials/dependencies.')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
