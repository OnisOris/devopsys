import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from devopsys.agents.bash_utils import normalise_bash_output


SAMPLE_INVALID_OUTPUT = """[PYTHON]
def get_bash_script(input_string: str) -> str:
    result = '#!/usr/bin/env bash\n'
    result += '\n'
    return "'" + input_string + "'\n"
[/PYTHON]
"""


class BashUtilsTestCase(unittest.TestCase):
    def test_rsync_fallback_used_for_invalid_response(self):
        task = "Скрипт на bash для rsync бэкапа"
        script = normalise_bash_output(SAMPLE_INVALID_OUTPUT, task)
        self.assertTrue(script.startswith("#!/usr/bin/env bash\n"))
        self.assertIn("set -euo pipefail", script)
        self.assertIn("rsync", script)
        self.assertNotIn("[PYTHON]", script)

    def test_valid_script_is_preserved(self):
        valid = "#!/usr/bin/env bash\nset -euo pipefail\nusage() { :; }\n"
        task = "любая задача"
        script = normalise_bash_output(valid, task)
        self.assertEqual(script, valid)

    def test_project_runner_fallback_for_launch_requests(self):
        task = "Скрипт для запуска данного проекта в папке"
        script = normalise_bash_output(SAMPLE_INVALID_OUTPUT, task)
        self.assertTrue(script.startswith("#!/usr/bin/env bash\n"))
        self.assertIn("uv venv", script)
        self.assertIn("uv pip install", script)
        self.assertIn("DEVOPSYS_BACKEND", script)
        self.assertIn("Running sample devopsys ask", script)

    def test_circle_drawing_fallback(self):
        task = "Скрипт, рисующий круг"
        script = normalise_bash_output(SAMPLE_INVALID_OUTPUT, task)
        self.assertTrue(script.startswith("#!/usr/bin/env bash\n"))
        self.assertIn("draw_circle", script)
        self.assertIn("awk", script)
        self.assertIn("Circle rendered", script)

    def test_generic_placeholder_when_no_match(self):
        task = "Непонятная уникальная задача"
        script = normalise_bash_output(SAMPLE_INVALID_OUTPUT, task)
        self.assertTrue(script.startswith("#!/usr/bin/env bash\n"))
        self.assertIn("TODO: Implement the following task", script)
        self.assertIn(task, script)


if __name__ == "__main__":
    unittest.main()
