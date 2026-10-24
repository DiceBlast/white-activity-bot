"""Safe release discovery and staging for Activity Rewards updates.

This module never installs an update. It only accepts an explicit HTTPS (or
test-only ``file://``) manifest, downloads the named ZIP into a staging folder,
and verifies its checksum and archive paths before an installer may use it.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zipfile


MAX_RELEASE_BYTES = 100 * 1024 * 1024
MANIFEST_NAME = 'update_manifest.json'
UPDATE_HISTORY_NAME = 'github_update_history.json'
GITHUB_RELEASE_API = 'https://api.github.com/repos/DiceBlast/white-activity-bot/releases/latest'
GITHUB_TOKEN_ENV = 'ACTIVITY_REWARDS_GITHUB_TOKEN'


@dataclass(frozen=True)
class Release:
    version: str
    download_url: str
    sha256: str


@dataclass(frozen=True)
class GitUpdateResult:
    """Outcome of one operator-triggered GitHub update attempt."""

    updated: bool
    details: str


def _github_headers() -> dict[str, str]:
    """Return API headers without ever saving a token to disk."""
    headers = {
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'ActivityRewards-Updater/1.0',
    }
    token = os.environ.get(GITHUB_TOKEN_ENV, '').strip()
    if token:
        headers['Authorization'] = f'Bearer {token}'
    return headers


def _history_path(root: Path) -> Path:
    return Path(root) / 'runtime' / UPDATE_HISTORY_NAME


def get_update_history(root: Path) -> dict:
    """Read local GitHub update history without creating or changing files."""
    try:
        payload = json.loads(_history_path(root).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_update_history(root: Path, payload: dict) -> None:
    path = _history_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def _record_history(root: Path, key: str, entry: dict) -> None:
    history = get_update_history(root)
    history[key] = {**entry, 'at': time.strftime('%Y-%m-%d %H:%M:%S %z')}
    _save_update_history(root, history)


def _version_tuple(value: str) -> tuple[int, ...]:
    pieces = str(value).strip().split('.')
    if not pieces or any(not piece.isdecimal() for piece in pieces):
        raise ValueError('Version must contain only numeric dot-separated components.')
    return tuple(int(piece) for piece in pieces)


def current_version(root: Path) -> str:
    value = (Path(root) / 'VERSION.txt').read_text(encoding='utf-8').strip().split()[0]
    _version_tuple(value)
    return value


def _read_json_url(url: str, headers: dict[str, str] | None = None) -> dict:
    parsed = urlparse(url)
    if parsed.scheme not in ('https', 'file'):
        raise ValueError('Update manifest must use HTTPS (or file:// for a local test).')
    request = Request(url, headers=headers or {'User-Agent': 'ActivityRewards-Updater/1.0'})
    with urlopen(request, timeout=15) as response:
        payload = response.read(256_000)
    try:
        parsed_json = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Update manifest is not valid JSON.') from exc
    if not isinstance(parsed_json, dict):
        raise ValueError('Update manifest must be a JSON object.')
    return parsed_json


def _read_checksum_url(url: str, headers: dict[str, str]) -> str:
    request = Request(url, headers=headers)
    with urlopen(request, timeout=15) as response:
        value = response.read(512).decode('ascii', errors='strict').strip().split()[0].lower()
    return value


def fetch_release(manifest_url: str) -> Release:
    payload = _read_json_url(manifest_url)
    version = str(payload.get('version', '')).strip()
    download_url = str(payload.get('download_url', '')).strip()
    sha256 = str(payload.get('sha256', '')).strip().lower()
    _version_tuple(version)
    parsed = urlparse(download_url)
    if parsed.scheme not in ('https', 'file'):
        raise ValueError('Release download must use HTTPS (or file:// for a local test).')
    if len(sha256) != 64 or any(char not in '0123456789abcdef' for char in sha256):
        raise ValueError('Release manifest has an invalid SHA-256 checksum.')
    return Release(version, download_url, sha256)


def check_for_update(root: Path, manifest_url: str) -> Release | None:
    release = fetch_release(manifest_url)
    return release if _version_tuple(release.version) > _version_tuple(current_version(root)) else None


def _release_from_github_payload(payload: dict) -> Release:
    """Convert one GitHub release response to a verified downloadable release."""
    if payload.get('draft') or payload.get('prerelease'):
        raise ValueError('The latest GitHub release is not a published stable release.')
    version = str(payload.get('tag_name') or payload.get('name') or '').strip().lstrip('vV')
    _version_tuple(version)
    assets = payload.get('assets')
    if not isinstance(assets, list):
        raise ValueError('GitHub release does not contain downloadable assets.')
    zips = [asset for asset in assets if isinstance(asset, dict) and str(asset.get('name', '')).lower().endswith('.zip')]
    if len(zips) != 1:
        raise ValueError('GitHub release must contain exactly one Activity Rewards ZIP asset.')
    asset = zips[0]
    download_url = str(asset.get('browser_download_url', '')).strip()
    digest = str(asset.get('digest', '')).strip().lower()
    if digest.startswith('sha256:'):
        digest = digest.removeprefix('sha256:')
    if len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest):
        checksum_assets = [
            item for item in assets if isinstance(item, dict)
            and str(item.get('name', '')).lower() == str(asset.get('name', '')).lower() + '.sha256'
        ]
        if len(checksum_assets) == 1:
            checksum_url = str(checksum_assets[0].get('browser_download_url', '')).strip()
            if checksum_url:
                digest = _read_checksum_url(checksum_url, _github_headers())
    if len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest):
        raise ValueError('GitHub release ZIP is missing its SHA-256 digest. Publish it through the release workflow.')
    return Release(version, download_url, digest)


def fetch_github_release() -> Release:
    """Read the latest published GitHub Release without changing the installation.

    Public releases work with no setup. A private repository needs a short-lived
    token supplied only through ``ACTIVITY_REWARDS_GITHUB_TOKEN`` on that PC.
    The token is never written to a file, log, update history, or backup.
    """
    try:
        payload = _read_json_url(GITHUB_RELEASE_API, _github_headers())
    except Exception as exc:
        message = str(exc)
        if '401' in message or '404' in message:
            raise RuntimeError(
                'GitHub Release feed is unavailable. Make the repository public, or set '
                f'{GITHUB_TOKEN_ENV} on this PC for private releases.'
            ) from exc
        raise RuntimeError(f'Could not read the GitHub Release feed: {message}') from exc
    return _release_from_github_payload(payload)


def check_github_release_update(root: Path) -> Release | None:
    release = fetch_github_release()
    _record_history(Path(root), 'last_release_check', {'available_version': release.version})
    return release if _version_tuple(release.version) > _version_tuple(current_version(root)) else None


def _safe_archive(archive: Path) -> None:
    with zipfile.ZipFile(archive) as package:
        entries = [entry for entry in package.infolist() if not entry.is_dir()]
        if not entries:
            raise ValueError('Release ZIP contains no files.')
        total_size = sum(entry.file_size for entry in entries)
        if total_size > MAX_RELEASE_BYTES * 4:
            raise ValueError('Release ZIP is unexpectedly large.')
        names = []
        for entry in entries:
            name = entry.filename.replace('\\', '/')
            path = Path(name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('Release ZIP contains an unsafe path.')
            names.append(name)
        if not any(name.endswith('/VERSION.txt') for name in names):
            raise ValueError('Release ZIP is missing VERSION.txt.')
        if any(Path(name).name in {'local_credentials.json', '.dota_test_guild.json'} for name in names):
            raise ValueError('Release ZIP must not contain local credentials or runtime configuration.')


def stage_release(release: Release, staging_dir: Path) -> Path:
    """Download, checksum, and inspect a release ZIP without installing it."""
    staging_dir = Path(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    destination = staging_dir / f'activity-rewards-{release.version}.zip'
    temporary = destination.with_suffix('.download')
    if temporary.exists():
        temporary.unlink()
    digest = hashlib.sha256()
    received = 0
    request = Request(release.download_url, headers={'User-Agent': 'ActivityRewards-Updater/1.0'})
    try:
        with urlopen(request, timeout=30) as response, temporary.open('xb') as output:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_RELEASE_BYTES:
                    raise ValueError('Release download exceeds the size limit.')
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest().lower() != release.sha256:
            raise ValueError('Release checksum does not match the manifest.')
        _safe_archive(temporary)
        os.replace(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def update_from_git(root: Path) -> GitUpdateResult:
    """Fast-forward local source from its configured GitHub remote and install requirements.

    This never touches data, credentials, backups, logs, or dashboard preferences:
    Git only changes tracked source files, while pip uses the Python environment
    that launched the dashboard.  The caller must be an operator who explicitly
    requested the update.
    """
    root = Path(root).resolve()
    git = shutil.which('git')
    if not git:
        raise RuntimeError('Git is not installed or is not available on PATH.')
    if not (root / '.git').is_dir():
        raise RuntimeError('This installation is not a Git clone. Clone the private repository first.')

    def run(*args: str) -> str:
        result = subprocess.run(
            [git, *args], cwd=root, text=True, capture_output=True, timeout=90,
        )
        if result.returncode:
            message = (result.stderr or result.stdout or 'Git command failed.').strip()
            raise RuntimeError(message)
        return (result.stdout or result.stderr).strip()

    if run('status', '--porcelain'):
        raise RuntimeError('Local source changes are present. Commit or discard them before updating.')
    previous_commit = run('rev-parse', 'HEAD')
    previous_version = current_version(root)
    run('remote', 'get-url', 'origin')
    run('fetch', '--prune', 'origin')
    ahead_behind = run('rev-list', '--left-right', '--count', 'HEAD...@{u}').split()
    if len(ahead_behind) != 2 or not all(part.isdecimal() for part in ahead_behind):
        raise RuntimeError('Could not compare the local source with its GitHub branch.')
    ahead, behind = (int(part) for part in ahead_behind)
    if not ahead and not behind:
        return GitUpdateResult(False, 'Already up to date. Dependencies were not changed.')
    if ahead and not behind:
        raise RuntimeError('Local source is ahead of GitHub. Push local changes before updating.')
    if ahead and behind:
        raise RuntimeError('Local and GitHub source have diverged. Resolve the Git conflict before updating.')
    run('merge', '--ff-only', '@{u}')

    requirements = root / 'requirements.txt'
    if requirements.is_file():
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'install', '-r', str(requirements)],
            cwd=root, text=True, capture_output=True, timeout=300,
        )
        if result.returncode:
            message = (result.stderr or result.stdout or 'Dependency installation failed.').strip()
            raise RuntimeError(f'GitHub source updated, but dependency installation failed: {message}')
    _record_history(root, 'last_update', {
        'from_commit': previous_commit,
        'to_commit': run('rev-parse', 'HEAD'),
        'from_version': previous_version,
        'to_version': current_version(root),
    })
    return GitUpdateResult(True, 'GitHub source and required packages were updated. Restart the bot to use the new code.')


def check_git_update(root: Path) -> GitUpdateResult:
    """Check the configured GitHub remote without changing source or packages."""
    root = Path(root).resolve()
    git = shutil.which('git')
    if not git:
        raise RuntimeError('Git is not installed or is not available on PATH.')
    if not (root / '.git').is_dir():
        raise RuntimeError('This installation is not a Git clone. Run INSTALL.bat to connect GitHub updates.')

    def run(*args: str) -> str:
        result = subprocess.run(
            [git, *args], cwd=root, text=True, capture_output=True, timeout=90,
        )
        if result.returncode:
            message = (result.stderr or result.stdout or 'Git command failed.').strip()
            raise RuntimeError(message)
        return (result.stdout or result.stderr).strip()

    if run('status', '--porcelain'):
        raise RuntimeError('Local source changes are present. Commit or discard them before checking for updates.')
    run('remote', 'get-url', 'origin')
    run('fetch', '--prune', 'origin')
    _record_history(root, 'last_check', {})
    ahead_behind = run('rev-list', '--left-right', '--count', 'HEAD...@{u}').split()
    if len(ahead_behind) != 2 or not all(part.isdecimal() for part in ahead_behind):
        raise RuntimeError('Could not compare the local source with its GitHub branch.')
    ahead, behind = (int(part) for part in ahead_behind)
    if not ahead and not behind:
        return GitUpdateResult(False, 'You are running the latest GitHub version.')
    if not ahead and behind:
        plural = 's' if behind != 1 else ''
        return GitUpdateResult(True, f'{behind} GitHub update{plural} available. Select Update from GitHub to install it.')
    if ahead and not behind:
        return GitUpdateResult(False, 'This installation has local source changes ahead of GitHub.')
    return GitUpdateResult(False, 'Local source and GitHub have diverged. Resolve the Git conflict before updating.')


def rollback_git_update(root: Path) -> GitUpdateResult:
    """Return tracked source to the version recorded before the last GitHub update."""
    root = Path(root).resolve()
    history = get_update_history(root)
    last_update = history.get('last_update') if isinstance(history.get('last_update'), dict) else {}
    target = str(last_update.get('from_commit', '')).strip()
    if len(target) < 7 or len(target) > 64 or any(char not in '0123456789abcdefABCDEF' for char in target):
        raise RuntimeError('No rollback point is available yet. Install a GitHub update first.')
    git = shutil.which('git')
    if not git:
        raise RuntimeError('Git is not installed or is not available on PATH.')
    if not (root / '.git').is_dir():
        raise RuntimeError('This installation is not a Git clone. Run INSTALL.bat to connect GitHub updates.')

    def run(*args: str) -> str:
        result = subprocess.run([git, *args], cwd=root, text=True, capture_output=True, timeout=300)
        if result.returncode:
            message = (result.stderr or result.stdout or 'Git command failed.').strip()
            raise RuntimeError(message)
        return (result.stdout or result.stderr).strip()

    if run('status', '--porcelain'):
        raise RuntimeError('Local source changes are present. Commit or discard them before rolling back.')
    current_commit = run('rev-parse', 'HEAD')
    if current_commit.lower() == target.lower():
        raise RuntimeError('This installation is already at the recorded rollback version.')
    run('cat-file', '-e', f'{target}^{{commit}}')
    current_version_value = current_version(root)
    run('reset', '--hard', target)
    requirements = root / 'requirements.txt'
    if requirements.is_file():
        result = subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(requirements)], cwd=root, text=True, capture_output=True, timeout=300)
        if result.returncode:
            message = (result.stderr or result.stdout or 'Dependency installation failed.').strip()
            raise RuntimeError(f'GitHub source was rolled back, but dependency installation failed: {message}')
    _record_history(root, 'last_rollback', {
        'from_commit': current_commit,
        'to_commit': target,
        'from_version': current_version_value,
        'to_version': current_version(root),
    })
    return GitUpdateResult(True, 'Previous GitHub version restored. Restart the bot to use the rolled-back code.')
