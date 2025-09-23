from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

from .agents.registry import AGENT_REGISTRY


@dataclass
class ProjectFileSpec:
    path: str
    goal: str
    agent_hint: str | None
    requirements: Sequence[str]

    @property
    def normalized_path(self) -> str:
        return self.path.replace("\\", "/").strip()

    @property
    def extension(self) -> str:
        name = Path(self.normalized_path).name
        if name.startswith(".") and name != ".env":
            return name
        if name.lower() == "dockerfile":
            return "dockerfile"
        return Path(self.normalized_path).suffix.lower()


@dataclass
class ProjectSpec:
    project_name: str
    summary: str
    language: str
    tasks: Sequence[str]
    files: Sequence[ProjectFileSpec]
    raw: dict

    @classmethod
    def from_json(cls, payload: str) -> ProjectSpec:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise ValueError(f"project plan is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("project plan JSON must be an object")
        name = str(data.get("project_name") or "project").strip() or "project"
        summary = str(data.get("summary") or "").strip()
        language = str(data.get("language") or "").strip().lower() or "unknown"
        tasks_raw = data.get("tasks")
        tasks = [str(item).strip() for item in tasks_raw or [] if str(item).strip()]
        files_raw = data.get("files") or []
        files: List[ProjectFileSpec] = []
        for item in files_raw:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            goal = str(item.get("goal") or "").strip()
            agent_hint = str(item.get("agent") or "").strip().lower() or None
            requirements_raw = item.get("requirements") or []
            requirements = [
                str(req).strip()
                for req in requirements_raw
                if str(req).strip()
            ]
            files.append(
                ProjectFileSpec(
                    path=path,
                    goal=goal,
                    agent_hint=agent_hint,
                    requirements=tuple(requirements),
                )
            )
        return cls(
            project_name=name,
            summary=summary,
            language=language,
            tasks=tuple(tasks),
            files=tuple(files),
            raw=data,
        )

    @property
    def slug(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", self.project_name.lower())
        slug = slug.strip("-")
        return slug or "project"

    def describe(self) -> str:
        lines: List[str] = []
        if self.summary:
            lines.append(self.summary.strip())
        if self.tasks:
            lines.append("Key capabilities:")
            for item in self.tasks:
                lines.append(f"- {item}")
        return "\n".join(lines).strip()


def _agent_exists(name: str | None) -> bool:
    return bool(name and name in AGENT_REGISTRY)


def select_agent_for_file(file_spec: ProjectFileSpec, project: ProjectSpec) -> str:
    if _agent_exists(file_spec.agent_hint):
        return file_spec.agent_hint  # type: ignore[return-value]

    ext = file_spec.extension
    if ext in {".py"}:
        return "python"
    if ext in {".rs"}:
        return "rust"
    if ext in {".sh", ".bash"}:
        return "bash"
    if ext == ".dockerfile" or ext == "dockerfile" or Path(file_spec.normalized_path).name.lower() == "dockerfile":
        return "docker"
    if ext in {".md", ".toml", ".yaml", ".yml", ".json", ".txt", ".ini", ".cfg"}:
        return "universal"
    if project.language == "python" and ext == "":
        return "python" if file_spec.goal.lower().startswith("module") else "universal"
    return "universal"


def build_instruction(file_spec: ProjectFileSpec, project: ProjectSpec) -> str:
    header = f"Create the file '{file_spec.normalized_path}' for the project '{project.project_name}'."
    details: List[str] = [header]
    if file_spec.goal:
        details.append(f"Goal: {file_spec.goal}.")
    summary = project.summary.strip()
    if summary:
        details.append(f"Project summary: {summary}.")
    if project.language:
        details.append(f"Primary language: {project.language}.")
    if project.tasks:
        details.append("Key capabilities:")
        for item in project.tasks:
            details.append(f"- {item}")
    if file_spec.requirements:
        details.append("File requirements:")
        for req in file_spec.requirements:
            details.append(f"- {req}")
    details.append("Ensure the file is production-ready and consistent with the rest of the project.")
    return "\n".join(details).strip()


def ensure_directory(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def format_plan_context_for_agent(
    agent_name: str,
    file_spec: ProjectFileSpec,
    project: ProjectSpec,
    ready_files: Iterable[str],
) -> str:
    if agent_name == "universal":
        payload = {
            "path": file_spec.normalized_path,
            "project_summary": project.summary,
            "requirements": list(file_spec.requirements),
            "language": project.language,
            "ready_files": list(ready_files),
        }
        return json.dumps(payload, ensure_ascii=False)
    lines: List[str] = []
    if project.summary:
        lines.append(project.summary)
    if file_spec.goal:
        lines.append(f"File goal: {file_spec.goal}")
    if file_spec.requirements:
        lines.append("Requirements:")
        for req in file_spec.requirements:
            lines.append(f"- {req}")
    existing = list(ready_files)
    if existing:
        lines.append("Existing files:")
        for name in existing:
            lines.append(f"- {name}")
    return "\n".join(lines)


def summarize_created_files(root: Path, files: Sequence[ProjectFileSpec]) -> str:
    lines = [f"Project scaffold created at {root}"]
    if not files:
        return "\n".join(lines)
    lines.append("Generated files:")
    for spec in files:
        lines.append(f"- {spec.normalized_path}")
    return "\n".join(lines)


__all__ = [
    "ProjectSpec",
    "ProjectFileSpec",
    "select_agent_for_file",
    "build_instruction",
    "ensure_directory",
    "format_plan_context_for_agent",
    "summarize_created_files",
]
