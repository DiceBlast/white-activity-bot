import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dashboard_metrics import _load_json, _JSON_CACHE

class PerformanceTests(unittest.TestCase):
    def test_cache_reuses_unchanged_files_and_detects_updates(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'metrics.json'
            path.write_text('{"x":1}', encoding='utf-8')
            _JSON_CACHE.clear()
            first = _load_json(path, {})
            self.assertIs(first, _load_json(path, {}))
            path.write_text('{"x": 200}', encoding='utf-8')
            self.assertEqual(_load_json(path, {})['x'], 200)
    def test_missing_or_invalid_files_never_reuse_stale_results(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'metrics.json'
            path.write_text('{"ok": true}', encoding='utf-8')
            self.assertTrue(_load_json(path, {})['ok'])
            path.unlink()
            self.assertEqual(_load_json(path, {}), {})
            path.write_text('bad json', encoding='utf-8')
            self.assertEqual(_load_json(path, {}), {})
