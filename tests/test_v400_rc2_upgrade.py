import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from safe_upgrade import CODE_FILES, run_upgrade


class SafeUpgradeTests(unittest.TestCase):
    def test_preserves_data_and_creates_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / 'bot.py').write_text('old bot')
            (target / 'bot_supervisor.py').write_text('old supervisor')
            (target / 'dota_test.py').write_text('legacy Dota code')
            (target / 'data').mkdir()
            (target / 'data' / 'users.json').write_text('{"score":123}')
            (target / 'local_credentials.json').write_text('SECRET')
            snapshot = run_upgrade(target)
            self.assertTrue(snapshot.is_file())
            self.assertEqual((target / 'data' / 'users.json').read_text(), '{"score":123}')
            self.assertEqual((target / 'local_credentials.json').read_text(), 'SECRET')
            self.assertIn('Activity Rewards', (target / 'bot_supervisor.py').read_text())
            self.assertFalse((target / 'dota_test.py').exists())

    def test_rejects_live_supervisor(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / 'bot.py').write_text('old')
            (target / 'bot_supervisor.py').write_text('old')
            (target / '.bot_supervisor_status.json').write_text(json.dumps({'timestamp': time.time()}))
            with self.assertRaisesRegex(RuntimeError, 'active'):
                run_upgrade(target)

    def test_refuses_symlink_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / 'bot.py').write_text('old')
            (target / 'bot_supervisor.py').write_text('old')
            try:
                (target / 'voice.py').symlink_to(target / 'bot.py')
            except OSError:
                self.skipTest('Symlinks unavailable')
            with self.assertRaisesRegex(RuntimeError, 'symlink'):
                run_upgrade(target)


if __name__ == '__main__':
    unittest.main()
