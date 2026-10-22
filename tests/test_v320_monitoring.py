import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from dashboard_metrics import event_schedule, guild_metrics, recent_reward_activity

class NewMonitorTests(unittest.TestCase):
    def test_schedule_countdown(self):
        cfg = {'timezone':'UTC', 'event': {'enabled': True, 'multiplier':2,'start_day':'Friday','start_time':'18:00','end_day':'Monday','end_time':'00:00'}}
        state = event_schedule(cfg, datetime(2026,10,16,17,0,tzinfo=ZoneInfo('UTC')))
        self.assertFalse(state['active'])
        self.assertEqual(state['seconds_left'], 3600)
        state = event_schedule(cfg, datetime(2026,10,16,19,0,tzinfo=ZoneInfo('UTC')))
        self.assertTrue(state['active'])
    def test_session_and_reward_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'logs').mkdir()
            (root/'logs'/'dashboard_status.json').write_text(json.dumps({'timestamp':time.time(),'guilds':{'7':{'tracked_sessions':[{'name':'M','channel':'General','started_at':time.time()-60}]}}}))
            lines='Reward sent | guild_id=7 user_id=4 normal=1\nReward sent | guild_id=8 user_id=3 normal=5\n'
            (root/'logs'/'bot.log').write_text(lines)
            before=(root/'logs'/'dashboard_status.json').read_bytes()
            self.assertEqual(guild_metrics('7',root)['sessions'][0]['name'],'M')
            self.assertEqual(len(recent_reward_activity('7',root)),1)
            self.assertEqual(before,(root/'logs'/'dashboard_status.json').read_bytes())

if __name__ == '__main__': unittest.main()
