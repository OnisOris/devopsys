
import unittest, tempfile, pathlib
from devopsys.config import Config, save_to_env

class TestConfigSave(unittest.TestCase):
    def test_save_roundtrip(self):
        cfg = Config.from_env()
        cfg.safe_mode = False
        cfg.monitor_sources = ["top","free"]
        cfg.monitor_interval_sec = 15
        p = pathlib.Path(tempfile.gettempdir()) / "devopsys_test.env"
        save_to_env(cfg, str(p))
        txt = p.read_text()
        self.assertIn("DEVOPSYS_SAFE=false", txt)
        self.assertIn("DEVOPSYS_MONITOR_SOURCES=top,free", txt)
        self.assertIn("DEVOPSYS_MONITOR_INTERVAL=15", txt)
