from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .base import Agent, AgentResult
from ..langchain_support import model_runnable
from langchain_core.output_parsers import StrOutputParser


PROMPT = """
You are a strict code compliance verifier.
Analyse the provided Python script with respect to the user task.

Task:
{task}

Script:
```python
{code}
```

Static analysis:
{analysis}

Execution (stdout):
{stdout}

Execution (stderr):
{stderr}

Return ONLY a JSON object with fields:
{{
  "ok": true|false,
  "reason": "...",
  "missing": ["..."],
  "forbidden": ["..."],
  "suggested_prompt": "A single paragraph instruction to regenerate compliant code"
}}

Rules:
- ok=true only if the script directly and sufficiently satisfies the task.
- Highlight missing functionality or gaps in "missing" (empty array if none).
- Note any disallowed or useless libraries in "forbidden".
- suggested_prompt must be actionable and self-contained; if ok=true, echo the task requirement.
- Do not include extra commentary outside the JSON.
"""


@dataclass
class ExecutionReport:
    compilation_ok: bool
    compilation_error: Optional[str]
    stdout: str
    stderr: str
    returncode: Optional[int]


class VerifierAgent(Agent):
    name = "verifier"
    description = "Verify code compliance with the given task and suggest fixes"
    prompt_template = PROMPT

    def run(self, task: str, plan_context: str | None = None, workspace: str | None = None) -> AgentResult:
        code = workspace or ""
        report = self._execute(code)
        analysis = self._format_analysis(report)
        prompt = self.build_prompt()
        payload = {
            "task": task.strip(),
            "code": code.strip(),
            "analysis": analysis,
            "stdout": report.stdout,
            "stderr": report.stderr,
        }
        variables = {name: payload.get(name, "") for name in prompt.input_variables}
        rendered = prompt.format(**variables)
        self._debug_log(f"[agent:{self.name}] prompt:\n{self._snippet(rendered)}\n")
        raw = self.model.complete(rendered)
        self._debug_log(f"[agent:{self.name}] raw output:\n{self._snippet(raw)}\n")
        return self.postprocess(raw)

    def _execute(self, code: str) -> ExecutionReport:
        if not code.strip():
            return ExecutionReport(False, "empty script", "", "", None)
        try:
            compile(code, "<script>", "exec")
            compilation_ok = True
            compilation_error = None
        except SyntaxError as exc:
            details = f"SyntaxError: {exc.msg} (line {exc.lineno}, col {exc.offset})"
            compilation_ok = False
            compilation_error = details

        stdout = ""
        stderr = ""
        returncode: Optional[int] = None
        if compilation_ok:
            with tempfile.TemporaryDirectory() as tmpdir:
                script_path = Path(tmpdir) / "script.py"
                script_path.write_text(code, encoding="utf-8")
                try:
                    completed = subprocess.run(  # nosec B603 B607 - intentional execution for verification
                        ["python", str(script_path)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=5,
                    )
                    stdout = completed.stdout.strip()[:2000]
                    stderr = completed.stderr.strip()[:2000]
                    returncode = completed.returncode
                except subprocess.TimeoutExpired as exc:
                    stderr = f"Execution timed out after {exc.timeout}s"
                except Exception as exc:  # pragma: no cover - defensive
                    stderr = f"Execution failed: {exc}"[:500]

        return ExecutionReport(compilation_ok, compilation_error, stdout, stderr, returncode)

    def _format_analysis(self, report: ExecutionReport) -> str:
        if not report.compilation_ok:
            return report.compilation_error or "compilation failed"
        details = []
        if report.returncode is not None:
            details.append(f"exit code: {report.returncode}")
        return "; ".join(filter(None, details))

    def postprocess(self, text: str) -> AgentResult:
        return AgentResult(text=text)
