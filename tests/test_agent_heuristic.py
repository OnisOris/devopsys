
import unittest
from devopsys.agents.devops import _heuristic_plan

class TestAgentHeuristic(unittest.TestCase):
    def test_install_htop(self):
        steps = _heuristic_plan("установи htop")
        self.assertTrue(any("htop" in s["command"] for s in steps))
