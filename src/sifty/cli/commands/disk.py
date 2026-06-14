"""`sifty disk` - volume usage, biggest items, duplicates."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table
from rich.tree import Tree

from ...console import console, human_size, warn
from ...core import disk
from .. import output

app = typer.Typer(no_args_is_help=True, help="Analyze disks: volume usage, biggest items, duplicates.")


@app.command("volumes")
def volumes_cmd() -> None:
    """Show used/free/total for each volume."""
    if output.json_enabled():
        output.emit([
            {
                "drive": v.mountpoint, "fstype": v.fstype, "used": v.used,
                "free": v.free, "total": v.total, "percent": round(v.percent, 1),
            }
            for v in disk.volumes()
        ])
        return

    table = Table(title="Volumes")
    table.add_column("Drive")
    table.add_column("FS", style="dim")
    table.add_column("Used", justify="right")
    table.add_column("Free", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Used %", justify="right")
    for v in disk.volumes():
        color = "red" if v.percent >= 90 else "yellow" if v.percent >= 75 else "green"
        table.add_row(
            v.mountpoint, v.fstype, human_size(v.used), human_size(v.free),
            human_size(v.total), f"[{color}]{v.percent:.0f}%[/{color}]",
        )
    console.print(table)


@app.command("analyze")
def analyze_cmd(
    path: Path = typer.Argument(Path.home(), help="Directory to analyze."),
    top: int = typer.Option(15, "--top", "-n", help="How many of the biggest items to show."),
) -> None:
    """Show the biggest folders/files directly under a path."""
    path = path.expanduser()
    if not path.exists():
        warn(f"Path does not exist: {path}")
        raise typer.Exit(1)

    if output.json_enabled():
        items = disk.biggest(path, top)
        output.emit({
            "path": str(path),
            "items": [
                {"name": e.name, "path": str(e), "size_bytes": s, "is_dir": e.is_dir()}
                for e, s in items
            ],
        })
        return

    with console.status(f"Scanning {path}…"):
        items = disk.biggest(path, top)

    tree = Tree(f"[bold]{path}[/bold]")
    for entry, size in items:
        icon = "📁" if entry.is_dir() else "📄"
        tree.add(f"{icon} {entry.name}  [cyan]{human_size(size)}[/cyan]")
    console.print(tree)


@app.command("duplicates")
def duplicates_cmd(
    path: Path = typer.Argument(..., help="Directory to scan for duplicates."),
    min_size: int = typer.Option(1024, "--min-size", help="Ignore files smaller than this (bytes)."),
) -> None:
    """Find duplicate files and report how much space they waste."""
    path = path.expanduser()
    if not path.exists():
        warn(f"Path does not exist: {path}")
        raise typer.Exit(1)

    if output.json_enabled():
        groups = disk.find_duplicates(path, min_size)
        payload = []
        reclaimable = 0
        for paths in groups.values():
            each = disk._entry_size(paths[0])
            wasted = each * (len(paths) - 1)
            reclaimable += wasted
            payload.append({
                "copies": len(paths), "each_bytes": each,
                "wasted_bytes": wasted, "paths": [str(p) for p in paths],
            })
        output.emit({"groups": payload, "reclaimable_bytes": reclaimable})
        return

    with console.status(f"Hashing files under {path}…"):
        groups = disk.find_duplicates(path, min_size)

    if not groups:
        console.print("No duplicates found.")
        return

    reclaimable = 0
    table = Table(title="Duplicate files")
    table.add_column("Copies", justify="right")
    table.add_column("Each", justify="right")
    table.add_column("Wasted", justify="right")
    table.add_column("Example path")
    for paths in sorted(groups.values(), key=lambda ps: disk._entry_size(ps[0]) * (len(ps) - 1), reverse=True):
        each = disk._entry_size(paths[0])
        wasted = each * (len(paths) - 1)
        reclaimable += wasted
        table.add_row(f"{len(paths):,}", human_size(each), human_size(wasted), str(paths[0]))
    console.print(table)
    console.print(f"\n[bold]Reclaimable by de-duplicating: {human_size(reclaimable)}[/bold]")
