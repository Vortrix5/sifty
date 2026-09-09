"""`sifty ai` - ask the local AI for maintenance advice (Ollama)."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.markdown import Markdown
from rich.table import Table

from ...ai import policy as ai_policy
from ...ai.advisor import SYSTEM_PROMPT, summarize_disk
from ...ai.agent import _VALID_LEVELS, _resolve_action, current_autonomy, set_autonomy
from ...ai.client import OllamaClient
from ...ai.tools import TOOLS
from ...console import console, error, human_size, success, warn
from ...core import disk
from .. import output

app = typer.Typer(no_args_is_help=True, help="Ask the local AI for maintenance advice (Ollama).")

policy_app = typer.Typer(no_args_is_help=True, help="Per-tool AI action policies (auto/ask/never).")
app.add_typer(policy_app, name="policy")

# How _resolve_action's verdict reads to a human.
_ACTION_LABEL = {"run": "auto-run", "confirm": "ask first", "skip": "never (blocked)"}


@app.command("status")
def status_cmd() -> None:
    """Check whether the local Ollama model is reachable."""
    client = OllamaClient.from_config()
    reachable = client.is_available()
    pulled = client.model in client.list_models() if reachable else False
    if output.json_enabled():
        output.emit(
            {
                "host": client.host,
                "model": client.model,
                "reachable": reachable,
                "pulled": pulled,
            }
        )
        return
    if reachable:
        success(f"Ollama is running at {client.host} (model: {client.model}).")
    else:
        error(f"Ollama not reachable at {client.host}.")
        console.print("[dim]Install from https://ollama.com, then run "
                      f"`ollama pull {client.model}`.[/dim]")


@app.command("ask")
def ask_cmd(
    question: str = typer.Argument(..., help="Your maintenance question."),
    path: Path = typer.Option(None, "--path", "-p", help="Ground the answer in this folder's biggest items."),
) -> None:
    """Ask a question, optionally grounded in a folder's largest items."""
    client = OllamaClient.from_config()
    if not client.is_available():
        error(f"Ollama not reachable at {client.host}. Run `sifty ai status` for help.")
        raise typer.Exit(1)

    if path:
        path = path.expanduser()
        if not path.exists():
            warn(f"Path does not exist: {path}")
            raise typer.Exit(1)
        with console.status(f"Scanning {path}…"):
            items = [
                (entry.name, human_size(size))
                for entry, size in disk.biggest(path, 20)
            ]
        with console.status("Thinking…"):
            answer = summarize_disk(client, items, question)
    else:
        with console.status("Thinking…"):
            answer = client.chat(SYSTEM_PROMPT, question)

    if answer:
        # The model replies in Markdown; render it so headings, code fences
        # and tables show up formatted instead of as literal syntax.
        console.print(Markdown(answer))
    else:
        console.print("[yellow]No answer (AI unavailable).[/yellow]")


@app.command("autonomy")
def autonomy_cmd(
    level: str = typer.Argument(None, help=f"New level: {', '.join(_VALID_LEVELS)}. Omit to show current."),
) -> None:
    """Show or set how much the agent does before asking (global default)."""
    if level is None:
        console.print(f"Autonomy: [b]{current_autonomy()}[/b]  [dim](levels: {', '.join(_VALID_LEVELS)})[/dim]")
        return
    if set_autonomy(level):
        success(f"Autonomy set to {level}.")
    else:
        error(f"Invalid level '{level}'. Choose one of: {', '.join(_VALID_LEVELS)}")
        raise typer.Exit(1)


@policy_app.command("list")
def policy_list_cmd() -> None:
    """Show every tool's per-tool policy and its effective action."""
    level = current_autonomy()
    policies = ai_policy.all_policies()
    table = Table(title=f"Tool policies (autonomy: {level})", title_style="bold")
    for col in ("Tool", "Risk", "Policy", "Effective"):
        table.add_column(col)
    for t in TOOLS:
        set_p = policies.get(t.name, "-")
        action = _resolve_action(t, level, policies.get(t.name, "default"))
        table.add_row(t.name, t.risk, set_p, _ACTION_LABEL[action])
    console.print(table)


@policy_app.command("set")
def policy_set_cmd(
    tool: str = typer.Argument(..., help="Tool name (see `sifty ai policy list`)."),
    action: str = typer.Argument(..., help=f"One of: {', '.join(ai_policy.VALID_POLICIES)}."),
) -> None:
    """Set a tool's policy (auto = never ask, ask = always ask, never = block, default = follow autonomy)."""
    if ai_policy.set_policy(tool, action):
        success(f"Policy for '{tool}' set to {action}.")
    else:
        error(f"Unknown tool or invalid policy. Policies: {', '.join(ai_policy.VALID_POLICIES)}.")
        raise typer.Exit(1)


@policy_app.command("reset")
def policy_reset_cmd(
    tool: str = typer.Argument(None, help="Tool to reset. Omit to clear all policies."),
) -> None:
    """Clear one tool's policy, or all of them."""
    ai_policy.reset_policy(tool)
    success("Cleared all tool policies." if tool is None else f"Reset policy for '{tool}'.")
