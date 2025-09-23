from __future__ import annotations

import json
import re
import itertools
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Sequence, Optional, Tuple

import tomllib

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate

from .agents.base import Agent, AgentResult
from .agents.registry import AGENT_REGISTRY
from .langchain_support import model_runnable
from .models.base import Model
from .models.dummy import DummyModel
from .router import Router
from .workspace import build_workspace_snapshot
from .run_logger import NullRunLogger
from .project_builder import (
    ProjectSpec,
    ProjectFileSpec,
    select_agent_for_file,
    build_instruction,
    ensure_directory,
    format_plan_context_for_agent,
    summarize_created_files,
)


@dataclass
class PlanStep:
    agent: str
    instruction: str
    reason: str


@dataclass
class StepExecution:
    step: PlanStep
    result: AgentResult


@dataclass
class OrchestrationResult:
    final: AgentResult
    steps: Sequence[StepExecution]


PLAN_PROMPT = """
You are the lead planner in a LangChain multi-agent DevOps system.
Available specialized agents: {agent_names}.
Analyse the user request and break it down into an ordered plan with the minimum number of steps.
Only include an agent if its contribution is essential for the final result. Prefer a single specialist when possible.
Each plan step must be a JSON object with fields: agent, instruction, reason.
Use only the allowed agent names.

Workspace snapshot (read-only context):
{workspace}

Return a JSON object with the exact shape:
{{"plan": [{{"agent": "name", "instruction": "...", "reason": "..."}}, ...]}}

User request:
{task}
"""


def _fallback_plan(task: str) -> List[PlanStep]:
    route = Router().classify(task)
    return [PlanStep(agent=route.agent, instruction=task, reason=route.reason)]


class LeadAgent:
    def __init__(self, model: Model) -> None:
        self.model = model
        self.prompt = PromptTemplate.from_template(PLAN_PROMPT)
        self.chain = self.prompt | model_runnable(model) | StrOutputParser()

    def plan(self, task: str, workspace: str) -> List[PlanStep]:
        if isinstance(self.model, DummyModel):
            return _fallback_plan(task)

        raw = self.chain.invoke(
            {
                "agent_names": ", ".join(sorted(AGENT_REGISTRY.keys())),
                "task": task.strip(),
                "workspace": workspace,
            }
        )
        steps = self._parse_plan(raw)
        if not steps:
            return _fallback_plan(task)
        return steps

    @staticmethod
    def _parse_plan(raw: str) -> List[PlanStep]:
        text = raw.strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        candidate = match.group(0) if match else text
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            return []
        plan_items = data.get("plan") if isinstance(data, dict) else data
        result: List[PlanStep] = []
        if not isinstance(plan_items, Iterable):
            return result
        for item in plan_items:
            if not isinstance(item, dict):
                continue
            agent = item.get("agent")
            instruction = item.get("instruction") or ""
            reason = item.get("reason") or ""
            if agent in AGENT_REGISTRY:
                result.append(
                    PlanStep(
                        agent=str(agent),
                        instruction=str(instruction),
                        reason=str(reason),
                    )
                )
        return result


