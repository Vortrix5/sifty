"""`sifty startup` - list and reversibly enable/disable startup programs."""

from __future__ import annotations

import typer
from rich.table import Table

from ...console import console, error, success
from ...core import startup
from .. import output

app = typer.Typer(no_args_is_help=True, help="Manage programs that run at startup (reversible).")


@app.command("list")
def list_cmd() -> None:
    """List startup programs and whether each is enabled or disabled."""
    entries = startup.list_entries()
    if output.json_enabled():
        output.emit([
            {"name": e.name, "enabled": e.enabled, "location": e.location, "command": e.command}
            for e in entries
        ])
        return
    table = Table(title=f"Startup programs ({len(entries):,})")
    table.add_column("Name")
    table.add_column("State")
    table.add_column("Origin", style="dim")
    table.add_column("Command")
    for e in entries:
        state = "[green]enabled[/green]" if e.enabled else "[yellow]disabled[/yellow]"
        table.add_row(e.name, state, e.location, e.command)
    console.print(table)


@app.command("disable")
def disable_cmd(
    name: str = typer.Argument(..., help="Startup entry name to disable."),
) -> None:
    """Disable a startup entry (reversible with `sifty startup enable`)."""
    if startup.set_enabled(name, False):
        success(f"Disabled '{name}'. Re-enable with: sifty startup enable \"{name}\"")
    else:
        error(f"Could not disable '{name}' - not found, already disabled, or HKLM needs --admin.")
        raise typer.Exit(1)


@app.command("enable")
def enable_cmd(
    name: str = typer.Argument(..., help="Startup entry name to enable."),
) -> None:
    """Re-enable a previously disabled startup entry."""
    if startup.set_enabled(name, True):
        success(f"Enabled '{name}'.")
    else:
        error(f"Could not enable '{name}' - not found, already enabled, or HKLM needs --admin.")
        raise typer.Exit(1)
