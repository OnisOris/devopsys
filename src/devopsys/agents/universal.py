from __future__ import annotations

from .base import Agent, AgentResult

PROMPT = """
You are a senior software engineer and technical writer. Generate the exact file contents requested.

File path: {path}
Project context:
{project_summary}

Task:
{task}

Rules:
- Return only the raw file contents with no Markdown fences or explanations.
- Match the requested format (e.g., Markdown, TOML, YAML) precisely.
- Keep placeholders minimal; prefer working, ready-to-use content.
"""

class UniversalAgent(Agent):
    name = "universal"
    description = "Generate arbitrary text files (Markdown/TOML/YAML/etc.)"
    prompt_template = PROMPT

    def run(self, task: str, plan_context: str | None = None, workspace: str | None = None) -> AgentResult:
        # We encode metadata (path + project summary) in the plan context.
        project_summary = ""
        path = ""
        if plan_context:
            try:
                import json
                ctx = json.loads(plan_context)
                project_summary = ctx.get("project_summary", "")
                path = ctx.get("path", "")
            except Exception:
                project_summary = plan_context
        payload = {
            "task": task.strip(),
            "project_summary": project_summary.strip(),
            "path": path,
        }
        prompt = self.build_prompt()
        variables = {name: payload.get(name, "") for name in prompt.input_variables}
        rendered = prompt.format(**variables)
        self._debug_log(f"[agent:{self.name}] prompt:\n{self._snippet(rendered)}\n")
        raw = self.model.complete(rendered)
        self._debug_log(f"[agent:{self.name}] raw output:\n{self._snippet(raw)}\n")
        return self.postprocess(raw, path)

    def postprocess(self, text: str, path: str | None = None) -> AgentResult:
        cleaned = (text or "").strip()
        if cleaned.startswith("```"):
            # Strip code fences if the model added them
            lines = cleaned.splitlines()
            if len(lines) >= 2:
                fence = lines[0]
                if fence.startswith("```"):
                    # Find matching closing fence
                    try:
                        closing = next(i for i in range(len(lines) - 1, 0, -1) if lines[i].startswith("```"))
                        cleaned = "\n".join(lines[1:closing]).strip()
                    except StopIteration:
                        cleaned = "\n".join(lines[1:]).strip()
        return AgentResult(text=cleaned, filename=path)
