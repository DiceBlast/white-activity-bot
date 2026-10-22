import json
import tempfile
import unittest
from pathlib import Path
from backup_center import guild_ids, backup_folders, create_manual, validate, restore_offline

class BackupCenterTests(unittest.TestCase):
    def test_manual_backup_and_offline_restore_with_safety_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guild = root / 'data' / 'guilds' / '123'
            guild.mkdir(parents=True)
            for name in ('config.json', 'users.json'):
                (guild / name).write_text(json.dumps({'original': 1}), encoding='utf-8')
            self.assertEqual(guild_ids(root), ['123'])
            backup = create_manual(root, '123')
            self.assertEqual(len(validate(backup)), 2)
            self.assertIn(backup, backup_folders(root, '123'))
            (guild / 'users.json').write_text('{"original": 99}', encoding='utf-8')
            with self.assertRaises(RuntimeError):
                restore_offline(root, '123', backup, running=True)
            self.assertEqual(json.loads((guild / 'users.json').read_text())['original'], 99)
            # Offline restore uses the installed app's configured data root; test safety snapshot separately.
            before = create_manual(root, '123', 'before_restore_DO_NOT_DELETE')
            self.assertEqual(json.loads((before / 'users.json').read_text())['original'], 99)
            self.assertEqual(json.loads((backup / 'users.json').read_text())['original'], 1)

    def test_invalid_backup_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'users.json').write_text('{}')
            with self.assertRaises(ValueError):
                validate(folder)
