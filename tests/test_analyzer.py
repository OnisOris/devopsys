
import unittest
from devopsys.system.analyzer import SystemAnalyzer

class TestAnalyzer(unittest.TestCase):
    def test_analyze_returns_dict(self):
        rep = SystemAnalyzer().analyze(simulate=False)
        self.assertIsInstance(rep, dict)
        self.assertIn("platform", rep)
        self.assertIn("disks", rep)
        self.assertIn("processes", rep)
