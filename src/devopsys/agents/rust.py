from __future__ import annotations
from .base import Agent, AgentResult

PROMPT = """
You are a senior Rust engineer inside a LangChain multi-agent pipeline. Generate a minimal Rust CLI app for the task.
Primary user task:
{task}

Planner context (may be empty):
{plan_context}

Workspace snapshot (read-only, may be empty):
{workspace}

Constraints:
- Use stable Rust edition 2021.
- Provide Cargo.toml and src/main.rs.
- Keep dependencies minimal.
- Add basic argument parsing (clap or std::env).
- Write comments for key parts.

Output format:
- First a Cargo.toml block.
- Then a src/main.rs block.
No extra explanatory text.
"""

class RustAgent(Agent):
    name = "rust"
    description = "Generate Rust CLI skeletons"
    prompt_template = PROMPT

    def postprocess(self, text: str) -> AgentResult:
        content = text.strip()
        return AgentResult(text=content, filename="rust_project.txt")
