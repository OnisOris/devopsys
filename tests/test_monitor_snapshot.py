
import unittest
from devopsys.monitor.stream import snapshot

class TestMonitorSnapshot(unittest.TestCase):
    def test_snapshot_df(self):
        out = snapshot(["df"])
        self.assertIn("df -h", out)
