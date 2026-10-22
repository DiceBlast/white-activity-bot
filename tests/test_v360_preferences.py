import tempfile
import unittest
from pathlib import Path
from dashboard_preferences import DEFAULTS, load, save, validate

class PreferenceTests(unittest.TestCase):
    def test_defaults_and_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(load(temp), DEFAULTS)
            (Path(temp) / '.dashboard_preferences.json').write_text('{wrong')
            self.assertEqual(load(temp), DEFAULTS)
    def test_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            value = save(temp, {'refresh_seconds': 5, 'theme': 'White', 'startup_windows': 'Open guild windows'})
            self.assertEqual(load(temp), value)
            self.assertEqual(value['refresh_seconds'], 5)
            self.assertEqual(validate({'refresh_seconds': -99, 'theme': 'neon'})['theme'], 'Dark')
    def test_other_data_untouched(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / 'data' / 'users.json'
            file.parent.mkdir()
            file.write_text('DO NOT CHANGE')
            save(temp, {})
            self.assertEqual(file.read_text(), 'DO NOT CHANGE')

if __name__ == '__main__': unittest.main()
