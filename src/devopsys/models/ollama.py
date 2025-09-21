from __future__ import annotations
import httpx
from .base import Model

class OllamaModel(Model):
    def __init__(
        self,
        host: str,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        timeout: float = 120.0,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    async def acomplete(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(f"{self.host}/api/generate", json=payload)
            r.raise_for_status()
            data = r.json()
            return data.get("response", "")
