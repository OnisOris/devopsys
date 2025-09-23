from __future__ import annotations

import re

from .base import Agent, AgentResult

PROMPT = """
You are a senior DevOps engineer within a LangChain multi-agent team. Produce a production-grade Dockerfile.
Primary user requirements:
{task}

Planner context (may be empty):
{plan_context}

Workspace snapshot (read-only, may be empty):
{workspace}

Constraints:
- Use multi-stage builds when appropriate.
- Pin base images or use slim variants.
- Avoid root where possible; drop capabilities.
- Include a healthcheck if meaningful.
- Expose relevant ports but do not RUN services in foreground unless it's ENTRYPOINT/CMD.
- Keep layers small; combine RUN commands; clean caches.
- Add brief comments explaining key steps.
- If the task mentions Astral's `uv` package manager, install it according to the official instructions (curl | sh) and use `uv pip install` / `uv run` instead of plain pip.
- Use pyproject.toml and the src/ layout when dealing with Python projects; never add requirements.txt or setup.py if not requested.
- If Poetry or pyproject are mentioned, copy the minimal files first to leverage Docker layer caching.

Return only the Dockerfile content.
"""

class DockerAgent(Agent):
    name = "docker"
    description = "Generate Dockerfile"
    prompt_template = PROMPT

    def postprocess(self, text: str) -> AgentResult:
        raw = text.strip()
        match = re.search(r"```(?:dockerfile)?\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
        code = match.group(1) if match else raw
        lines = code.splitlines()
        cleaned: list[str] = []
        started = False
        for line in lines:
            stripped = line.strip()
            if not started:
                if not stripped:
                    continue
                if stripped.startswith("#") or stripped.upper().startswith("FROM ") or stripped.upper().startswith("ARG "):
                    started = True
                else:
                    continue
            cleaned.append(line.rstrip())
        final_code = "\n".join(cleaned).strip()
        if not final_code:
            final_code = code.strip()
        return AgentResult(text=final_code, filename="Dockerfile")
