import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analytics_history import record_snapshot, history_for_guild

class HistoryTests(unittest.TestCase):
    def test_guild_history_isolated_and_throttled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record_snapshot(root, {'timestamp': __import__('time').time(), 'guilds': {'1': {'voice_users': 3, 'members': 9}, '2': {'voice_users': 1, 'members': 8}}})
            self.assertEqual(history_for_guild('1', root)[0][1:], (3, 9))
            self.assertEqual(history_for_guild('2', root)[0][1:], (1, 8))
            self.assertEqual(history_for_guild('3', root), [])
            self.assertFalse((root / 'data').exists())
