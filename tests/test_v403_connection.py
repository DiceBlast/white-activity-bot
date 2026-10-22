import unittest
from connection_health import display_status, public_connection_status

class ConnectionTests(unittest.TestCase):
    def test_connected(self):
        c = public_connection_status({'state':'connected','reconnects':2,'last_ready':1}, True, 0.045, 100)
        self.assertEqual(c['latency_ms'],45)
        self.assertEqual(display_status(c,100,True,100), ('Connected',45,2))
    def test_stale_never_connected(self):
        self.assertIn('stale',display_status({'state':'connected'},10,True,100)[0])
    def test_stopped_never_connected(self):
        self.assertEqual(display_status({'state':'connected'},100,False,100)[0],'Stopped')
    def test_unknown_latency(self):
        self.assertIsNone(public_connection_status({'state':'reconnecting'},False,float('inf'),10)['latency_ms'])
    def test_no_duplicate_count_from_snapshot(self):
        conn={'state':'reconnecting','reconnects':1}
        public_connection_status(conn,False, None,10)
        public_connection_status(conn,False, None,11)
        self.assertEqual(conn['reconnects'],1)
