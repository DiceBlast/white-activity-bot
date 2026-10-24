"""Create the safe ZIP asset consumed by the dashboard's Release updater."""
from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parent
EXCLUDED = {'.git', '.venv', 'backups', 'data', 'logs', 'runtime', 'updates', 'dist', '__pycache__', '.pytest_cache'}
EXCLUDED_FILES = {'local_credentials.json', '.dashboard_preferences.json', '.dashboard_font.json', '.bot_supervisor_status.json', '.bot_supervisor.lock'}


def build(destination: Path) -> Path:
    version = (ROOT / 'VERSION.txt').read_text(encoding='utf-8').strip().split()[0]
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f'ActivityRewards-v{version}.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as package:
        for path in ROOT.rglob('*'):
            relative = path.relative_to(ROOT)
            if not path.is_file() or any(part in EXCLUDED for part in relative.parts) or path.name in EXCLUDED_FILES:
                continue
            package.write(path, Path('ActivityRewards') / relative)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    return archive


if __name__ == '__main__':
    result = build(ROOT / 'dist')
    print(result)
