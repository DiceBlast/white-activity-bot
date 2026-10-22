import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot_supervisor as sup


class WindowsStatusTests(unittest.TestCase):
    def test_transient_windows_lock_retries_and_preserves_old_status(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / '.bot_supervisor_status.json'
            target.write_text('{"previous": true}', encoding='utf-8')
            original_replace = sup.os.replace
            snapshots = []
            def locked_once(source, dest):
                snapshots.append(target.read_text(encoding='utf-8'))
                if len(snapshots) == 1:
                    raise PermissionError(5, 'Access is denied')
                return original_replace(source, dest)
            with patch.object(sup.os, 'replace', side_effect=locked_once), patch.object(sup.time, 'sleep'):
                sup.atomic_json(target, {'running': True}, attempts=3)
            self.assertEqual(snapshots, ['{"previous": true}', '{"previous": true}'])
            self.assertTrue(sup.read_json(target)['running'])
            self.assertEqual(len(list(Path(folder).glob('*.tmp'))), 0)

    def test_sqlite_write_failure_never_crashes_supervisor_status_update(self):
        with patch.object(sup.storage, 'put_status', side_effect=sup.storage.sqlite3.OperationalError('database is locked')):
            self.assertFalse(sup.publish_status({'running': False}))

    def test_control_file_remains_atomic(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'control.json'
            sup.atomic_json(target, {'action': 'shutdown'})
            self.assertEqual(sup.read_json(target)['action'], 'shutdown')

if __name__ == '__main__':
    unittest.main()
