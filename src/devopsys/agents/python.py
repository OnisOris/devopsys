from __future__ import annotations
from .base import Agent, AgentResult
from .python_utils import normalise_python_output

PROMPT = """
You are a senior Python engineer working in a multi-agent team.
Task:
{task}

Additional context (may be empty):
{plan_context}

Constraints:
- Implement exactly what the task requests; avoid unrelated features.
- Write clean, idiomatic Python 3.11+ using only the standard library. Never import third-party packages unless the task explicitly names them.
- Always include a main() and an if __name__ == "__main__": guard.
- If the task implies command-line usage, use minimal argparse.
- Return ONLY executable Python code. No Markdown, prose, or explanations.
"""

class PythonAgent(Agent):
    name = "python"
    description = "Generate Python scripts"
    prompt_template = PROMPT

    def __init__(self, model) -> None:
        super().__init__(model)
        self._last_task: str = ""

    def run(self, task: str, plan_context: str | None = None, workspace: str | None = None) -> AgentResult:
        self._last_task = task
        return super().run(task=task, plan_context=plan_context, workspace="")

    def postprocess(self, text: str) -> AgentResult:
        # Pass raw model output to the normaliser which handles Markdown fences,
        # trailing explanations and syntax validation/fixes.
        code = normalise_python_output(text, self._last_task)
        # Decide whether to assign a filename only if code is syntactically valid
        is_valid = False
        try:
            import ast as _ast
            _ast.parse(code or "")
            is_valid = True
        except SyntaxError:
            is_valid = False
        if "Generated (dummy backend)" in (code or ""):
            is_valid = True
        filename = "script.py" if is_valid else None
        lowered_task = (self._last_task or "").lower()
        if is_valid and "main.py" in lowered_task:
            filename = "main.py"
        return AgentResult(text=code, filename=filename)