class MultiAgentOrchestrator:
    def __init__(
        self,
        default_model_factory: Callable[[], Model],
        logger: Optional[NullRunLogger] = None,
        *,
        planner_model_factory: Optional[Callable[[], Model]] = None,
        agent_model_factories: Optional[dict[str, Callable[[], Model]]] = None,
    ) -> None:
        # Default model used when no per-role override is provided
        self.model_factory = default_model_factory
        self.planner_model_factory = planner_model_factory
        self.agent_model_factories = agent_model_factories or {}
        self.logger = logger or NullRunLogger()

    def execute(
        self,
        task: str,
        forced_agent: str | None = None,
        os_name: str | None = None,
        project_root: str | Path | None = None,
    ) -> OrchestrationResult:
        planner_model = (self.planner_model_factory or self.model_factory)()
        planner = LeadAgent(planner_model)
        workspace_snapshot = build_workspace_snapshot()
        self.logger.on_start(task, workspace_snapshot)

        base_project_root: Path | None = None
        if project_root is not None:
            base_project_root = Path(project_root).expanduser().resolve()

        if forced_agent:
            plan = [PlanStep(agent=forced_agent, instruction=task, reason="forced by user")]
        else:
            plan = planner.plan(task, workspace_snapshot)
            plan = self._prune_plan(plan, task)
            plan = self._ensure_project_plan(task, plan)

        if not plan:
            plan = _fallback_plan(task)

        self.logger.on_plan(plan)

        executions: List[StepExecution] = []
        project_summary: AgentResult | None = None
        for step in plan:
            agent_cls = AGENT_REGISTRY.get(step.agent)
            if agent_cls is None:
                continue
            factory = self.agent_model_factories.get(step.agent, self.model_factory)
            agent_model = factory()
            agent = agent_cls(agent_model)
            instruction = step.instruction or task
            if step.agent == "linux" and os_name:
                instruction = f"[target distro: {os_name}]\n{instruction}"
            self.logger.on_agent_start(step, instruction, step.reason)
            try:
                result = agent.run(task=instruction, plan_context=step.reason, workspace=workspace_snapshot)
            except Exception as exc:  # pragma: no cover - bubble up after logging
                self.logger.on_agent_error(step, exc)
                raise
            self.logger.on_agent_end(step, result)
            executions.append(StepExecution(step=step, result=result))

            if step.agent == "project_architect":
                project_steps, summary = self._execute_project_plan(
                    architect_output=result,
                    original_task=task,
                    base_directory=base_project_root,
                )
                executions.extend(project_steps)
                if summary:
                    project_summary = summary
                continue

            if step.agent not in {"python", "verifier"}:
                verifier_exec = self._invoke_verifier(
                    task=task,
                    code=result.text,
                    reason=f"verification after {step.agent}",
                )
                if verifier_exec:
                    executions.append(verifier_exec)

            # Generic self-review-and-refine loop for Python code generation
            if step.agent == "python" and not isinstance(agent_model, DummyModel):
                extra_execs = self._review_and_refine_python(
                    original_step=step,
                    last_result=result,
                    task=task,
                    workspace=workspace_snapshot,
                )
                executions.extend(extra_execs)

        if not executions:
            factory = self.agent_model_factories.get("python", self.model_factory)
            agent_model = factory()
            agent_cls = AGENT_REGISTRY["python"]
            fallback_step = PlanStep(agent="python", instruction=task, reason="fallback to python")
            self.logger.on_agent_start(fallback_step, task, "fallback")
            result = agent_cls(agent_model).run(task=task, plan_context="fallback")
            self.logger.on_agent_end(fallback_step, result)
            executions.append(
                StepExecution(
                    step=fallback_step,
                    result=result,
                )
            )

        final_result = self._finalize(task, executions, project_summary=project_summary)
        self.logger.on_final(final_result)
        return OrchestrationResult(final=final_result, steps=executions)

    def _ensure_project_plan(self, task: str, plan: List[PlanStep]) -> List[PlanStep]:
        if any(step.agent == "project_architect" for step in plan):
            return plan
        if not self._looks_like_project_request(task):
            return plan
        return [
            PlanStep(
                agent="project_architect",
                instruction=task,
                reason="auto project generation",
            )
        ]

    def _execute_project_plan(
        self,
        *,
        architect_output: AgentResult,
        original_task: str,
        base_directory: Path | None,
    ) -> Tuple[List[StepExecution], AgentResult | None]:
        text = (architect_output.text or "").strip()
        if not text:
            raise RuntimeError("project architect produced empty plan")
        try:
            spec = ProjectSpec.from_json(text)
        except ValueError as exc:
            raise RuntimeError(f"invalid project plan: {exc}") from exc

        if not spec.files:
            raise RuntimeError("project architect returned no files to generate")

        project_root = self._allocate_project_root(spec, base_directory)
        executions: List[StepExecution] = []
        ready_files: List[str] = []

        for file_spec in spec.files:
            agent_name = select_agent_for_file(file_spec, spec)
            agent_cls = AGENT_REGISTRY.get(agent_name)
            if agent_cls is None:
                raise RuntimeError(f"no agent registered for '{agent_name}' while generating {file_spec.path}")

            factory = self.agent_model_factories.get(agent_name, self.model_factory)
            agent_model = factory()
            agent = agent_cls(agent_model)

            instruction = build_instruction(file_spec, spec)
            reason = f"project file: {file_spec.normalized_path}"
            plan_context = format_plan_context_for_agent(agent_name, file_spec, spec, ready_files)
            workspace_ctx = self._project_workspace_context(project_root, ready_files)
            plan_step = PlanStep(agent=agent_name, instruction=instruction, reason=reason)

            self.logger.on_agent_start(plan_step, instruction, plan_context)
            try:
                agent_result = agent.run(
                    task=instruction,
                    plan_context=plan_context,
                    workspace=workspace_ctx,
                )
            except Exception as exc:  # pragma: no cover - pass through after logging
                self.logger.on_agent_error(plan_step, exc)
                raise

            target_path = self._write_project_file(project_root, file_spec, agent_result.text)
            rel_display = self._relative_display(target_path, base_directory)
            stored_result = AgentResult(text=agent_result.text, filename=str(rel_display))
            self.logger.on_agent_end(plan_step, stored_result)
            executions.append(StepExecution(step=plan_step, result=stored_result))
            ready_files.append(file_spec.normalized_path)

            verifier_exec = self._invoke_verifier(
                task=f"Syntax check for {file_spec.normalized_path}",
                code=stored_result.text,
                reason=f"syntax check for {file_spec.normalized_path}",
                filename=str(target_path),
                mode="syntax",
            )
            if verifier_exec:
                executions.append(verifier_exec)

        env_step, env_message = self._maybe_create_uv_environment(spec, project_root)
        if env_step:
            executions.append(env_step)

        runtime_step, runtime_message = self._maybe_run_project_runtime(
            spec,
            project_root,
            original_task,
        )
        if runtime_step:
            executions.append(runtime_step)

        summary_lines: List[str] = []
        display_root = self._relative_display(project_root, base_directory)
        summary_lines.append(summarize_created_files(display_root, spec.files))
        project_description = spec.describe()
        if project_description:
            summary_lines.append("")
            summary_lines.append(project_description)
        if env_message:
            summary_lines.append("")
            summary_lines.append(env_message)
        if runtime_message:
            summary_lines.append(runtime_message)

        summary_text = "\n".join(line for line in summary_lines if line is not None)
        summary_result = AgentResult(text=summary_text.strip()) if summary_text.strip() else None
        return executions, summary_result

    def _allocate_project_root(self, project: ProjectSpec, base: Path | None) -> Path:
        base_dir = (base or Path.cwd()).resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        candidate = base_dir / project.slug
        if not candidate.exists():
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        for idx in itertools.count(2):
            alt = base_dir / f"{project.slug}-{idx}"
            if not alt.exists():
                alt.mkdir(parents=True, exist_ok=True)
                return alt
        raise RuntimeError("unable to allocate unique project directory")

    def _project_workspace_context(
        self,
        project_root: Path,
        ready_files: Sequence[str],
        *,
        max_files: int = 5,
        max_bytes: int = 800,
    ) -> str:
        if not ready_files:
            return ""
        lines: List[str] = []
        for rel in list(ready_files)[-max_files:]:
            path = project_root / rel
            if not path.exists() or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except (OSError, UnicodeDecodeError):
                continue
            snippet = text[:max_bytes].rstrip()
            if not snippet:
                continue
            lines.append(f"--- {rel} ---")
            lines.append(snippet)
        return "\n".join(lines).strip()

    def _write_project_file(
        self,
        project_root: Path,
        file_spec: ProjectFileSpec,
        content: str,
    ) -> Path:
        root_resolved = project_root.resolve()
        relative = Path(file_spec.normalized_path)
        target = (root_resolved / relative).resolve()
        try:
            target.relative_to(root_resolved)
        except ValueError as exc:
            raise RuntimeError(f"file path {file_spec.normalized_path} escapes project root") from exc
        ensure_directory(target)
        text = content if content.endswith("\n") else content + "\n"
        target.write_text(text, encoding="utf-8")
        return target

    def _relative_display(self, path: Path, base: Path | None) -> Path:
        target = path.resolve()
        if base is not None:
            try:
                return target.relative_to(base.resolve())
            except ValueError:
                pass
        try:
            return target.relative_to(Path.cwd())
        except ValueError:
            return target

    def _looks_like_project_request(self, task: str) -> bool:
        text = task.lower()
        hints = [
            "project",
            "проект",
            "pyproject",
            "readme",
            "package",
            "init.py",
            "src/",
            "структур",
            "каталог",
            "module",
        ]
        return any(hint in text for hint in hints)

    def _maybe_create_uv_environment(
        self,
        project: ProjectSpec,
        project_root: Path,
    ) -> Tuple[StepExecution | None, str | None]:
        if project.language.strip().lower() != "python":
            return None, None
        uv_path = shutil.which("uv")
        if not uv_path:
            return None, "Skipped uv venv setup (uv executable not found)."
        venv_dir = project_root / ".venv"
        if venv_dir.exists():
            return None, "Skipped uv venv setup (.venv already exists)."
        step = PlanStep(agent="uv", instruction="uv venv .venv", reason="create project virtualenv")
        self.logger.on_agent_start(step, step.instruction, "")
        try:
            completed = subprocess.run(  # nosec B603 B607 - intentional process execution
                [uv_path, "venv", ".venv"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=180,
                cwd=project_root,
            )
            stdout = completed.stdout.strip()
            stderr = completed.stderr.strip()
            if completed.returncode == 0:
                summary = "uv venv .venv completed successfully"
                if stdout:
                    summary += f" (stdout: {stdout[:200]})"
            else:
                summary = f"uv venv .venv failed with code {completed.returncode}"
                if stderr:
                    summary += f" (stderr: {stderr[:200]})"
        except subprocess.TimeoutExpired as exc:
            summary = f"uv venv .venv timed out after {exc.timeout}s"
        except OSError as exc:  # pragma: no cover - unlikely but defensive
            summary = f"uv venv .venv failed: {exc}"
        result = AgentResult(text=summary)
        self.logger.on_agent_end(step, result)
        return StepExecution(step=step, result=result), summary

    def _discover_entrypoint(self, project_root: Path) -> str | None:
        pyproject = project_root / "pyproject.toml"
        if not pyproject.exists():
            return None
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except Exception:
            return None
        project_data = data.get("project")
        if not isinstance(project_data, dict):
            return None
        scripts = project_data.get("scripts")
        if not isinstance(scripts, dict):
            return None
        for name, target in scripts.items():
            if isinstance(name, str) and name.strip() and isinstance(target, str) and target.strip():
                return name.strip()
        return None

    def _maybe_run_project_runtime(
        self,
        project: ProjectSpec,
        project_root: Path,
        original_task: str,
    ) -> Tuple[StepExecution | None, str | None]:
        entrypoint = self._discover_entrypoint(project_root)
        if not entrypoint:
            return None, "Runtime check skipped (no [project.scripts] entrypoint)."
        verifier_exec = self._invoke_verifier(
            task=f"Run project entrypoint {entrypoint}",
            code="",
            reason="project runtime verification",
            filename=None,
            mode="project_runtime",
            project_meta={
                "root": str(project_root),
                "entrypoint": entrypoint,
                "args": [],
                "original_task": original_task,
            },
        )
        if verifier_exec is None:
            return None, "Runtime check skipped (verifier unavailable)."
        verdict = self._parse_verifier_payload(verifier_exec.result.text)
        ok_value = verdict.get("ok")
        ok = bool(ok_value) if isinstance(ok_value, bool) else str(ok_value).lower() in {"true", "1", "yes"}
        reason = str(verdict.get("reason") or "").strip()
        if ok:
            message = "Runtime check: OK"
        else:
            message = f"Runtime check failed: {reason or 'see verifier output'}"
        return verifier_exec, message

    @staticmethod
    def _parse_verifier_payload(raw: str) -> dict:
        text = (raw or "").strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        candidate = match.group(0) if match else text
        try:
            data = json.loads(candidate)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _invoke_verifier(
        self,
        *,
        task: str,
        code: str,
        reason: str,
        filename: str | None = None,
        mode: str | None = None,
        project_meta: dict | None = None,
    ) -> StepExecution | None:
        verifier_cls = AGENT_REGISTRY.get("verifier")
        if verifier_cls is None:
            return None

        if (mode or "") != "project_runtime" and not (code or "").strip():
            return None

        factory = self.agent_model_factories.get("verifier")
        if factory is None:
            factory = self.planner_model_factory or self.model_factory
        agent_model = factory()
        verifier = verifier_cls(agent_model)
        step = PlanStep(agent="verifier", instruction=task, reason=reason)
        meta: dict = {}
        if mode:
            meta["mode"] = mode
        if filename:
            meta["filename"] = filename
        if project_meta:
            meta["project"] = project_meta
        plan_context = json.dumps(meta, ensure_ascii=False) if meta else ""
        self.logger.on_agent_start(step, task, plan_context)
        verdict = verifier.run(task=task, plan_context=plan_context, workspace=code)
        self.logger.on_agent_end(step, verdict)
        return StepExecution(step=step, result=verdict)

    # --- Self-review and refinement for Python agent ---

    def _review_and_refine_python(
        self,
        *,
        original_step: PlanStep,
        last_result: AgentResult,
        task: str,
        workspace: str,
        max_attempts: int = 6,
    ) -> List[StepExecution]:
        reviewer_model = (self.planner_model_factory or self.model_factory)()
        if isinstance(reviewer_model, DummyModel):
            return []

        verifier_available = AGENT_REGISTRY.get("verifier") is not None
        if not verifier_available:
            review_prompt = PromptTemplate.from_template(
                (
                    "You are a strict code reviewer. Assess if the Python script fulfills the task.\n"
                    "Return ONLY JSON with keys \"ok\" (boolean), \"reason\" (string), and \"missing\" (array of strings).\n"
                    "Example: {{\"ok\": false, \"reason\": \"does not draw ASCII square\", \"missing\": [\"render square\"]}}\n"
                    "Set ok=true only if the script directly implements the task without unrelated features or external calls.\n\n"
                    "Task:\n{task}\n\n"
                    "Script:\n```python\n{code}\n```\n"
                )
            )
            chain = review_prompt | model_runnable(reviewer_model) | StrOutputParser()

        def _parse_review(raw: str) -> dict:
            text = (raw or "").strip()
            m = re.search(r"\{.*\}", text, re.DOTALL)
            cand = m.group(0) if m else text
            try:
                data = json.loads(cand)
                if not isinstance(data, dict):
                    raise ValueError("review JSON not object")
                if "ok" not in data:
                    raise ValueError("review JSON missing ok")
                if not isinstance(data.get("missing"), list):
                    data["missing"] = []
                reason = data.get("reason")
                if not isinstance(reason, str) or not reason.strip():
                    reasons = data.get("reasons")
                    if isinstance(reasons, list) and reasons:
                        data["reason"] = "; ".join(str(item) for item in reasons if item)
                    else:
                        data.setdefault("reason", "")
                suggested = data.get("suggested_prompt")
                if suggested is not None and not isinstance(suggested, str):
                    data["suggested_prompt"] = ""
                forbidden = data.get("forbidden")
                if forbidden is not None and not isinstance(forbidden, list):
                    data["forbidden"] = []
                return data
            except (json.JSONDecodeError, ValueError):
                return {"ok": False, "reason": "review parse error", "missing": []}

        attempts: List[StepExecution] = []
        current_result = last_result
        last_outcome: dict | None = None

        task_lc = (task or "").lower()
        gpu_refinement_hint = (
            "When gathering GPU utilisation, call subprocess.run("
            "['nvidia-smi', '--query-gpu=utilization.gpu', "
            "'--format=csv,noheader,nounits'], capture_output=True, text=True, check=False) without shell=True. "
            "Gracefully handle FileNotFoundError or non-zero return codes by printing a clear message "
            "and exiting cleanly (use sys.exit(0) after the message). "
            "Provide one line per GPU with its utilisation or an informative fallback when data is unavailable."
        )
        directory_refinement_hint = (
            "List directories via os.scandir(path) filtered with entry.is_dir(). "
            "Expose an argparse --path argument defaulting to '.', sort the resulting folder names, "
            "and print them one per line."
        )
        dynamic_max_attempts = 8 if ("matplotlib" in task_lc) else max_attempts

        for attempt_idx in range(1, dynamic_max_attempts + 1):
            audit_ok, audit_reason, audit_missing, _ = self._static_python_audit(
                task=task,
                code=current_result.text,
            )

            if verifier_available:
                verifier_exec = self._invoke_verifier(
                    task=task,
                    code=current_result.text,
                    reason="code compliance check",
                )
                if verifier_exec:
                    attempts.append(verifier_exec)
                    outcome = _parse_review(verifier_exec.result.text)
                else:
                    outcome = {"ok": False, "reason": "verifier unavailable", "missing": []}
            else:
                if not audit_ok:
                    outcome = {"ok": False, "reason": audit_reason, "missing": audit_missing}
                else:
                    raw = chain.invoke({"task": task, "code": current_result.text})
                    outcome = _parse_review(raw)
            last_outcome = outcome

            ok_value = outcome.get("ok", True)
            ok = bool(ok_value) if isinstance(ok_value, bool) else str(ok_value).strip().lower() in {"true", "1", "yes"}
            if ok:
                break

            reason = outcome.get("reason", "")
            missing = outcome.get("missing") or []
            missing_list = [str(item) for item in missing if item]
            feedback = reason.strip() if isinstance(reason, str) and reason.strip() else "does not fulfill the task"
            missing_section = ""
            if missing_list:
                missing_section = "\nRequired fixes:\n" + "\n".join(f"- {item}" for item in missing_list)

            suggested_prompt = outcome.get("suggested_prompt")
            base_instruction = (
                suggested_prompt.strip()
                if isinstance(suggested_prompt, str) and suggested_prompt.strip()
                else original_step.instruction
            )

            refined_parts = [base_instruction.strip()]
            refined_parts.append(
                f"Previous attempt did not fulfill the task: {feedback}."
                + (missing_section if missing_section else "")
            )
            refined_parts.append("Regenerate from scratch. Output ONLY Python code (no markdown/prose).")
            refined_parts.append("Use only the standard library unless explicitly requested.")

            reason_lc = reason.lower() if isinstance(reason, str) else ""
            combined_missing = " ".join(missing_list).lower()

            needs_gpu_hint = any(
                key in value
                for value in (task_lc, reason_lc, combined_missing)
                for key in ("gpu", "nvidia-smi", "cuda")
            )
            if needs_gpu_hint and not any("nvidia-smi" in part.lower() for part in refined_parts):
                refined_parts.append(gpu_refinement_hint)

            dir_keywords = ("directory", "directories", "folder", "folders")
            needs_directory_hint = any(keyword in task_lc for keyword in dir_keywords) or any(
                keyword in value for value in (reason_lc, combined_missing) for keyword in dir_keywords
            )
            if needs_directory_hint and not any("scandir" in part.lower() for part in refined_parts):
                refined_parts.append(directory_refinement_hint)
            refined_instruction = "\n".join(part.strip() for part in refined_parts if part.strip()) + "\n"

            agent_model = self.agent_model_factories.get("python", self.model_factory)()
            agent_cls = AGENT_REGISTRY["python"]
            agent = agent_cls(agent_model)
            refined_step = PlanStep(
                agent="python",
                instruction=refined_instruction,
                reason=f"refinement attempt {attempt_idx}",
            )
            self.logger.on_agent_start(refined_step, refined_instruction, refined_step.reason)
            try:
                refined_result = agent.run(task=refined_instruction, plan_context=refined_step.reason, workspace=workspace)
            except Exception as exc:  # pragma: no cover - propagate
                self.logger.on_agent_error(refined_step, exc)
                raise
            self.logger.on_agent_end(refined_step, refined_result)
            attempts.append(StepExecution(step=refined_step, result=refined_result))
            current_result = refined_result

        if verifier_available:
            needs_final = True
            if attempts and attempts[-1].step.agent == "verifier":
                parsed_last = _parse_review(attempts[-1].result.text)
                if parsed_last.get("ok") is True:
                    needs_final = False
            if needs_final:
                verifier_exec = self._invoke_verifier(
                    task=task,
                    code=current_result.text,
                    reason="final verification",
                )
                if verifier_exec:
                    attempts.append(verifier_exec)

        return attempts
    @staticmethod
    def _static_python_audit(
        task: str,
        code: str,
    ) -> tuple[bool, str, list[str], dict[str, bool]]:
        try:
            import ast as _ast
            _ast.parse(code or "")
        except SyntaxError as _exc:
            return (
                False,
                f"invalid python syntax: {_exc.msg}",
                ["return valid Python code"],
                {"syntax_error": True},
            )
        return True, "", [], {}

    def _prune_plan(self, plan: List[PlanStep], task: str) -> List[PlanStep]:
        if not plan:
            return plan

        route = Router().classify(task)

        if route.agent == "docker":
            docker_steps = [step for step in plan if step.agent == "docker"]
            if docker_steps:
                return docker_steps

        ordered: List[PlanStep] = []
        seen = set()

        for step in plan:
            if step.agent == route.agent and step.agent not in seen:
                ordered.append(step)
                seen.add(step.agent)

        for step in plan:
            if step.agent not in seen:
                ordered.append(step)
                seen.add(step.agent)

        return ordered

    def _finalize(
        self,
        task: str,
        executions: Sequence[StepExecution],
        *,
        project_summary: AgentResult | None = None,
    ) -> AgentResult:
        if project_summary is not None:
            return project_summary
        if len(executions) == 1:
            return executions[0].result

        # Compliance-gated finalize: prefer last verifier verdict
        def _parse_verdict(raw: str) -> dict:
            txt = (raw or "").strip()
            import re, json as _json
            m = re.search(r"\{.*\}", txt, re.DOTALL)
            cand = m.group(0) if m else txt
            try:
                data = _json.loads(cand)
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}

        last_verifier_idx: int | None = None
        for idx in range(len(executions) - 1, -1, -1):
            if executions[idx].step.agent == "verifier":
                last_verifier_idx = idx
                break

        if last_verifier_idx is not None:
            verifier_exec = executions[last_verifier_idx]
            verifier_result = verifier_exec.result
            verdict = _parse_verdict(verifier_result.text)
            candidate_result: AgentResult | None = None
            for j in range(last_verifier_idx - 1, -1, -1):
                if executions[j].step.agent == "verifier":
                    continue
                candidate_result = executions[j].result
                break

            if verdict.get("ok") is True and candidate_result is not None:
                if candidate_result.filename:
                    return AgentResult(text=candidate_result.text, filename=candidate_result.filename)
                return AgentResult(text=candidate_result.text)

            if candidate_result is not None:
                combined = candidate_result.text
                verifier_text = verifier_result.text.strip()
                if verifier_text:
                    suffix = f"\n\n[verifier]\n{verifier_text}"
                    combined = combined.rstrip() + suffix
                return AgentResult(text=combined, filename=candidate_result.filename)

            return AgentResult(text=verifier_result.text)

        for exec_step in reversed(executions):
            if exec_step.step.agent == "python":
                return AgentResult(text=exec_step.result.text)

        sections = []
        for exec_step in executions:
            heading = f"### {exec_step.step.agent}\nReason: {exec_step.step.reason}\n"
            sections.append(f"{heading}\n{exec_step.result.text.strip()}\n")
        combined = "\n".join(sections).strip()
        return AgentResult(text=f"Task: {task}\n\n{combined}")
