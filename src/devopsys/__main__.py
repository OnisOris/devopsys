from __future__ import annotations

import pathlib
import sys
from typing import Callable

import click
import httpx
from click.core import ParameterSource
from rich.console import Console

from .agents.registry import AGENT_REGISTRY
from .models.base import Model
from .models.dummy import DummyModel
from .models.ollama import OllamaModel
from .models.openai import OpenAIModel
from .ollama import list_models, pull_model
from .orchestrator import MultiAgentOrchestrator
from .run_logger import RunLogger, NullRunLogger
from .settings import settings

console = Console()

BACKENDS = {
    "dummy": DummyModel,
    "ollama": OllamaModel,
    "openai": OpenAIModel,
    "deepseek": OpenAIModel,
}


def _make_model_factory(
    backend: str,
    model_name: str,
    *,
    ollama_host: str | None = None,
) -> Callable[[], Model]:
    if backend == "dummy":
        return DummyModel
    if backend == "ollama":
        host = ollama_host or settings.ollama_host

        def _factory() -> Model:
            return OllamaModel(
                host=host,
                model=model_name,
                temperature=settings.temperature,
                max_tokens=settings.max_tokens,
                timeout=settings.ollama_timeout,
            )

        return _factory
    if backend == "openai":
        api_key = settings.openai_api_key.strip()
        if not api_key:
            raise click.ClickException("OpenAI backend requires DEVOPSYS_OPENAI_API_KEY")
        base_url = (settings.openai_base_url or "https://api.openai.com/v1").rstrip("/")
        chosen_model = model_name or settings.openai_model
        system_prompt = settings.openai_system_prompt

        def _factory() -> Model:
            return OpenAIModel(
                api_key=api_key,
                model=chosen_model,
                base_url=base_url,
                temperature=settings.temperature,
                max_tokens=settings.max_tokens,
                timeout=settings.openai_timeout,
                system_prompt=system_prompt,
            )

        return _factory
    if backend == "deepseek":
        api_key = settings.deepseek_api_key.strip()
        if not api_key:
            raise click.ClickException("DeepSeek backend requires DEVOPSYS_DEEPSEEK_API_KEY")
        base_url = (settings.deepseek_base_url or "https://api.deepseek.com/v1").rstrip("/")
        chosen_model = model_name or settings.deepseek_model
        system_prompt = settings.deepseek_system_prompt or settings.openai_system_prompt

        def _factory() -> Model:
            return OpenAIModel(
                api_key=api_key,
                model=chosen_model,
                base_url=base_url,
                temperature=settings.temperature,
                max_tokens=settings.max_tokens,
                timeout=settings.deepseek_timeout,
                system_prompt=system_prompt,
            )

        return _factory
    raise click.ClickException(f"Unknown backend: {backend}")


def _ensure_out_dir(path: str | None) -> None:
    if not path:
        return
    p = pathlib.Path(path).expanduser().resolve().parent
    p.mkdir(parents=True, exist_ok=True)


@click.group()
@click.version_option()
@click.option(
    "--backend",
    default=lambda: settings.backend,
    help="Model backend: ollama|dummy|openai|deepseek",
)
@click.option(
    "--model",
    default=lambda: settings.model,
    help="Model name for the selected backend (e.g., codellama:7b-instruct or gpt-4o-mini)",
)
@click.option(
    "--ollama-host",
    default=lambda: settings.ollama_host,
    show_default=True,
    help="Default Ollama base URL",
)
@click.pass_context
def cli_main(ctx: click.Context, backend: str, model: str, ollama_host: str) -> None:
    """devopsys — локальный DevOps-ассистент (CLI)."""
    ctx.ensure_object(dict)
    ctx.obj["backend"] = backend
    model_source = ctx.get_parameter_source("model")
    effective_model = model
    if model_source == ParameterSource.DEFAULT:
        if backend == "openai":
            effective_model = settings.openai_model
        elif backend == "deepseek":
            effective_model = settings.deepseek_model
    ctx.obj["model"] = effective_model
    ctx.obj["ollama_host"] = ollama_host


