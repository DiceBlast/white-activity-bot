import json
import tempfile
import time
import unittest
from pathlib import Path
from dashboard_metrics import guild_metrics

class MetricsTests(unittest.TestCase):
    def test_display_reads_without_mutating_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / 'data' / 'guilds' / '123'
            d.mkdir(parents=True)
            u = d / 'users.json'
            c = d / 'config.json'
            u.write_text(json.dumps({'1': {'name': 'Alice', 'score': 5}, '2': {'name': 'Bob', 'score': 10}}))
            c.write_text(json.dumps({'event': {'enabled': True, 'multiplier': 3}}))
            before = (u.read_bytes(), c.read_bytes())
            logs = root / 'logs'
            logs.mkdir()
            (logs / 'dashboard_status.json').write_text(json.dumps({'timestamp':time.time(), 'uptime_seconds': 42, 'guilds': {'123': {'name': 'Test', 'members': 22, 'voice_users': 3}}}))
            m = guild_metrics('123', root)
            self.assertEqual(m['top'][0], ('Bob', 10))
            self.assertEqual(m['voice_users'], 3)
            self.assertTrue(m['online'])
            self.assertEqual((u.read_bytes(), c.read_bytes()), before)

    def test_stale_heartbeat_does_not_fabricate_online(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'logs').mkdir()
            (root / 'logs' / 'dashboard_status.json').write_text(json.dumps({'timestamp':time.time()-300, 'guilds':{'1':{'voice_users':55}}}))
            m = guild_metrics('1', root)
            self.assertFalse(m['online'])
            self.assertIsNone(m['voice_users'])

if __name__ == '__main__': unittest.main()
