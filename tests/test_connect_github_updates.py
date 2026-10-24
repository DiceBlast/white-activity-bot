import tempfile
import unittest
from pathlib import Path

from connect_github_updates import backup_local_state


class ConnectGitHubUpdatesTests(unittest.TestCase):
    def test_backup_preserves_local_state_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'ActivityRewards'
            root.mkdir()
            (root / 'data').mkdir()
            (root / 'data' / 'points.sqlite').write_text('points', encoding='utf-8')
            (root / 'local_credentials.json').write_text('{"token":"kept"}', encoding='utf-8')
            (root / 'bot.py').write_text('source', encoding='utf-8')
            backup = backup_local_state(root)
            self.assertTrue((backup / 'data' / 'points.sqlite').is_file())
            self.assertTrue((backup / 'local_credentials.json').is_file())
            self.assertFalse((backup / 'bot.py').exists())


if __name__ == '__main__':
    unittest.main()
