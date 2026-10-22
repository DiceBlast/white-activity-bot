import json
import tempfile
import unittest
from pathlib import Path
from backup_center import create_manual, delete_backup

class BackupDeleteTests(unittest.TestCase):
    def test_deletes_only_selected_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guild = root/'data'/'guilds'/'123'
            guild.mkdir(parents=True)
            for name in ('config.json', 'users.json'):
                (guild/name).write_text(json.dumps({'keep':True}))
            first = create_manual(root, '123')
            second = create_manual(root, '123')
            delete_backup(root, '123', first)
            self.assertFalse(first.exists())
            self.assertTrue(second.exists())
            self.assertTrue((guild/'users.json').exists())

    def test_safety_backup_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guild = root/'data'/'guilds'/'123'
            guild.mkdir(parents=True)
            for name in ('config.json', 'users.json'):
                (guild/name).write_text('{}')
            safety = create_manual(root, '123', 'before_restore_DO_NOT_DELETE')
            with self.assertRaises(ValueError):
                delete_backup(root, '123', safety)
            self.assertTrue(safety.exists())

    def test_reject_other_guild_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guild = root/'data'/'guilds'/'123'
            guild.mkdir(parents=True)
            for name in ('config.json', 'users.json'):
                (guild/name).write_text('{}')
            backup = create_manual(root, '123')
            with self.assertRaises(ValueError):
                delete_backup(root, '999', backup)
            self.assertTrue(backup.exists())
