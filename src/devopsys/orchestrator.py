from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Iterable, List, Sequence, Optional

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
    ) -> OrchestrationResult:
        planner_model = (self.planner_model_factory or self.model_factory)()
        planner = LeadAgent(planner_model)
        workspace_snapshot = build_workspace_snapshot()
        self.logger.on_start(task, workspace_snapshot)

        if forced_agent:
            plan = [PlanStep(agent=forced_agent, instruction=task, reason="forced by user")]
        else:
            plan = planner.plan(task, workspace_snapshot)
            plan = self._prune_plan(plan, task)

        if not plan:
            plan = _fallback_plan(task)

        self.logger.on_plan(plan)

        executions: List[StepExecution] = []
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

        final_result = self._finalize(task, executions)
        self.logger.on_final(final_result)
        return OrchestrationResult(final=final_result, steps=executions)

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

        verifier_agent_cls = AGENT_REGISTRY.get("verifier")
        use_verifier = verifier_agent_cls is not None
        if not use_verifier:
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
        dynamic_max_attempts = 8 if ("matplotlib" in task_lc) else max_attempts

        for attempt_idx in range(1, dynamic_max_attempts + 1):
            audit_ok, audit_reason, audit_missing, _ = self._static_python_audit(
                task=task,
                code=current_result.text,
            )

            if use_verifier:
                agent_model = self.agent_model_factories.get("verifier", self.planner_model_factory or self.model_factory)()
                verifier = verifier_agent_cls(agent_model)
                step = PlanStep(agent="verifier", instruction=task, reason="code compliance check")
                self.logger.on_agent_start(step, task, step.reason)
                verdict = verifier.run(task=task, plan_context="", workspace=current_result.text)
                self.logger.on_agent_end(step, verdict)
                attempts.append(StepExecution(step=step, result=verdict))
                outcome = _parse_review(verdict.text)
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

        if use_verifier:
            needs_final = True
            if attempts and attempts[-1].step.agent == "verifier":
                parsed_last = _parse_review(attempts[-1].result.text)
                if parsed_last.get("ok") is True:
                    needs_final = False
            if needs_final:
                agent_model = self.agent_model_factories.get("verifier", self.planner_model_factory or self.model_factory)()
                verifier = verifier_agent_cls(agent_model)
                final_step = PlanStep(agent="verifier", instruction=task, reason="final verification")
                self.logger.on_agent_start(final_step, task, final_step.reason)
                verdict = verifier.run(task=task, plan_context="", workspace=current_result.text)
                self.logger.on_agent_end(final_step, verdict)
                attempts.append(StepExecution(step=final_step, result=verdict))

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

    def _finalize(self, task: str, executions: Sequence[StepExecution]) -> AgentResult:
        if len(executions) == 1:
            return executions[0].result

        # Prefer the last file result that passes our static audit (for python).
        for exec_step in reversed(executions):
            if exec_step.result.filename and exec_step.step.agent == "python":
                ok, _reason, _missing, _meta = self._static_python_audit(task, exec_step.result.text)
                if ok:
                    return AgentResult(text=exec_step.result.text, filename=exec_step.result.filename)
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

        last_verifier_idx = None
        for idx in range(len(executions) - 1, -1, -1):
            if executions[idx].step.agent == "verifier":
                last_verifier_idx = idx
                break

        if last_verifier_idx is not None:
            verdict = _parse_verdict(executions[last_verifier_idx].result.text)
            if verdict.get("ok") is True:
                for j in range(last_verifier_idx - 1, -1, -1):
                    if executions[j].step.agent == "python" and executions[j].result.filename:
                        return AgentResult(text=executions[j].result.text, filename=executions[j].result.filename)
                for j in range(last_verifier_idx - 1, -1, -1):
                    if executions[j].step.agent == "python":
                        return AgentResult(text=executions[j].result.text)
            else:
                return AgentResult(text=executions[last_verifier_idx].result.text)

        # Otherwise, return the last python file result produced as text only (do not save invalid code).
        for exec_step in reversed(executions):
            if exec_step.step.agent == "python" and exec_step.result.filename:
                return AgentResult(text=exec_step.result.text, filename=None)

        sections = []
        for exec_step in executions:
            heading = f"### {exec_step.step.agent}\nReason: {exec_step.step.reason}\n"
            sections.append(f"{heading}\n{exec_step.result.text.strip()}\n")
        combined = "\n".join(sections).strip()
        return AgentResult(text=f"Task: {task}\n\n{combined}")
