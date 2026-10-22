import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot_supervisor import request_action
import supervisor_storage as db

class SupervisorTests(unittest.TestCase):
    def test_local_control_protocol(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch('bot_supervisor.time.time', return_value=1000):
                db.put_status(root, {'timestamp': 1000, 'running': True})
                request_action('restart', root)
                self.assertEqual(db.take_command(root)['action'], 'restart')
                request_action('stop', root)
                self.assertEqual(db.take_command(root)['action'], 'stop')
            self.assertFalse((root / 'data').exists())
    def test_refuse_if_not_running(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RuntimeError):
                request_action('stop', folder)
    def test_refuse_unknown_command(self):
        with self.assertRaises(ValueError):
            request_action('erase')
