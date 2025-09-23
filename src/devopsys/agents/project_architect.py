from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .base import Agent, AgentResult

PROMPT = """
You are a project architect responsible for drafting a build plan.
Analyse the user request and propose a complete project layout.

Return ONLY JSON with the structure:
{{
  "project_name": "...",
  "language": "python|...",
  "summary": "One paragraph describing the goal",
  "tasks": ["key capabilities"],
  "files": [
    {{
      "path": "relative/path.ext",
      "goal": "purpose of the file",
      "agent": "python|bash|docker|universal" (optional),
      "requirements": [
        "actionable bullet requirement",
        "..."
      ]
    }}
  ]
}}

Guidelines:
- Prefer src/ layout for Python projects and rely on uv for environment management.
- Include README.md with setup (uv venv, uv pip install -e .) and usage instructions.
- Include pyproject.toml with [project], [project.scripts], and uv-specific metadata where relevant.
- Ensure every required directory appears via files (use __init__.py to create packages).
- Only specify files that must exist; omit empty arrays.
- Choose appropriate agent when you know the best specialist; otherwise omit and the orchestrator will auto-select.
- Requirements must be explicit enough for a single agent to complete without further clarification.
"""


@dataclass
class ParsedPlan:
    data: dict
    raw: str


class ProjectArchitectAgent(Agent):
    name = "project_architect"
    description = "Design multi-file project layouts"
    prompt_template = PROMPT

    def postprocess(self, text: str) -> AgentResult:
        parsed = self._normalize(text)
        return AgentResult(text=json.dumps(parsed, ensure_ascii=False, indent=2))

    def _normalize(self, text: str) -> dict:
        raw = (text or "").strip()
        if not raw:
            return {}
        if raw.startswith("```"):
            lines = raw.splitlines()
            try:
                closing = next(i for i in range(len(lines) - 1, 0, -1) if lines[i].startswith("```"))
                raw = "\n".join(lines[1:closing]).strip()
            except StopIteration:
                raw = "\n".join(lines[1:]).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0))
                except json.JSONDecodeError:
                    data = {}
            else:
                data = {}
        if not isinstance(data, dict):
            return {}
        files = data.get("files")
        if isinstance(files, list):
            normalized_files = []
            for item in files:
                if not isinstance(item, dict):
                    continue
                path = (item.get("path") or "").strip()
                if not path:
                    continue
                normalized_files.append(
                    {
                        "path": path,
                        "goal": (item.get("goal") or "").strip(),
                        "agent": (item.get("agent") or "").strip(),
                        "requirements": [str(req).strip() for req in (item.get("requirements") or []) if str(req).strip()],
                    }
                )
            data["files"] = normalized_files
        else:
            data["files"] = []
        data.setdefault("project_name", "project")
        data.setdefault("language", "python")
        data.setdefault("summary", "")
        data.setdefault("tasks", [])
        return data
