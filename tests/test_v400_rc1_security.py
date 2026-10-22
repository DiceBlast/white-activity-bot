import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from backup_center import restore_offline
from backup import _cleanup
import backup


class ReleaseCandidateSafetyTests(unittest.TestCase):
    def test_retention_keeps_restore_safety_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'backups'
            guild = root / '123'
            safety = guild / '2020-01-01_00-00-00_before_restore_DO_NOT_DELETE_example'
            old = guild / '2020-01-01_00-00-00_scheduled'
            safety.mkdir(parents=True)
            old.mkdir()
            with patch.object(backup, 'BACKUP_ROOT', root):
                _cleanup('123', 0, datetime.now(timezone.utc) + timedelta(seconds=10))
            self.assertTrue(safety.is_dir())
            self.assertFalse(old.exists())

    def test_restore_rejects_symlink_backup_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / 'backups' / '123'
            parent.mkdir(parents=True)
            target = root / 'elsewhere'
            target.mkdir()
            link = parent / 'linked'
            try:
                link.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest('Symlinks unavailable')
            with self.assertRaises(ValueError):
                restore_offline(root, '123', link, False)

    def test_restore_rejects_linked_json_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / 'backups' / '123'
            folder = parent / 'snapshot'
            folder.mkdir(parents=True)
            (folder / 'config.json').write_text('{}')
            outside = root / 'outside.json'
            outside.write_text('{}')
            try:
                (folder / 'users.json').symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest('Symlinks unavailable')
            with self.assertRaises(ValueError):
                restore_offline(root, '123', folder, False)


if __name__ == '__main__':
    unittest.main()
