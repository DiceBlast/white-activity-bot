import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analytics_reports import load_samples, summarize, export_csv

class ReportsTest(unittest.TestCase):
    def test_ranges_and_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'logs'/'analytics_history.jsonl'
            p.parent.mkdir()
            data=[{'timestamp':100,'guilds':{'1':{'voice':2,'members':10},'2':{'voice':4,'members':20}}},
                  {'timestamp':100000,'guilds':{'1':{'voice':6,'members':12}}}]
            p.write_text(''.join(json.dumps(x)+'\n' for x in data))
            self.assertEqual(len(load_samples(d,'1','All history')),2)
            self.assertEqual(len(load_samples(d,'1','24 hours',now=200000)),0)
            self.assertEqual(len(load_samples(d,'2','All history')),1)
            self.assertEqual(summarize(load_samples(d,'1','All history'))['avg_voice'],4)
    def test_export_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'report.csv'
            export_csv(target, [(1000,2,10)], '=unsafe', '1', 'All history')
            self.assertIn("'=unsafe", target.read_text(encoding='utf-8-sig'))
            with self.assertRaises(FileExistsError):
                export_csv(target, [(1000,2,10)], '=unsafe', '1', 'All history')
