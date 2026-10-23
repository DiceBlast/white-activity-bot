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
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zipfile


MAX_RELEASE_BYTES = 100 * 1024 * 1024
MANIFEST_NAME = 'update_manifest.json'


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


def _version_tuple(value: str) -> tuple[int, ...]:
    pieces = str(value).strip().split('.')
    if not pieces or any(not piece.isdecimal() for piece in pieces):
        raise ValueError('Version must contain only numeric dot-separated components.')
    return tuple(int(piece) for piece in pieces)


def current_version(root: Path) -> str:
    value = (Path(root) / 'VERSION.txt').read_text(encoding='utf-8').strip().split()[0]
    _version_tuple(value)
    return value


def _read_json_url(url: str) -> dict:
    parsed = urlparse(url)
    if parsed.scheme not in ('https', 'file'):
        raise ValueError('Update manifest must use HTTPS (or file:// for a local test).')
    request = Request(url, headers={'User-Agent': 'ActivityRewards-Updater/1.0'})
    with urlopen(request, timeout=15) as response:
        payload = response.read(256_000)
    try:
        parsed_json = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Update manifest is not valid JSON.') from exc
    if not isinstance(parsed_json, dict):
        raise ValueError('Update manifest must be a JSON object.')
    return parsed_json


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
