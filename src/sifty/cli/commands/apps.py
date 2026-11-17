"""`sifty apps` - list installed apps and startup items, uninstall via winget."""

from __future__ import annotations

import typer
from rich.table import Table

from ...console import confirm, console, error, human_size, success, warn
from ...core import apps
from .. import output


class _null:
    def __enter__(self): return self
    def __exit__(self, *_): return False

app = typer.Typer(no_args_is_help=True, help="List, inspect, and remove installed apps and startup items.")


@app.command("list")
def list_cmd(
    sort_by_size: bool = typer.Option(False, "--by-size", help="Sort by disk size (largest first)."),
    limit: int = typer.Option(0, "--limit", "-n", help="Show only the first N apps (0 = all)."),
) -> None:
    """List installed applications."""
    items = apps.installed_apps()
    if sort_by_size:
        items = sorted(items, key=lambda a: a.size_bytes, reverse=True)
    if limit:
        items = items[:limit]

    if output.json_enabled():
        output.emit([
            {"name": a.name, "version": a.version, "publisher": a.publisher,
             "size_bytes": a.size_bytes}
            for a in items
        ])
        return

    table = Table(title=f"Installed apps ({len(items):,})")
    table.add_column("Name")
    table.add_column("Version", style="dim")
    table.add_column("Publisher", style="dim")
    table.add_column("Size", justify="right")
    for a in items:
        table.add_row(a.name, a.version, a.publisher, human_size(a.size_bytes) if a.size_bytes else "-")
    console.print(table)


@app.command("startup")
def startup_cmd() -> None:
    """List programs that launch at startup."""
    entries = apps.startup_entries()
    if output.json_enabled():
        output.emit([
            {"name": e.name, "location": e.location, "command": e.command}
            for e in entries
        ])
        return
    table = Table(title=f"Startup programs ({len(entries):,})")
    table.add_column("Name")
    table.add_column("Origin", style="dim")
    table.add_column("Command")
    for e in entries:
        table.add_row(e.name, e.location, e.command)
    console.print(table)


@app.command("orphans")
def orphans_cmd() -> None:
    """Report orphaned uninstall entries whose executable no longer exists."""
    from ...core.registry_scan import find_orphan_uninstall_entries

    with console.status("Scanning uninstall registry keys…") if not output.json_enabled() else _null():
        entries = find_orphan_uninstall_entries()

    if output.json_enabled():
        output.emit([
            {"display_name": e.display_name, "reason": e.reason,
             "uninstall_string": e.uninstall_string, "key_path": e.key_path}
            for e in entries
        ])
        return

    if not entries:
        success("No orphaned uninstall entries found.")
        return

    table = Table(title=f"Orphaned uninstall entries ({len(entries):,})")
    table.add_column("Application")
    table.add_column("Reason", style="dim")
    table.add_column("Key (hive)", style="dim")
    for e in entries:
        table.add_row(e.display_name, e.reason, e.hive)
    console.print(table)
    console.print(
        f"\n[dim]{len(entries):,} entry/entries with broken uninstallers. "
        "These can be removed manually via regedit or a registry cleaner.[/dim]"
    )


@app.command("uninstall")
def uninstall_cmd(
    name: str = typer.Argument(..., help="App name (or winget id) to uninstall."),
    apply: bool = typer.Option(False, "--apply", help="Actually run the uninstaller."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Uninstall an app via winget (preferred) with a dry-run preview."""
    from ...windows import winget

    if not winget.available():
        error("winget is not available on this system.")
        raise typer.Exit(1)

    if not apply:
        console.print(f"[dim]Dry-run:[/dim] would run [cyan]winget uninstall --name \"{name}\"[/cyan]")
        console.print("[dim]Re-run with --apply to uninstall.[/dim]")
        return

    if not confirm(f"Uninstall '{name}'?", assume_yes=yes):
        warn("Cancelled.")
        return

    ok, message = apps.uninstall_app(name)
    if ok:
        success(message)
        _report_leftovers(name)
    else:
        error(message)
        raise typer.Exit(1)


def _report_leftovers(name: str) -> None:
    """After an uninstall, tell the user what the uninstaller left behind."""
    from ...core.leftovers import find_leftovers

    with console.status("Scanning for leftovers…"):
        items = find_leftovers(name)
    if not items:
        return
    total = sum(i.size_bytes for i in items)
    warn(f"{len(items):,} leftover item(s) found ({human_size(total)}).")
    console.print(f'[dim]Review and remove them with: [cyan]sifty apps leftovers "{name}"[/cyan][/dim]')


@app.command("leftovers")
def leftovers_cmd(
    name: str = typer.Argument(..., help="App name to scan leftovers for (after uninstalling)."),
    publisher: str = typer.Option("", "--publisher", help="Publisher name, to match Publisher\\App folders."),
    apply: bool = typer.Option(False, "--apply", help="Move the leftovers to the Recycle Bin."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Find files an uninstaller left behind (AppData, ProgramData, Start Menu)."""
    from ...core import history
    from ...core.leftovers import clean_leftovers, find_leftovers

    with console.status("Scanning for leftovers…") if not output.json_enabled() else _null():
        items = find_leftovers(name, publisher)

    if output.json_enabled():
        output.emit([
            {"path": str(i.path), "size_bytes": i.size_bytes, "kind": i.kind}
            for i in items
        ])
        return

    if not items:
        success(f"No leftovers found for '{name}'.")
        return

    total = sum(i.size_bytes for i in items)
    table = Table(title=f"Leftovers for '{name}' ({human_size(total)})")
    table.add_column("Path")
    table.add_column("Kind", style="dim")
    table.add_column("Size", justify="right")
    for i in items:
        table.add_row(str(i.path), i.kind, human_size(i.size_bytes))
    console.print(table)

    if not apply:
        console.print("[dim]Dry-run - re-run with --apply to move them to the Recycle Bin.[/dim]")
        return
    if not confirm(f"Move {len(items):,} item(s) ({human_size(total)}) to the Recycle Bin?", assume_yes=yes):
        warn("Cancelled.")
        return
    result = clean_leftovers(items, dry_run=False)
    history.record_clean("leftovers", name, result.bytes_freed, result.items, result.trashed)
    success(f"Sent {result.items:,} item(s) ({human_size(result.bytes_freed)}) to the Recycle Bin.")
    if result.skipped:
        warn(f"{len(result.skipped):,} item(s) skipped (in use or protected).")
