from __future__ import annotations
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
        code = text.strip()
        if code.startswith("```"):
            code = code.strip("`\n")
            code = "\n".join(line for line in code.splitlines() if not line.lower().startswith("dockerfile"))
        return AgentResult(text=code, filename="Dockerfile")
