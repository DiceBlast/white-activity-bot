import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import release_preflight


class ReleasePreflightTests(unittest.TestCase):
    def test_preflight_on_package_without_credentials_is_read_only(self):
        root = release_preflight.ROOT
        before = sorted(str(p.relative_to(root)) for p in root.iterdir() if p.is_file())
        results = release_preflight.inspect_install(root, check_dependencies=False)
        after = sorted(str(p.relative_to(root)) for p in root.iterdir() if p.is_file())
        self.assertEqual(before, after)
        self.assertTrue(all(ok for ok, label in results if label.startswith('Python parse:')))
        self.assertEqual(
            len([x for x in results if 'Current release notes: README_V403.txt' in x[1]]),
            1,
        )

    def test_missing_code_reported(self):
        with tempfile.TemporaryDirectory() as td:
            result = release_preflight.inspect_install(td, check_dependencies=False)
            self.assertTrue(any(not ok and 'bot.py' in label for ok, label in result))

    def test_invalid_python_reported(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, 'broken.py').write_text('def :(\n')
            result = release_preflight.inspect_install(td, check_dependencies=False)
            self.assertTrue(any(not ok and 'broken.py' in label for ok, label in result))


if __name__ == '__main__':
    unittest.main()
