import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import zipfile

from update_manager import Release, check_for_update, current_version, stage_release


def file_url(path):
    return Path(path).resolve().as_uri()


class UpdateManagerTests(unittest.TestCase):
    def create_release(self, folder, version='4.0.4'):
        archive = Path(folder) / 'release.zip'
        with zipfile.ZipFile(archive, 'w') as package:
            package.writestr('ActivityRewards/VERSION.txt', version + '\n')
            package.writestr('ActivityRewards/bot.py', '# test release\n')
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        manifest = Path(folder) / 'manifest.json'
        manifest.write_text(json.dumps({
            'version': version,
            'download_url': file_url(archive),
            'sha256': digest,
        }), encoding='utf-8')
        return archive, manifest, digest

    def test_checks_and_stages_verified_local_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'bot'
            root.mkdir()
            (root / 'VERSION.txt').write_text('4.0.3\n', encoding='utf-8')
            archive, manifest, digest = self.create_release(temporary)
            release = check_for_update(root, file_url(manifest))
            self.assertEqual(release, Release('4.0.4', file_url(archive), digest))
            staged = stage_release(release, root / 'updates')
            self.assertTrue(staged.is_file())
            self.assertEqual(hashlib.sha256(staged.read_bytes()).hexdigest(), digest)
            self.assertEqual(current_version(root), '4.0.3')

    def test_rejects_tampered_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive, manifest, digest = self.create_release(temporary)
            archive.write_bytes(archive.read_bytes() + b'tampered')
            release = Release('4.0.4', file_url(archive), digest)
            with self.assertRaisesRegex(ValueError, 'checksum'):
                stage_release(release, Path(temporary) / 'updates')


if __name__ == '__main__':
    unittest.main()
