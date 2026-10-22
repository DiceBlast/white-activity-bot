"""Safe release discovery and staging for Activity Rewards updates.

This module never installs an update. It only accepts an explicit HTTPS (or
test-only ``file://``) manifest, downloads the named ZIP into a staging folder,
and verifies its checksum and archive paths before an installer may use it.
"""
from __future__ import annotations

import hashlib
import json
import os
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
