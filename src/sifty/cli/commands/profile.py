"""`sifty profile` - manage saved cleanup presets (junk categories)."""

from __future__ import annotations

import typer
from rich.table import Table

from ...console import console, error, success
from ...core import junk, profiles
from ...core.models import Profile
from .. import output

app = typer.Typer(no_args_is_help=True, help="Saved cleanup profiles (presets of junk categories).")


@app.command("list")
def list_cmd() -> None:
    """List saved cleanup profiles."""
    items = profiles.list_profiles()
    if output.json_enabled():
        output.emit([{"name": p.name, "categories": p.categories} for p in items])
        return
    if not items:
        console.print("No profiles yet. Create one with [cyan]sifty profile add[/cyan].")
    else:
        table = Table(title="Cleanup profiles")
        table.add_column("Name")
        table.add_column("Categories")
        for p in items:
            table.add_row(p.name, ", ".join(p.categories) or "(none)")
        console.print(table)
    keys = ", ".join(c.key for c in junk.junk_categories())
    console.print(f"\n[dim]Available categories: {keys}[/dim]")


@app.command("add")
def add_cmd(
    name: str = typer.Argument(..., help="Profile name."),
    category: list[str] = typer.Option(..., "--category", "-c", help="Junk category key (repeatable)."),
) -> None:
    """Create or replace a profile from one or more junk category keys."""
    valid = {c.key for c in junk.junk_categories()}
    unknown = [c for c in category if c not in valid]
    if unknown:
        error(f"Unknown categories: {', '.join(unknown)}. Valid: {', '.join(sorted(valid))}")
        raise typer.Exit(1)
    profiles.save(Profile(name, category))
    success(f"Saved profile '{name}' ({len(category):,} categories).")


@app.command("remove")
def remove_cmd(name: str = typer.Argument(..., help="Profile name to remove.")) -> None:
    """Delete a saved profile."""
    if profiles.remove(name):
        success(f"Removed profile '{name}'.")
    else:
        error(f"No profile named '{name}'.")
        raise typer.Exit(1)
