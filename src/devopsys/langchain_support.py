from __future__ import annotations

from typing import Any, Callable, cast

from langchain_core.runnables import RunnableLambda
import httpx

from .models.base import Model


def model_runnable(model: Model) -> RunnableLambda:
    """Wrap project Model into a LangChain RunnableLambda instance."""

    def _ensure_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if hasattr(value, "to_string"):
            return value.to_string()
        return str(value)

    def _invoke(prompt: Any) -> str:
        try:
            return model.complete(_ensure_text(prompt))
        except httpx.HTTPStatusError as exc:  # pragma: no cover - network dependent
            host = getattr(model, "host", "<unknown host>")
            name = getattr(model, "model", "<unknown model>")
            raise RuntimeError(
                f"LLM request failed (model={name}, host={host}): {exc.response.status_code} {exc.response.reason_phrase}. "
                "Ensure Ollama is running and the model is available."
            ) from exc

    async def _ainvoke(prompt: Any) -> str:
        try:
            return await model.acomplete(_ensure_text(prompt))
        except httpx.HTTPStatusError as exc:  # pragma: no cover - network dependent
            host = getattr(model, "host", "<unknown host>")
            name = getattr(model, "model", "<unknown model>")
            raise RuntimeError(
                f"LLM request failed (model={name}, host={host}): {exc.response.status_code} {exc.response.reason_phrase}. "
                "Ensure Ollama is running and the model is available."
            ) from exc

    # cast keeps type checkers calm; LangChain accepts callables returning strings.
    return RunnableLambda(cast(Callable[[Any], str], _invoke), afunc=_ainvoke)
