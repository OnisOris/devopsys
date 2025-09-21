from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

import httpx
from rich.console import Console


@dataclass
class PullEvent:
    status: str
    digest: Optional[str] = None
    completed: Optional[int] = None
    total: Optional[int] = None


@dataclass
class ModelInfo:
    name: str
    size: Optional[int] = None
    digest: Optional[str] = None
    parameter_size: Optional[str] = None
    families: Tuple[str, ...] = ()
    modified_at: Optional[str] = None


def _parse_events(lines: Iterable[str]) -> Iterable[PullEvent]:
    for raw in lines:
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        status = data.get("status")
        if not status:
            continue
        yield PullEvent(
            status=status,
            digest=data.get("digest"),
            completed=data.get("completed"),
            total=data.get("total"),
        )


def pull_model(model: str, host: str, console: Optional[Console] = None, timeout: float = 300.0) -> None:
    """Pull an Ollama model using the HTTP API."""
    console = console or Console()
    base_url = host.rstrip("/")
    url = f"{base_url}/api/pull"
    payload = {"name": model, "stream": True}

    with httpx.Client(timeout=timeout) as client:
        with client.stream("POST", url, json=payload) as response:
            response.raise_for_status()
            for event in _parse_events(response.iter_lines()):
                parts = [event.status]
                if event.digest:
                    parts.append(event.digest)
                if event.completed is not None and event.total:
                    parts.append(f"{event.completed}/{event.total}")
                console.print("[cyan]Ollama[/] " + " · ".join(parts))

    console.print(f"[green]Model ready:[/] {model}")


def list_models(host: str, timeout: float = 30.0) -> List[ModelInfo]:
    """Return models already available on the Ollama host."""
    base_url = host.rstrip("/")
    url = f"{base_url}/api/tags"

    with httpx.Client(timeout=timeout) as client:
        response = client.get(url)
        response.raise_for_status()

    payload = response.json()
    items = payload.get("models", []) if isinstance(payload, dict) else []

    models: List[ModelInfo] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        details_raw = item.get("details") or {}
        details = details_raw if isinstance(details_raw, dict) else {}
        family = details.get("family")
        families_raw = details.get("families")

        families: Tuple[str, ...] = ()
        if isinstance(families_raw, list):
            families = tuple(str(fam) for fam in families_raw if isinstance(fam, str))
        elif isinstance(family, str):
            families = (family,)

        size_raw = item.get("size")
        digest_raw = item.get("digest")
        modified_raw = item.get("modified_at")
        parameter_raw = details.get("parameter_size")
        name_raw = item.get("name", "")

        models.append(
            ModelInfo(
                name=str(name_raw),
                size=size_raw if isinstance(size_raw, int) else None,
                digest=digest_raw if isinstance(digest_raw, str) else None,
                parameter_size=parameter_raw if isinstance(parameter_raw, str) else None,
                families=families,
                modified_at=modified_raw if isinstance(modified_raw, str) else None,
            )
        )

    return models
