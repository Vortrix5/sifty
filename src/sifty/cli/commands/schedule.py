"""`sifty schedule` - run cleanup profiles automatically via Task Scheduler."""

from __future__ import annotations

import typer
from rich.table import Table

from ...console import console, error, success
from ...core import profiles, schedule
from .. import output

app = typer.Typer(no_args_is_help=True, help="Schedule cleanup profiles to run automatically.")


@app.command("add")
def add_cmd(
    name: str = typer.Argument(..., help="A name for the scheduled task."),
    profile_name: str = typer.Option(..., "--profile", "-p", help="Profile to run."),
    sc: str = typer.Option("WEEKLY", "--sc", help="WEEKLY or DAILY."),
    day: str = typer.Option("SUN", "--day", help="Day for WEEKLY (MON…SUN)."),
    time: str = typer.Option("03:00", "--time", help="Start time, HH:MM (24h)."),
) -> None:
    """Create a scheduled task that runs a cleanup profile."""
    if profiles.get(profile_name) is None:
        error(f"No profile named '{profile_name}'. See `sifty profile list`.")
        raise typer.Exit(1)
    ok, message = schedule.add(
        name, profile_name, schedule.sifty_command(profile_name),
        sc.upper(), day.upper(), time,
    )
    if ok:
        success(f"Scheduled '{name}' → profile '{profile_name}' ({sc.lower()} {time}).")
    else:
        error(f"Failed to create task: {message}")
        raise typer.Exit(1)


@app.command("list")
def list_cmd() -> None:
    """List Sifty's scheduled tasks."""
    items = schedule.list_schedules()
    if output.json_enabled():
        output.emit(items)
        return
    if not items:
        console.print("No schedules yet. Create one with [cyan]sifty schedule add[/cyan].")
        return
    table = Table(title="Scheduled cleanups")
    table.add_column("Name")
    table.add_column("Profile")
    table.add_column("When")
    table.add_column("Active")
    for s in items:
        active = "[green]yes[/green]" if s["active"] else "[yellow]missing[/yellow]"
        table.add_row(s["name"], s["profile"], s["schedule"], active)
    console.print(table)


@app.command("remove")
def remove_cmd(name: str = typer.Argument(..., help="Scheduled task name to remove.")) -> None:
    """Remove a scheduled task."""
    if schedule.remove(name):
        success(f"Removed schedule '{name}'.")
    else:
        error(f"Could not remove task '{name}' (it may not exist).")
        raise typer.Exit(1)
