from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from devopsys.models.base import Model
from devopsys.orchestrator import MultiAgentOrchestrator


class CallableModel(Model):
    def __init__(self, handler):
        self._handler = handler

    async def acomplete(self, prompt: str) -> str:  # pragma: no cover - exercised via orchestrator
        return self._handler(prompt)


@pytest.fixture
def project_plan() -> str:
    plan = {
        "project_name": "Sample App",
        "language": "python",
        "summary": "Sample CLI project scaffolded for tests",
        "tasks": ["Provide a CLI entrypoint", "Offer README instructions"],
        "files": [
            {
                "path": "README.md",
                "goal": "Usage instructions",
                "requirements": [
                    "Document how to create a uv virtual environment",
                    "Explain how to run the CLI",
                ],
            },
            {
                "path": "pyproject.toml",
                "goal": "Project metadata with entrypoint",
                "requirements": [
                    "Set project name to sample-app",
                    "Expose sample-app CLI via project.scripts",
                ],
            },
            {
                "path": "src/sample_app/__init__.py",
                "goal": "Package init",
                "requirements": ["Expose __all__ with cli module"],
            },
            {
                "path": "src/sample_app/cli.py",
                "goal": "CLI entrypoint",
                "requirements": ["Implement a main() that prints Hello"],
            },
        ],
    }
    return "Plan summary:\n```json\n" + json.dumps(plan) + "\n```\nThanks!"


@pytest.fixture
def architect_factory(project_plan):
    return lambda: CallableModel(lambda _prompt: project_plan)


def _python_handler(prompt: str) -> str:
    match = re.search(r"Create the file '([^']+)'", prompt)
    path = match.group(1) if match else ""
    if path.endswith("__init__.py"):
        return """\n__all__ = [\"cli\"]\n""".strip()
    if path.endswith("cli.py"):
        return (
            """\nimport argparse\n\n\ndef build_parser() -> argparse.ArgumentParser:\n    parser = argparse.ArgumentParser(description=\"CLI scaffolding\")\n    parser.add_argument(\"--name\", default=\"World\")\n    return parser\n\n\ndef main() -> None:\n    parser = build_parser()\n    args = parser.parse_args()\n    print(f\"Hello {args.name}!\")\n\n\nif __name__ == \"__main__\":\n    main()\n""".strip()
        )
    return "print('placeholder')\n"


@pytest.fixture
def python_factory():
    return lambda: CallableModel(_python_handler)


def _universal_handler(prompt: str) -> str:
    if "pyproject.toml" in prompt:
        return """[project]\nname = \"sample-app\"\nversion = \"0.1.0\"\ndescription = \"Sample CLI app\"\nrequires-python = \">=3.11\"\n\n[project.scripts]\n"sample-app" = "sample_app.cli:main"\n""".strip()
    if "README.md" in prompt:
        return (
            "# Sample App\n\n"
            "## Setup\n\n"
            "```bash\nuv venv .venv\nuv run sample-app --help\n```\n"
        )
    return ""


@pytest.fixture
def universal_factory():
    return lambda: CallableModel(_universal_handler)


@pytest.fixture
def verifier_factory():
    return lambda: CallableModel(lambda _prompt: json.dumps({
        "ok": True,
        "reason": "syntactic checks passed",
        "missing": [],
        "forbidden": [],
        "suggested_prompt": "",
    }))


@pytest.fixture
def orchestrator(architect_factory, python_factory, universal_factory, verifier_factory):
    default_factory = lambda: CallableModel(lambda prompt: prompt)
    return MultiAgentOrchestrator(
        default_factory,
        agent_model_factories={
            "project_architect": architect_factory,
            "python": python_factory,
            "universal": universal_factory,
            "verifier": verifier_factory,
        },
    )


def test_project_scaffold_builds_expected_files(tmp_path, monkeypatch, orchestrator):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("devopsys.orchestrator.shutil.which", lambda name: None)
    monkeypatch.setattr("devopsys.agents.verifier.shutil.which", lambda name: None)

    result = orchestrator.execute(
        "Bootstrap a sample python project",
    )

    final_text = result.final.text
    project_dir = tmp_path / "sample-app"
    assert project_dir.exists() and project_dir.is_dir()

    cli_file = project_dir / "src" / "sample_app" / "cli.py"
    readme_file = project_dir / "README.md"
    pyproject_file = project_dir / "pyproject.toml"

    assert cli_file.exists()
    assert "Hello" in cli_file.read_text(encoding="utf-8")
    assert "uv venv" in readme_file.read_text(encoding="utf-8")
    assert "project.scripts" in pyproject_file.read_text(encoding="utf-8")

    assert "Project scaffold created at" in final_text
    assert "Runtime check" in final_text
    assert "uv" in final_text  # mentions uv setup status

    # Ensure summary references relative path
    assert "sample-app" in final_text

    # No virtual environment created because uv is unavailable
    assert not (project_dir / ".venv").exists()


def test_project_scaffold_uses_custom_root(tmp_path, monkeypatch, orchestrator):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("devopsys.orchestrator.shutil.which", lambda name: None)
    monkeypatch.setattr("devopsys.agents.verifier.shutil.which", lambda name: None)

    target_root = tmp_path / "generated"

    result = orchestrator.execute(
        "Bootstrap a sample python project",
        project_root=target_root,
    )

    project_dir = target_root / "sample-app"
    assert project_dir.exists() and project_dir.is_dir()
    assert "Project scaffold created at sample-app" in result.final.text
    assert not (tmp_path / "sample-app").exists()
