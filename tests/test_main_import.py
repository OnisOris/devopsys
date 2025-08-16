
import unittest
import importlib
class TestMainImport(unittest.TestCase):
    def test_import(self):
        m = importlib.import_module("devopsys.main")
        self.assertTrue(hasattr(m, "cli_main"))
