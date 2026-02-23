"""Centinela CLI — Interactive terminal interface.

Commands:
    centinela chat [MESSAGE]    Chat with the agent (interactive if no message)
    centinela doctor            Check system health
    centinela models            Show model status
    centinela config show       Show current configuration
    centinela version           Show version
"""

from __future__ import annotations

import sys

import typer
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

app = typer.Typer(
    name="centinela",
    help="Centinela — Agente IA autónomo con seguridad de grado empresarial",
    no_args_is_help=True,
)
console = Console()


@app.command()
def chat(
    message: str = typer.Argument(None, help="Message to send (omit for interactive mode)"),
    model: str = typer.Option(None, "--model", "-m", help="Model alias: opus, sonnet, haiku"),
    stream: bool = typer.Option(True, "--stream/--no-stream", help="Enable streaming"),
):
    """Chat with Centinela."""
    from centinela.agents.base import BaseAgent
    from centinela.core.config import get_config

    config = get_config()
    agent = BaseAgent(config=config, model=model)

    if message:
        _send_message(agent, message, stream=stream)
    else:
        _interactive_loop(agent, stream=stream)


def _send_message(agent, message: str, stream: bool = True) -> None:
    """Send a single message and display the response."""
    if stream:
        console.print()
        text_buffer = ""
        with Live(console=console, refresh_per_second=15, vertical_overflow="visible") as live:
            for chunk in agent.stream_chat(message):
                text_buffer += chunk
                live.update(Markdown(text_buffer))
        console.print()
    else:
        with console.status("[bold cyan]Pensando...[/]", spinner="dots"):
            response = agent.chat(message)
        console.print()
        console.print(Markdown(response))
        console.print()


def _interactive_loop(agent, stream: bool = True) -> None:
    """Interactive chat loop."""
    console.print(
        Panel(
            "[bold cyan]Centinela[/] — Agente IA Autónomo\n"
            "Escribe tu mensaje. Usa [bold]Ctrl+C[/] o [bold]salir[/] para terminar.",
            border_style="cyan",
        )
    )

    while True:
        try:
            console.print()
            user_input = console.input("[bold green]tú>[/] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Hasta luego.[/]")
            break

        if not user_input:
            continue
        if user_input.lower() in ("salir", "exit", "quit", "/q"):
            console.print("[dim]Hasta luego.[/]")
            break
        if user_input.lower() == "/reset":
            agent.reset()
            console.print("[dim]Conversación reiniciada.[/]")
            continue
        if user_input.lower() == "/models":
            _show_models()
            continue
        if user_input.lower() == "/help":
            console.print(
                "[dim]Comandos: /reset (nueva conversación), /models (estado modelos), "
                "/q (salir), /help (ayuda)[/]"
            )
            continue

        console.print()
        console.print("[bold cyan]centinela>[/] ", end="")
        _send_message(agent, user_input, stream=stream)


@app.command()
def doctor():
    """Check system health and dependencies."""
    console.print(Panel("[bold]Centinela Doctor[/]", border_style="cyan"))
    checks: list[tuple[str, bool, str]] = []

    # Python version
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_ok = sys.version_info >= (3, 12)
    checks.append(("Python", py_ok, py_ver if py_ok else f"{py_ver} (requiere 3.12+)"))

    # AWS Bedrock
    try:
        import boto3
        from centinela.core.config import get_config

        config = get_config()
        session = boto3.Session(profile_name=config.models.aws_profile)
        client = session.client("bedrock-runtime", region_name=config.models.region)
        # Lightweight check — just see if client was created
        checks.append(("AWS Bedrock", True, f"region={config.models.region}, profile={config.models.aws_profile}"))
    except Exception as e:
        checks.append(("AWS Bedrock", False, str(e)[:80]))

    # Models
    try:
        from centinela.core.config import get_config

        config = get_config()
        model_ids = [config.models.primary] + config.models.fallbacks
        checks.append(("Modelos", True, ", ".join(m.split(".")[-1][:30] for m in model_ids)))
    except Exception as e:
        checks.append(("Modelos", False, str(e)[:80]))

    # Docker
    try:
        import docker

        client = docker.from_env()
        client.ping()
        version = client.version().get("Version", "?")
        checks.append(("Docker", True, f"v{version}"))
    except Exception as e:
        checks.append(("Docker", False, str(e)[:80]))

    # Config file
    from centinela.core.config import _find_config_file

    config_path = _find_config_file()
    if config_path:
        checks.append(("Config", True, str(config_path)))
    else:
        checks.append(("Config", False, "No se encontró centinela.yaml"))

    # Display results
    table = Table(show_header=True, header_style="bold")
    table.add_column("Componente", style="bold")
    table.add_column("Estado")
    table.add_column("Detalle")

    for name, ok, detail in checks:
        status = "[green]OK[/]" if ok else "[red]FALLO[/]"
        table.add_row(name, status, detail)

    console.print(table)

    all_ok = all(ok for _, ok, _ in checks)
    if all_ok:
        console.print("\n[bold green]Todo listo.[/]")
    else:
        console.print("\n[bold yellow]Algunos componentes necesitan atención.[/]")


@app.command()
def models():
    """Show model status and fallback chain."""
    _show_models()


def _show_models():
    from centinela.core.models import get_model_resolver

    resolver = get_model_resolver()
    statuses = resolver.get_status()

    table = Table(title="Modelos Bedrock", show_header=True, header_style="bold")
    table.add_column("Modelo")
    table.add_column("Estado")
    table.add_column("Fallos")
    table.add_column("Cooldown")
    table.add_column("Último error")

    chain = resolver._model_chain
    for i, model_id in enumerate(chain):
        status = statuses[model_id]
        label = "[bold cyan]primario[/]" if i == 0 else f"fallback {i}"
        short_name = model_id.split(".")[-1][:40]
        available = "[green]disponible[/]" if status["available"] else "[red]no disponible[/]"
        cooldown = f"{status['cooldown_remaining']}s" if status["in_cooldown"] else "-"
        error = (status["last_error"] or "-")[:50]

        table.add_row(
            f"{short_name}\n[dim]{label}[/]",
            available,
            str(status["failure_count"]),
            cooldown,
            error,
        )

    console.print(table)


@app.command()
def version():
    """Show version."""
    from centinela import __version__

    console.print(f"[bold cyan]Centinela[/] v{__version__}")


@app.command()
def config(
    action: str = typer.Argument("show", help="Action: show"),
):
    """Show current configuration."""
    if action == "show":
        from centinela.core.config import get_config

        cfg = get_config()
        import json as _json

        data = cfg.model_dump()
        console.print_json(_json.dumps(data, indent=2, default=str))


if __name__ == "__main__":
    app()
