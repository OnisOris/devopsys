from __future__ import annotations

from typing import Any, Callable, cast

from langchain_core.runnables import RunnableLambda
import httpx

from .models.base import Model


def _format_http_error(exc: httpx.HTTPStatusError, model: Model) -> RuntimeError:
    host = getattr(model, "host", getattr(model, "base_url", "<unknown host>"))
    name = getattr(model, "model", "<unknown model>")
    status = exc.response.status_code
    reason = exc.response.reason_phrase
    detail = (exc.response.text or "").strip()
    if len(detail) > 200:
        detail = detail[:200] + "..."
    suffix = f" Response body: {detail}" if detail else ""
    message = (
        f"LLM request failed (model={name}, host={host}): {status} {reason}.{suffix}"
    )
    return RuntimeError(message)


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
            raise _format_http_error(exc, model) from exc

    async def _ainvoke(prompt: Any) -> str:
        try:
            return await model.acomplete(_ensure_text(prompt))
        except httpx.HTTPStatusError as exc:  # pragma: no cover - network dependent
            raise _format_http_error(exc, model) from exc

    # cast keeps type checkers calm; LangChain accepts callables returning strings.
    return RunnableLambda(cast(Callable[[Any], str], _invoke), afunc=_ainvoke)
