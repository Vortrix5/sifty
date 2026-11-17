"""`sifty do "<request>"` - one-shot agentic maintenance from the command line.

The local model plans the steps and calls Sifty's tools; every tool that needs
approval (per the autonomy level and per-tool policies) is confirmed in the
console first. Nothing here deletes directly: destructive tools route through
the core functions and ``safety.trash()`` like every other command.
"""

from __future__ import annotations

import sys

import typer
from rich.markdown import Markdown
from rich.table import Table

from ...ai import agent
from ...ai.agent import FallbackEvent, FinalAnswerEvent, ToolCallEvent, ToolResultEvent
from ...ai.client import OllamaClient
from ...ai.tools import TOOLS, ToolResult
from ...console import console, error, warn


def _console_confirm(prompt: str) -> bool:
    """Ask in the terminal; with no terminal to ask in, the answer is no."""
    if not sys.stdin or not sys.stdin.isatty():
        warn(f"Skipped (no terminal to confirm in): {prompt}")
        return False
    return typer.confirm(f"  {prompt}. Run it?", default=False)


def _format_args(args: dict) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in args.items())


def _print_table(result: ToolResult) -> None:
    table = Table(title=result.title or None, title_style="bold")
    for col in result.columns:
        table.add_column(col)
    for row in result.rows[:30]:
        table.add_row(*[str(c) for c in row])
    console.print(table)
    if len(result.rows) > 30:
        console.print(f"[dim]  … and {len(result.rows) - 30} more[/dim]")


def render_event(event) -> None:
    """Print one agent event the way a person reading the terminal wants it."""
    if isinstance(event, ToolCallEvent):
        risk = "" if event.risk == "read" else f" [yellow]({event.risk} risk)[/yellow]"
        console.print(f"[cyan]→[/cyan] {event.tool_name}({_format_args(event.args)}){risk}")
    elif isinstance(event, ToolResultEvent):
        if event.skipped:
            console.print(f"  [dim]{event.result}[/dim]")
        elif event.table is not None:
            _print_table(event.table)
        else:
            console.print(f"  [dim]{event.result}[/dim]")
    elif isinstance(event, FallbackEvent):
        warn("This model doesn't support tools, so this is advice only. "
             "A tool-capable model (e.g. qwen2.5 or llama3.1) can act on it.")
        console.print(Markdown(event.text))
    elif isinstance(event, FinalAnswerEvent):
        console.print()
        console.print(Markdown(event.text))


def do_cmd(
    request: str = typer.Argument(..., help='What you want done, e.g. "free up space on C:".'),
    read_only: bool = typer.Option(
        False, "--read-only", help="Only let the agent look around; it can't change anything."
    ),
) -> None:
    """Ask the local AI agent to do a maintenance task, confirming each change."""
    client = OllamaClient.from_config()
    if not client.is_available():
        error(f"Ollama not reachable at {client.host}. Run `sifty ai status` for help.")
        raise typer.Exit(1)

    tools = agent.read_only_tools() if read_only else TOOLS
    autonomy = agent.current_autonomy()
    with console.status("Reading the machine…"):
        system = agent.build_system_prompt()
    messages = [{"role": "system", "content": system}, {"role": "user", "content": request}]

    status = console.status("Thinking…")
    status.start()
    try:
        for event in agent.run(client, messages, autonomy=autonomy,
                               confirm=_confirm_paused(status), tools=tools):
            status.stop()
            render_event(event)
            if not isinstance(event, FinalAnswerEvent | FallbackEvent):
                status.start()
    except typer.Abort:
        # Ctrl+C or end of input at a prompt: stop here, run nothing further.
        status.stop()
        warn("Stopped. Nothing else was run.")
        raise typer.Exit(1) from None
    finally:
        status.stop()


def _confirm_paused(status):
    """Wrap the confirm so the spinner doesn't draw over the prompt."""
    def confirm(prompt: str) -> bool:
        status.stop()
        try:
            return _console_confirm(prompt)
        finally:
            status.start()
    return confirm
