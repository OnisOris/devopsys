
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import unittest
from devopsys.router import Router

class TestRouter(unittest.TestCase):
    def test_router_docker(self):
        r = Router().classify("Собери Dockerfile для FastAPI")
        self.assertEqual(r.agent, "docker")

    def test_router_python_fallback(self):
        r = Router().classify("Напиши утилиту для обработки текстов")
        self.assertIn(r.agent, {"python", "bash"})

    def test_router_launch_task_goes_to_bash(self):
        r = Router().classify("Скрипт для запуска данного проекта")
        self.assertEqual(r.agent, "bash")

    def test_router_circle_task_goes_to_python(self):
        r = Router().classify("Скрипт, рисующий круг")
        self.assertEqual(r.agent, "python")

if __name__ == "__main__":
    unittest.main()
