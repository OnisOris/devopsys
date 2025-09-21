from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import json

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
        outcome = self._build_outcome(raw, report, task)
        final_text = json.dumps(outcome, ensure_ascii=False)
        return self.postprocess(final_text)

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

    def _build_outcome(self, raw: str, report: ExecutionReport, task: str) -> dict:
        text = (raw or "").strip()
        extracted = text
        if text:
            import re
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                extracted = match.group(0)
        try:
            data = json.loads(extracted)
            if not isinstance(data, dict):
                raise ValueError("verdict is not object")
        except Exception:
            data = {
                "ok": False,
                "reason": "verifier response not understood",
                "missing": [],
                "forbidden": [],
                "suggested_prompt": "Regenerate the script to satisfy the task.",
            }

        reason = data.get("reason") or ""
        if not isinstance(reason, str):
            reason = str(reason)
        missing = data.get("missing") or []
        if not isinstance(missing, list):
            missing = [str(missing)]
        forbidden = data.get("forbidden") or []
        if not isinstance(forbidden, list):
            forbidden = [str(forbidden)]
        suggested = data.get("suggested_prompt") or ""
        if not isinstance(suggested, str):
            suggested = str(suggested)
        ok_raw = data.get("ok")
        if isinstance(ok_raw, bool):
            ok = ok_raw
        elif isinstance(ok_raw, str):
            ok = ok_raw.strip().lower() in {"true", "1", "yes"}
        else:
            ok = bool(ok_raw)

        def _append_reason(msg: str) -> None:
            nonlocal reason
            if msg:
                reason = f"{reason}; {msg}" if reason else msg

        def _add_missing(msg: str) -> None:
            if msg and msg not in missing:
                missing.append(msg)

        if not report.compilation_ok:
            ok = False
            _append_reason(report.compilation_error or "failed to compile")
            _add_missing("return syntactically valid Python code")
        if report.compilation_ok:
            if report.returncode is None:
                ok = False
                _append_reason("script did not produce an exit code")
                _add_missing("ensure the script runs to completion and exits with code 0")
            elif report.returncode != 0:
                ok = False
                _append_reason(f"script exited with code {report.returncode}")
                _add_missing("ensure the script completes successfully")

        task_lc = (task or "").lower()
        stderr_lc = report.stderr.lower()
        if "gpu" in task_lc:
            if report.returncode != 0:
                _add_missing("handle unavailable GPU or missing nvidia-smi gracefully")
            if ok and not report.stdout.strip():
                ok = False
                _append_reason("GPU output was empty")
                _add_missing("print the current GPU usage when available")
            if "nvidia-smi" in stderr_lc and any(hint in stderr_lc for hint in ("not found", "no such file")):
                ok = False
                _append_reason("nvidia-smi command is unavailable")
                _add_missing("detect missing nvidia-smi and emit a friendly message without failing")

        outcome = {
            "ok": ok,
            "reason": reason.strip(),
            "missing": missing,
            "forbidden": forbidden,
            "suggested_prompt": suggested.strip() or "Regenerate the script to satisfy the task.",
        }
        return outcome