@cli_main.command("ask")
@click.argument("task", nargs=-1)
@click.option("--backend", type=click.Choice(list(BACKENDS.keys())), help="Override backend for this command")
@click.option("--model", "model_name", type=str, help="Override model name for this command")
@click.option("--planner-model", type=str, help="Override model used by the lead planner")
@click.option(
    "--agent-model",
    "agent_models",
    multiple=True,
    help="Override model for an agent, format: name=model (can be given multiple times)",
)
@click.option("--agent", type=click.Choice(list(AGENT_REGISTRY.keys())), help="Force agent")
@click.option("--os", "os_name", type=click.Choice(["ubuntu", "arch"]), help="Target OS for linux agent")
@click.option("--out", "out_path", type=str, help="Write result to file path")
@click.option("--print", "print_result", is_flag=True, default=True, help="Print to stdout")
@click.option("--trace/--no-trace", default=True, help="Show step-by-step agent logs")
@click.option("--ollama-host", "ollama_host_override", type=str, help="Override Ollama base URL")
@click.pass_context
def ask_cmd(
    ctx: click.Context,
    task: tuple[str, ...],
    backend: str | None,
    model_name: str | None,
    planner_model: str | None,
    agent_models: tuple[str, ...],
    agent: str | None,
    os_name: str | None,
    out_path: str | None,
    print_result: bool,
    trace: bool,
    ollama_host_override: str | None,
) -> None:
    """Распознаёт задачу и вызывает нужного агента."""
    text = " ".join(task).strip()
    if not text:
        raise click.ClickException("Task is empty")

    backend_name: str = backend or ctx.obj["backend"]
    selected_model: str = model_name or ctx.obj["model"]

    base_ollama_host = ollama_host_override or ctx.obj.get("ollama_host", settings.ollama_host)
    model_factory = _make_model_factory(backend_name, selected_model, ollama_host=base_ollama_host)

    planner_factory = None
    if planner_model:
        planner_factory = _make_model_factory(backend_name, planner_model, ollama_host=base_ollama_host)

    # Parse per-agent model overrides: name=model
    agent_factories: dict[str, Callable[[], Model]] = {}
    for item in agent_models or ():
        if "=" not in item:
            raise click.ClickException(f"Invalid --agent-model value '{item}'. Expected name=model")
        name, value = item.split("=", 1)
        name = name.strip().lower()
        if name not in AGENT_REGISTRY:
            raise click.ClickException(f"Unknown agent in --agent-model: {name}")
        agent_factories[name] = _make_model_factory(backend_name, value.strip(), ollama_host=base_ollama_host)
    logger = RunLogger(console) if trace else NullRunLogger()
    orchestrator = MultiAgentOrchestrator(
        model_factory,
        logger=logger,
        planner_model_factory=planner_factory,
        agent_model_factories=agent_factories or None,
    )
    try:
        result = orchestrator.execute(text, forced_agent=agent, os_name=os_name)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    for idx, exec_step in enumerate(result.steps, start=1):
        console.print(
            f"[bold]Step {idx}[/bold] → {exec_step.step.agent} ({exec_step.step.reason})"
        )

    res = result.final

    if out_path:
        _ensure_out_dir(out_path)
        pathlib.Path(out_path).write_text(res.text, encoding="utf-8")
        console.print(f"[green]Saved →[/green] {out_path}\n")
    elif res.filename:
        _ensure_out_dir(res.filename)
        pathlib.Path(res.filename).write_text(res.text, encoding="utf-8")
        console.print(f"[green]Saved →[/green] {res.filename}\n")

    if print_result:
        sys.stdout.write(res.text + ("\n" if not res.text.endswith("\n") else ""))


@cli_main.group("ollama")
@click.option(
    "--host",
    default=None,
    help="Ollama base URL (defaults to --ollama-host)",
)
@click.pass_context
def ollama_cmd(ctx: click.Context, host: str | None) -> None:
    """Утилиты для управления моделями Ollama."""
    ctx.ensure_object(dict)
    effective_host = host or ctx.obj.get("ollama_host", settings.ollama_host)
    ctx.obj["ollama_host"] = effective_host


@ollama_cmd.command("pull")
@click.argument("model_name")
@click.pass_context
def ollama_pull_cmd(ctx: click.Context, model_name: str) -> None:
    host = ctx.obj.get("ollama_host", settings.ollama_host)
    try:
        pull_model(model_name, host=host, console=console)
    except httpx.HTTPError as exc:
        raise click.ClickException(f"Failed to pull model: {exc}") from exc


@ollama_cmd.command("list")
@click.pass_context
def ollama_list_cmd(ctx: click.Context) -> None:
    host = ctx.obj.get("ollama_host", settings.ollama_host)

    try:
        models = list_models(host=host)
    except httpx.HTTPError as exc:
        raise click.ClickException(f"Failed to list models: {exc}") from exc

    if not models:
        console.print(f"[yellow]No models found on {host}.[/]")
        return

    console.print(f"[bold]Models on[/bold] {host}")

    def _format_size(value: int | None) -> str:
        if not value:
            return "?"
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(value)
        for unit in units:
            if size < 1024.0 or unit == units[-1]:
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"

    for info in models:
        size_text = _format_size(info.size)
        meta_parts: list[str] = []
        if info.parameter_size:
            meta_parts.append(info.parameter_size)
        if info.families:
            meta_parts.append("/".join(info.families))
        meta = f" ({', '.join(meta_parts)})" if meta_parts else ""
        console.print(f"- {info.name}{meta} [{size_text}]")
