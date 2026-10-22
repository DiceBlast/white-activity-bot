import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import bot_supervisor as sup
import supervisor_storage as db

class SqliteSupervisorTests(unittest.TestCase):
    def test_status_round_trip_and_no_legacy_json(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(db.get_status(root), {})
            self.assertFalse((root / 'runtime').exists())
            db.put_status(root, {'timestamp': time.time(), 'pid': 123, 'running': True})
            self.assertTrue(db.get_status(root)['running'])
            self.assertTrue(db.db_path(root).is_file())
            self.assertEqual(list(root.glob('*.tmp')), [])
            self.assertFalse((root / '.bot_supervisor_status.json').exists())

    def test_command_queue_order_and_one_time_consumption(self):
        with tempfile.TemporaryDirectory() as td:
            db.send_command(td, 'restart')
            db.send_command(td, 'stop')
            self.assertEqual(db.take_command(td)['action'], 'restart')
            self.assertEqual(db.take_command(td)['action'], 'stop')
            self.assertEqual(db.take_command(td), {})

    def test_stale_commands_removed_at_next_launch(self):
        with tempfile.TemporaryDirectory() as td:
            db.send_command(td, 'shutdown')
            db.clear_commands(td)
            self.assertEqual(db.take_command(td), {})

    def test_supervisor_control_with_valid_heartbeat(self):
        with tempfile.TemporaryDirectory() as td:
            db.put_status(td, {'timestamp': time.time(), 'running': True})
            sup.request_action('restart', td)
            self.assertEqual(db.take_command(td)['action'], 'restart')

    def test_db_write_error_cannot_crash_heartbeat(self):
        with patch.object(sup.storage, 'put_status', side_effect=sqlite3.OperationalError('busy')):
            self.assertFalse(sup.publish_status({'timestamp': time.time()}))

    def test_invalid_action_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                db.send_command(td, 'delete')

if __name__ == '__main__':
    unittest.main()
