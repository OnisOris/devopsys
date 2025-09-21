from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate

from ..langchain_support import model_runnable
from ..models.base import Model

@dataclass
class AgentResult:
    text: str
    filename: Optional[str] = None

class Agent:
    name: str = "agent"
    description: str = "Base agent"
    prompt_template: str = ""

    def __init__(self, model: Model) -> None:
        self.model = model

    def build_prompt(self) -> PromptTemplate:
        if not self.prompt_template:
            raise NotImplementedError("prompt_template must be defined in subclasses")
        return PromptTemplate.from_template(self.prompt_template)

    def postprocess(self, text: str) -> AgentResult:
        return AgentResult(text=text)

    @staticmethod
    def _snippet(text: str, limit: int = 800) -> str:
        text = text or ""
        return text if len(text) <= limit else text[:limit] + "…"

    def _debug_log(self, message: str) -> None:
        print(message)

    def run(self, task: str, plan_context: str | None = None, workspace: str | None = None) -> AgentResult:
        prompt = self.build_prompt()
        payload = {
            "task": task.strip(),
            "plan_context": (plan_context or "").strip(),
            "workspace": (workspace or "").strip(),
        }
        variables = {name: payload.get(name, "") for name in prompt.input_variables}
        rendered = prompt.format(**variables)
        self._debug_log(f"[agent:{self.name}] prompt:\n{self._snippet(rendered)}\n")
        raw = model_runnable(self.model).invoke(rendered)
        self._debug_log(f"[agent:{self.name}] raw output:\n{self._snippet(raw)}\n")
        return self.postprocess(raw)
