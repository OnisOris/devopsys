from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Iterable

class Model(ABC):
    """Абстракция LLM-бэкенда."""
    @abstractmethod
    async def acomplete(self, prompt: str) -> str:
        ...

    def complete(self, prompt: str) -> str:
        import anyio
        return anyio.run(self.acomplete, prompt)

    @staticmethod
    def join_messages(parts: Iterable[str]) -> str:
        return "\n\n".join(p.strip() for p in parts if p and p.strip())
