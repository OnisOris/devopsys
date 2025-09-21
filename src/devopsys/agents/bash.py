from __future__ import annotations

from .base import Agent, AgentResult
from .bash_utils import normalise_bash_output

PROMPT = """
You are a senior SRE collaborating with other agents. Generate a Bash script for the request.
Primary user task:
{task}

Planner context (may be empty):
{plan_context}

Workspace snapshot (read-only, may be empty):
{workspace}

Constraints:
- Target: bash (#!/usr/bin/env bash) with set -euo pipefail.
- Include usage() help and argument parsing (getopts or simple parsing).
- Add comments, safety checks, and meaningful exit codes.
- Avoid GNU-only features when portability matters.

Return only the final Bash script content.
"""

class BashAgent(Agent):
    name = "bash"
    description = "Generate Bash scripts"
    prompt_template = PROMPT

    def __init__(self, model) -> None:
        super().__init__(model)
        self._last_task: str = ""

    def run(self, task: str, plan_context: str | None = None, workspace: str | None = None) -> AgentResult:
        self._last_task = task
        return super().run(task=task, plan_context=plan_context, workspace=workspace)

    def postprocess(self, text: str) -> AgentResult:
        code = text.strip()
        if code.startswith("```"):
            code = code.strip("`\n")
            code = "\n".join(line for line in code.splitlines() if not line.lower().startswith("bash"))
        code = normalise_bash_output(code, self._last_task)
        return AgentResult(text=code, filename="script.sh")
