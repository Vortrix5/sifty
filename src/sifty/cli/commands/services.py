"""`sifty services` - view and toggle a curated set of optional Windows services."""

from __future__ import annotations

import typer
from rich.table import Table

from ...console import console, error, success
from ...core import history, services
from .. import output

app = typer.Typer(no_args_is_help=True, help="Toggle a vetted set of optional Windows services (needs admin to change).")


@app.command("list")
def list_cmd() -> None:
    """Show the curated services and their current start type."""
    items = services.list_services()
    if output.json_enabled():
        output.emit([
            {"name": s.name, "label": s.label, "start_type": s.start_type,
             "present": s.present, "description": s.description}
            for s in items
        ])
        return
    table = Table(title="Optional services")
    table.add_column("Service")
    table.add_column("Name", style="dim")
    table.add_column("State")
    table.add_column("What it is")
    for s in items:
        if not s.present:
            state = "[dim]absent[/dim]"
        elif s.start_type == "disabled":
            state = "[yellow]disabled[/yellow]"
        else:
            state = f"[green]{s.start_type}[/green]"
        table.add_row(s.label, s.name, state, s.description)
    console.print(table)
    console.print("\n[dim]Change with `sifty --admin services disable <name>` / `enable <name>`.[/dim]")


def _apply(name: str, mode: str, action: str) -> None:
    if not services.can_manage(name):
        error(f"'{name}' is not a manageable service (not on Sifty's curated allowlist).")
        raise typer.Exit(1)
    if not services.is_present(name):
        error(f"'{name}' is not installed on this PC - nothing to change.")
        raise typer.Exit(1)
    if services.set_start_type(name, mode):
        history.record_clean(action, name, 0, 0, [])
        success(f"Set '{name}' start type to {mode}.")
    else:
        error(f"Could not change '{name}' - this needs Administrator rights (try `sifty --admin …`).")
        raise typer.Exit(1)


@app.command("disable")
def disable_cmd(name: str = typer.Argument(..., help="Service name to disable.")) -> None:
    """Disable a curated service (sets start type to 'disabled')."""
    _apply(name, "disabled", "service-disable")


@app.command("enable")
def enable_cmd(name: str = typer.Argument(..., help="Service name to enable.")) -> None:
    """Re-enable a curated service (sets start type to 'manual')."""
    _apply(name, "manual", "service-enable")
