"""`sifty update` - check and apply application updates (winget)."""

from __future__ import annotations

import typer
from rich.table import Table

from ...console import confirm, console, error, success, warn
from ...core import updates
from .. import output

app = typer.Typer(no_args_is_help=True, help="Check and apply application updates (winget).")


@app.command("check")
def check_cmd() -> None:
    """List applications that have updates available."""
    from ...windows import winget

    if not winget.available():
        if output.json_enabled():
            output.emit({"error": "winget is not available"})
        else:
            error("winget is not available on this system.")
        raise typer.Exit(1)

    if output.json_enabled():
        output.emit([
            {"name": u.name, "id": u.id, "current": u.current, "available": u.available}
            for u in updates.list_upgrades()
        ])
        return

    with console.status("Checking for updates…"):
        upgrades = updates.list_upgrades()

    if not upgrades:
        success("Everything is up to date.")
        return

    table = Table(title=f"Available updates ({len(upgrades):,})")
    table.add_column("Name")
    table.add_column("Id", style="dim")
    table.add_column("Current", justify="right")
    table.add_column("Available", justify="right", style="green")
    for u in upgrades:
        table.add_row(u.name, u.id, u.current, u.available)
    console.print(table)
    console.print("\nRun [cyan]sifty update apply[/cyan] to install (use --id for a single app).")


@app.command("apply")
def apply_cmd(
    id: str = typer.Option(None, "--id", help="Upgrade only this winget id (default: all)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Apply updates via winget."""
    from ...windows import winget

    if not winget.available():
        error("winget is not available on this system.")
        raise typer.Exit(1)

    target = id or "all"
    if not confirm(f"Upgrade {target} now?", assume_yes=yes):
        warn("Cancelled.")
        return

    console.print("[dim]Running winget…[/dim]")
    code = updates.apply_upgrades(id)
    if code == 0:
        success("Updates applied.")
    else:
        error(f"winget exited with code {code}.")
        raise typer.Exit(code)
