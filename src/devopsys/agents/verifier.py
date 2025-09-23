from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .base import Agent, AgentResult

PROMPT = """
You are a strict code compliance verifier.
Detected language: {language_name}.
Analyse the provided code with respect to the user task.

Task:
{task}

Code:
```{language_fence}
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
- ok=true only if the code directly and sufficiently satisfies the task.
- Highlight missing functionality or gaps in "missing" (empty array if none).
- Note any disallowed or useless libraries in "forbidden".
- suggested_prompt must be actionable and self-contained; if ok=true, echo the task requirement.
- Do not include extra commentary outside the JSON.
"""


MAX_CAPTURE = 2000


@dataclass
class ExecutionReport:
    language: str
    mode: str
    compilation_ok: bool
    compilation_error: Optional[str]
    stdout: str
    stderr: str
    returncode: Optional[int]
    invocation: Optional[str]


class VerifierAgent(Agent):
    name = "verifier"
    description = "Verify code compliance with the given task and suggest fixes"
    prompt_template = PROMPT

    def run(
        self,
        task: str,
        plan_context: str | None = None,
        workspace: str | None = None,
    ) -> AgentResult:
        code = workspace or ""
        meta: dict = {}
        if plan_context:
            try:
                meta = json.loads(plan_context)
            except Exception:
                meta = {}
        mode = str(meta.get("mode", "auto")).lower()
        filename = meta.get("filename")
        project_meta = meta.get("project")

        report = self._execute(
            code,
            task,
            mode=mode,
            filename=filename,
            project_meta=project_meta,
        )
        analysis = self._format_analysis(report, filename)
        prompt = self.build_prompt()
        payload = {
            "task": task.strip(),
            "code": code.strip(),
            "analysis": analysis,
            "stdout": report.stdout,
            "stderr": report.stderr,
            "language_name": report.language or "unknown",
            "language_fence": self._language_fence(report.language),
        }
        variables = {name: payload.get(name, "") for name in prompt.input_variables}
        rendered = prompt.format(**variables)
        self._debug_log(f"[agent:{self.name}] prompt:\n{self._snippet(rendered)}\n")
        raw = self.model.complete(rendered)
        self._debug_log(f"[agent:{self.name}] raw output:\n{self._snippet(raw)}\n")
        outcome = self._build_outcome(raw, report, task)
        final_text = json.dumps(outcome, ensure_ascii=False)
        return self.postprocess(final_text)

    # --- Language detection and execution helpers ---

    def _execute(
        self,
        code: str,
        task: str,
        *,
        mode: str,
        filename: str | None,
        project_meta: dict | None,
    ) -> ExecutionReport:
        mode = mode or "auto"
        if mode == "project_runtime":
            return self._execute_project_runtime(project_meta)

        language = self._detect_language(task, code, filename)
        if language == "python":
            return self._execute_python(code, mode, filename)
        if language == "bash":
            return self._execute_bash(code, mode, filename)
        if language == "dockerfile":
            return ExecutionReport(
                language=language,
                mode=mode,
                compilation_ok=True,
                compilation_error=None,
                stdout="",
                stderr="",
                returncode=None,
                invocation=None,
            )
        return ExecutionReport(
            language=language,
            mode=mode,
            compilation_ok=True,
            compilation_error=None,
            stdout="",
            stderr="",
            returncode=None,
            invocation=None,
        )

    def _detect_language(self, task: str, code: str, filename: str | None) -> str:
        task_lc = (task or "").lower()
        stripped = code.lstrip()
        first_line = stripped.splitlines()[0] if stripped else ""
        if filename:
            suffix = Path(filename).suffix.lower()
            if suffix == ".py":
                return "python"
            if suffix in {".sh", ".bash"}:
                return "bash"
            if suffix in {".toml", ".md", ".txt"}:
                return "text"
        if first_line.startswith("#!"):
            if "bash" in first_line or "sh" in first_line:
                return "bash"
            if "python" in first_line:
                return "python"
        if "dockerfile" in task_lc:
            return "dockerfile"
        if stripped.upper().startswith("FROM "):
            return "dockerfile"
        if "bash" in task_lc or "shell" in task_lc:
            return "bash"
        if "python" in task_lc or "pythonic" in task_lc:
            return "python"
        if re.search(r"\bdef\s+\w+\(", code) or "import " in code:
            return "python"
        if re.search(r"\$\{?1\b", code) or re.search(r"\bfor\s+\w+\s+in\s+\$\{?@\b", code):
            return "bash"
        return "unknown"

    def _execute_python(self, code: str, mode: str, filename: str | None) -> ExecutionReport:
        try:
            compile(code, filename or "<script>", "exec")
            compilation_ok = True
            compilation_error = None
        except SyntaxError as exc:
            details = f"SyntaxError: {exc.msg} (line {exc.lineno}, col {exc.offset})"
            compilation_ok = False
            compilation_error = details

        stdout = ""
        stderr = ""
        returncode: Optional[int] = None
        invocation = None

        if compilation_ok and mode == "syntax":
            ruff_path = shutil.which("ruff")
            if ruff_path:
                with tempfile.TemporaryDirectory() as tmpdir:
                    script_path = Path(tmpdir) / (filename or "module.py")
                    script_path.write_text(code, encoding="utf-8")
                    command = [ruff_path, "check", str(script_path), "--select=E999", "--no-cache"]
                    invocation = " ".join(shlex.quote(part) for part in command)
                    try:
                        completed = subprocess.run(  # nosec B603 B607 - intentional lint execution
                            command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            timeout=15,
                        )
                        stdout = completed.stdout.strip()[:MAX_CAPTURE]
                        stderr = completed.stderr.strip()[:MAX_CAPTURE]
                        returncode = completed.returncode
                        if completed.returncode != 0:
                            compilation_ok = False
                            compilation_error = stderr or stdout or f"ruff check failed (exit {completed.returncode})"
                    except subprocess.TimeoutExpired as exc:
                        stderr = f"ruff check timed out after {exc.timeout}s"
                        compilation_ok = False
                        compilation_error = stderr
                    except Exception as exc:  # pragma: no cover - defensive
                        stderr = f"ruff check failed: {exc}"[:500]
                        compilation_ok = False
                        compilation_error = stderr

        if compilation_ok and mode != "syntax":
            with tempfile.TemporaryDirectory() as tmpdir:
                script_path = Path(tmpdir) / "script.py"
                script_path.write_text(code, encoding="utf-8")
                command = [sys.executable or "python", str(script_path)]
                invocation = " ".join(shlex.quote(part) for part in command)
                try:
                    completed = subprocess.run(  # nosec B603 B607 - intentional execution for verification
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=10,
                    )
                    stdout = completed.stdout.strip()[:MAX_CAPTURE]
                    stderr = completed.stderr.strip()[:MAX_CAPTURE]
                    returncode = completed.returncode
                except subprocess.TimeoutExpired as exc:
                    stderr = f"Execution timed out after {exc.timeout}s"
                except Exception as exc:  # pragma: no cover - defensive
                    stderr = f"Execution failed: {exc}"[:500]

        return ExecutionReport(
            language="python",
            mode=mode,
            compilation_ok=compilation_ok,
            compilation_error=compilation_error,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            invocation=invocation,
        )

    def _execute_bash(self, code: str, mode: str, filename: str | None) -> ExecutionReport:
        stdout = ""
        stderr = ""
        returncode: Optional[int] = None
        invocation = None
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "script.sh"
            script_path.write_text(code, encoding="utf-8")
            syntax_cmd = ["bash", "-n", str(script_path)]
            syntax_invocation = " ".join(shlex.quote(part) for part in syntax_cmd)
            syntax = subprocess.run(  # nosec B603 - intentional static check
                syntax_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if syntax.returncode != 0:
                stderr = syntax.stderr.strip()[:MAX_CAPTURE]
                return ExecutionReport(
                    language="bash",
                    mode=mode,
                    compilation_ok=False,
                    compilation_error=stderr or f"bash -n failed (command: {syntax_invocation})",
                    stdout="",
                    stderr=stderr,
                    returncode=syntax.returncode,
                    invocation=syntax_invocation,
                )

            if mode != "syntax":
                sample_dir = Path(tmpdir) / "sample"
                sample_dir.mkdir(parents=True, exist_ok=True)
                (sample_dir / "file1.txt").write_text("sample", encoding="utf-8")
                (sample_dir / ".hidden").write_text("hidden", encoding="utf-8")
                needs_arg = bool(re.search(r"\$\{?1\b", code))
                command = ["bash", str(script_path)]
                if needs_arg:
                    command.append(str(sample_dir))
                invocation = " ".join(shlex.quote(part) for part in command)
                try:
                    completed = subprocess.run(  # nosec B603 B607 - intentional execution for verification
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=10,
                        cwd=tmpdir,
                        env={**os.environ, "LC_ALL": "C"},
                    )
                    stdout = completed.stdout.strip()[:MAX_CAPTURE]
                    stderr = completed.stderr.strip()[:MAX_CAPTURE]
                    returncode = completed.returncode
                except subprocess.TimeoutExpired as exc:
                    stderr = f"Execution timed out after {exc.timeout}s"
                except Exception as exc:  # pragma: no cover - defensive
                    stderr = f"Execution failed: {exc}"[:500]

        return ExecutionReport(
            language="bash",
            mode=mode,
            compilation_ok=True,
            compilation_error=None,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            invocation=invocation,
        )

    def _execute_project_runtime(self, project_meta: dict | None) -> ExecutionReport:
        if not project_meta:
            return ExecutionReport(
                language="project",
                mode="project_runtime",
                compilation_ok=False,
                compilation_error="missing project metadata",
                stdout="",
                stderr="",
                returncode=None,
                invocation=None,
            )
        root = Path(project_meta.get("root", Path.cwd()))
        entrypoint = project_meta.get("entrypoint") or ""
        args = project_meta.get("args") or []
        if not entrypoint:
            return ExecutionReport(
                language="project",
                mode="project_runtime",
                compilation_ok=False,
                compilation_error="no entrypoint defined in project.scripts",
                stdout="",
                stderr="",
                returncode=None,
                invocation=None,
            )
        if shutil.which("uv") is None:
            return ExecutionReport(
                language="project",
                mode="project_runtime",
                compilation_ok=False,
                compilation_error="uv binary not found",
                stdout="",
                stderr="",
                returncode=None,
                invocation=None,
            )
        command = ["uv", "run", entrypoint]
        if args:
            command.extend(args)
        else:
            command.append("--help")
        invocation = " ".join(shlex.quote(part) for part in command)
        try:
            completed = subprocess.run(  # nosec B603 B607 - intentional project execution
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=45,
                cwd=root,
                env={**os.environ, "CI": "1", "UV_NO_COMPILE_BYTECODE": "1"},
            )
            stdout = completed.stdout.strip()[:MAX_CAPTURE]
            stderr = completed.stderr.strip()[:MAX_CAPTURE]
            return ExecutionReport(
                language="project",
                mode="project_runtime",
                compilation_ok=True,
                compilation_error=None,
                stdout=stdout,
                stderr=stderr,
                returncode=completed.returncode,
                invocation=invocation,
            )
        except subprocess.TimeoutExpired as exc:
            stderr = f"Runtime timed out after {exc.timeout}s"
        except Exception as exc:  # pragma: no cover - defensive
            stderr = f"Runtime failed: {exc}"[:500]
        return ExecutionReport(
            language="project",
            mode="project_runtime",
            compilation_ok=False,
            compilation_error=stderr,
            stdout="",
            stderr=stderr,
            returncode=None,
            invocation=invocation,
        )

    # --- Formatting helpers ---

    def _language_fence(self, language: str | None) -> str:
        if not language:
            return "text"
        mapping = {
            "python": "python",
            "bash": "bash",
            "dockerfile": "dockerfile",
            "project": "text",
        }
        return mapping.get(language.lower(), "text")

    def _format_analysis(self, report: ExecutionReport, filename: str | None) -> str:
        details = [f"language={report.language or 'unknown'}", f"mode={report.mode}"]
        if filename:
            details.append(f"file={filename}")
        if report.compilation_ok:
            details.append("syntax=ok")
        else:
            details.append(f"syntax=fail:{report.compilation_error}")
        if report.invocation:
            details.append(f"command={report.invocation}")
        if report.returncode is not None:
            details.append(f"exit_code={report.returncode}")
        return "; ".join(filter(None, details))

    def postprocess(self, text: str) -> AgentResult:
        return AgentResult(text=text)

    def _build_outcome(self, raw: str, report: ExecutionReport, task: str) -> dict:
        text = (raw or "").strip()
        extracted = text
        if text:
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

        language = (report.language or "unknown").lower()
        lang_label = {
            "python": "Python code",
            "bash": "Bash script",
            "project": "project runtime",
        }.get(language, "code")

        if not report.compilation_ok:
            ok = False
            _append_reason(report.compilation_error or "failed static analysis")
            _add_missing(f"return syntactically valid {lang_label}")

        if report.compilation_ok and language in {"python", "bash"} and report.mode != "syntax":
            if report.returncode is None:
                ok = False
                _append_reason("script did not complete execution")
                _add_missing("ensure the script runs to completion and exits with code 0")
            elif report.returncode != 0:
                ok = False
                _append_reason(f"script exited with code {report.returncode}")
                _add_missing("ensure the script completes successfully")

        if language == "project":
            if report.returncode is None:
                ok = False
                _append_reason("project execution did not finish")
            elif report.returncode != 0:
                ok = False
                _append_reason(f"project command exited with code {report.returncode}")

        task_lc = (task or "").lower()
        stderr_lc = (report.stderr or "").lower()
        if "gpu" in task_lc:
            if report.returncode is not None and report.returncode != 0:
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
