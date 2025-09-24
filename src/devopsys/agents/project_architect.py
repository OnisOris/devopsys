from __future__ import annotations

import json
import re
from dataclasses import dataclass

_CURLY_QUOTES = {
    "“": '"',
    "”": '"',
    "„": '"',
    "‟": '"',
    "‟": '"',
    "’": "'",
    "‘": "'",
}


def _strip_code_fences(text: str) -> str:
    if text.startswith("```"):
        lines = text.splitlines()
        try:
            closing = next(i for i in range(len(lines) - 1, 0, -1) if lines[i].startswith("```"))
            return "\n".join(lines[1:closing]).strip()
        except StopIteration:
            return "\n".join(lines[1:]).strip()
    return text


def _replace_curly_quotes(text: str) -> str:
    for wrong, right in _CURLY_QUOTES.items():
        text = text.replace(wrong, right)
    return text


def _strip_json_comments(text: str) -> str:
    without_line = re.sub(r"//.*?$", "", text, flags=re.MULTILINE)
    without_block = re.sub(r"/\*.*?\*/", "", without_line, flags=re.DOTALL)
    return without_block


def _remove_trailing_commas(text: str) -> str:
    pattern = re.compile(r",(\s*[}\]])")
    previous = None
    current = text
    while current != previous:
        previous = current
        current = pattern.sub(r"\1", current)
    return current

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


def _extract_plan_regex(text: str) -> dict:
    result: dict[str, object] = {}

    def _extract_str(source: str, pattern: str) -> str:
        match = re.search(pattern, source, re.DOTALL)
        return match.group(1).strip() if match else ""

    name = _extract_str(text, r'"project_name"\s*:\s*"([^"]+)"')
    if name:
        result["project_name"] = name
    language = _extract_str(text, r'"language"\s*:\s*"([^"]+)"')
    if language:
        result["language"] = language
    summary = _extract_str(text, r'"summary"\s*:\s*"([^"]*)"')
    if summary:
        result["summary"] = summary

    tasks_block = re.search(r'"tasks"\s*:\s*\[(.*?)\]', text, re.DOTALL)
    if tasks_block:
        tasks = [item.strip() for item in re.findall(r'"([^"]+)"', tasks_block.group(1)) if item.strip()]
        if tasks:
            result["tasks"] = tasks

    files: list[dict[str, object]] = []
    for match in re.finditer(r'\{[^{}]*?"path"\s*:\s*"[^"]+"[^{}]*?\}', text, re.DOTALL):
        block = match.group(0)
        cleaned = _remove_trailing_commas(_strip_json_comments(_replace_curly_quotes(block)))
        try:
            file_data = json.loads(cleaned)
            if isinstance(file_data, dict) and file_data.get("path"):
                files.append(file_data)
                continue
        except json.JSONDecodeError:
            pass

        path = _extract_str(block, r'"path"\s*:\s*"([^"]+)"')
        if not path:
            continue
        entry: dict[str, object] = {"path": path}
        goal = _extract_str(block, r'"goal"\s*:\s*"([^"]*)"')
        if goal:
            entry["goal"] = goal
        agent = _extract_str(block, r'"agent"\s*:\s*"([^"]*)"')
        if agent:
            entry["agent"] = agent
        req_block = re.search(r'"requirements"\s*:\s*\[(.*?)\]', block, re.DOTALL)
        if req_block:
            req_items = [item.strip() for item in re.findall(r'"([^"]+)"', req_block.group(1)) if item.strip()]
            if req_items:
                entry["requirements"] = req_items
        files.append(entry)

    if files:
        result["files"] = files
    return result


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

        base = _strip_code_fences(raw)
        base = _replace_curly_quotes(base)

        candidates: list[str] = []
        primary = base.strip()
        if primary:
            candidates.append(primary)
        candidates.append(_strip_json_comments(primary))
        candidates.append(_remove_trailing_commas(primary))
        candidates.append(_remove_trailing_commas(_strip_json_comments(primary)))

        data: dict | None = None
        for candidate in candidates:
            candidate = candidate.strip()
            if not candidate:
                continue
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    break
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", candidate, re.DOTALL)
                if not match:
                    continue
                try:
                    data = json.loads(_remove_trailing_commas(match.group(0)))
                    if isinstance(data, dict):
                        break
                except json.JSONDecodeError:
                    continue
        if not isinstance(data, dict) or not data.get("files"):
            fallback = _extract_plan_regex(raw)
            if fallback:
                data = fallback
            else:
                data = {} if not isinstance(data, dict) else data
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
