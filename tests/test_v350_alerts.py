import tempfile
import unittest
from pathlib import Path
from alert_center import add_alert, recent_alerts, save_settings, FailureWatcher

class AlertTests(unittest.TestCase):
    def test_storage_and_opt_out(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertTrue(add_alert(root, 'recovery', 'Bot restarted'))
            self.assertEqual(recent_alerts(root)[0]['message'], 'Bot restarted')
            save_settings(root, {'recovery': False})
            self.assertFalse(add_alert(root, 'recovery', 'No alert'))
            self.assertEqual(len(recent_alerts(root)), 1)
            self.assertFalse((root / 'data').exists())
    def test_watch_new_lines_only(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'bot.log'
            log.write_text('Old reward failed\n')
            watcher = FailureWatcher(log)
            self.assertEqual(watcher.poll(), [])
            with log.open('a') as f:
                f.write('Reward notification failed | guild_id=123\n')
            self.assertEqual(watcher.poll()[0][0], 'reward_failures')
