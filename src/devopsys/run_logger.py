from __future__ import annotations

from textwrap import shorten
import ast
from typing import TYPE_CHECKING, Optional, Sequence

from rich.console import Console
from rich.panel import Panel

if TYPE_CHECKING:  # pragma: no cover - import only for type checking
    from .agents.base import AgentResult
    from .orchestrator import PlanStep


def _preview(text: str, *, limit: int = 400) -> str:
    data = (text or "").strip()
    if not data:
        return "<empty>"
    return shorten(data.replace("\n", " ⏎ "), width=limit, placeholder=" …")


class NullRunLogger:
    """No-op logger used when tracing is disabled."""

    def on_start(self, task: str, workspace: str) -> None:  # pragma: no cover - no behaviour
        return

    def on_plan(self, plan: Sequence["PlanStep"]) -> None:  # pragma: no cover - no behaviour
        return

    def on_agent_start(
        self,
        step: "PlanStep",
        instruction: str,
        plan_context: str,
    ) -> None:  # pragma: no cover - no behaviour
        return

    def on_agent_end(self, step: "PlanStep", result: "AgentResult") -> None:  # pragma: no cover
        return

    def on_agent_error(self, step: "PlanStep", error: BaseException) -> None:  # pragma: no cover
        return

    def on_final(self, result: "AgentResult") -> None:  # pragma: no cover
        return


class RunLogger(NullRunLogger):
    """Rich-powered logger giving Codex-like runtime tracing."""

    def __init__(
        self,
        console: Console,
        *,
        show_workspace_snapshot: bool = False,
        preview_limit: int = 400,
    ) -> None:
        self.console = console
        self.show_workspace_snapshot = show_workspace_snapshot
        self.preview_limit = preview_limit

    def on_start(self, task: str, workspace: str) -> None:
        self.console.rule("[bold cyan]Task")
        self.console.log(task.strip() or "<empty task>")
        if self.show_workspace_snapshot:
            self.console.rule("[bold yellow]Workspace Snapshot")
            preview = _preview(workspace, limit=self.preview_limit)
            self.console.print(Panel(preview, title="workspace", expand=False))

    def on_plan(self, plan: Sequence["PlanStep"]) -> None:
        self.console.rule("[bold green]Planner")
        if not plan:
            self.console.log("Planner returned empty plan; falling back to router route.")
            return
        for idx, step in enumerate(plan, start=1):
            instruction = step.instruction.strip() if step.instruction else "<same as task>"
            reason = step.reason.strip() if step.reason else "<no reason provided>"
            self.console.log(f"Step {idx}: {step.agent}", f"instruction={_preview(instruction)}", f"reason={reason}")

    def on_agent_start(self, step: "PlanStep", instruction: str, plan_context: str) -> None:
        self.console.rule(f"[bold magenta]Agent → {step.agent}")
        self.console.log("instruction", _preview(instruction, limit=self.preview_limit))
        if plan_context:
            self.console.log("context", _preview(plan_context, limit=self.preview_limit))

    def on_agent_end(self, step: "PlanStep", result: "AgentResult") -> None:
        details = []
        if result.filename:
            details.append(f"file={result.filename}")
        details.append(f"size={len(result.text or '')} chars")
        self.console.log("result", ", ".join(details))
        preview = _preview(result.text, limit=self.preview_limit)
        self.console.print(Panel(preview, title=f"{step.agent} output", expand=False))
        # Best-effort syntax check for Python outputs with a concise status line.
        try:
            is_python = (result.filename or "").endswith(".py") or step.agent == "python"
            if is_python and (result.text or "").strip():
                ast.parse(result.text)
                self.console.log("syntax", "OK")
        except SyntaxError as exc:  # pragma: no cover - non-critical logging
            self.console.log("syntax", f"ERROR: {exc.msg} (line {exc.lineno})")

    def on_agent_error(self, step: "PlanStep", error: BaseException) -> None:
        self.console.rule(f"[bold red]Agent Error → {step.agent}")
        self.console.log(repr(error))

    def on_final(self, result: "AgentResult") -> None:
        self.console.rule("[bold blue]Final Result")
        desc = f"file={result.filename}" if result.filename else "text"
        self.console.log(desc)
        self.console.print(Panel(_preview(result.text, limit=self.preview_limit), title="final", expand=False))


__all__ = [
    "RunLogger",
    "NullRunLogger",
]
