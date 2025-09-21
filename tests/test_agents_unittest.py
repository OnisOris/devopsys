
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import unittest
from devopsys.models.dummy import DummyModel
from devopsys.agents.docker import DockerAgent
from devopsys.agents.python import PythonAgent
from devopsys.agents.rust import RustAgent
from devopsys.agents.bash import BashAgent
from devopsys.agents.linux import LinuxAgent

def _nonempty(text: str) -> bool:
    return bool(text and text.strip())

class TestAgents(unittest.TestCase):
    def setUp(self):
        self.m = DummyModel()

    def test_docker(self):
        a = DockerAgent(self.m)
        out = a.run("Dockerfile для Python 3.11")
        self.assertTrue(_nonempty(out.text))
        self.assertEqual(out.filename, "Dockerfile")

    def test_python(self):
        a = PythonAgent(self.m)
        out = a.run("Пример задачи")
        self.assertTrue(_nonempty(out.text))
        self.assertEqual(out.filename, "script.py")

    def test_rust(self):
        a = RustAgent(self.m)
        out = a.run("Пример задачи")
        self.assertTrue(_nonempty(out.text))
        self.assertEqual(out.filename, "rust_project.txt")

    def test_bash(self):
        a = BashAgent(self.m)
        out = a.run("Пример задачи")
        self.assertTrue(_nonempty(out.text))
        self.assertEqual(out.filename, "script.sh")

    def test_linux(self):
        a = LinuxAgent(self.m)
        out = a.run("Настроить docker на Ubuntu")
        self.assertTrue(_nonempty(out.text))
        self.assertEqual(out.filename, "linux_setup.txt")

if __name__ == "__main__":
    unittest.main()
