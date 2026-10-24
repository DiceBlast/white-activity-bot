import tempfile
import unittest
from pathlib import Path
import zipfile

from release_installer import install_release


class ReleaseInstallerTests(unittest.TestCase):
    def test_replaces_code_and_preserves_local_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'bot'
            root.mkdir()
            (root / 'bot_supervisor.py').write_text('# supervisor\n', encoding='utf-8')
            (root / 'VERSION.txt').write_text('4.1.0\n', encoding='utf-8')
            (root / 'bot.py').write_text('old\n', encoding='utf-8')
            (root / 'data').mkdir()
            (root / 'data' / 'points.db').write_text('keep me', encoding='utf-8')
            archive = Path(temporary) / 'release.zip'
            with zipfile.ZipFile(archive, 'w') as package:
                package.writestr('ActivityRewards/VERSION.txt', '4.2.0\n')
                package.writestr('ActivityRewards/bot.py', 'new\n')
                package.writestr('ActivityRewards/release_installer.py', '# helper\n')
            backup = install_release(archive, root)
            self.assertEqual((root / 'bot.py').read_text(encoding='utf-8'), 'new\n')
            self.assertEqual((root / 'data' / 'points.db').read_text(encoding='utf-8'), 'keep me')
            self.assertTrue(backup.is_file())


if __name__ == '__main__':
    unittest.main()
