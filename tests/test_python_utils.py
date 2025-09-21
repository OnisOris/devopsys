import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from devopsys.agents.python_utils import normalise_python_output


SAMPLE_INVALID_PY_OUTPUT = """The provided code is a Python project."""


class PythonUtilsTestCase(unittest.TestCase):
    def test_invalid_code_returns_placeholder_even_for_specific_tasks(self):
        task = "Скрипт python, рисующий круг"
        code = normalise_python_output(SAMPLE_INVALID_PY_OUTPUT, task)
        self.assertIn("Python scaffold for task", code)
        self.assertIn(task, code)

    def test_generic_placeholder_for_unknown_task(self):
        task = "Неизвестная задача"
        code = normalise_python_output(SAMPLE_INVALID_PY_OUTPUT, task)
        self.assertIn("Python scaffold for task", code)
        self.assertIn(task, code)

    def test_valid_code_preserved(self):
        snippet = """
def main():
    print("ok")


if __name__ == "__main__":
    main()
"""
        code = normalise_python_output(snippet, "any")
        self.assertEqual(code.strip(), snippet.strip())


if __name__ == "__main__":
    unittest.main()
