"""`sifty purge` - scan and remove dev artifact directories."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn
from rich.table import Table

from ...console import confirm, console, human_size, success, warn
from ...core import history, purge
from .. import output

app = typer.Typer(no_args_is_help=True, help="Find and remove dev artifact directories (node_modules, dist, __pycache__, …).")


def _print_details(artifacts: list[purge.ArtifactScan], *, title: str) -> None:
    """One row per artifact directory, with its full path."""
    table = Table(title=title)
    table.add_column("Pattern", style="dim")
    table.add_column("Size", justify="right")
    table.add_column("Path", overflow="fold")
    for a in artifacts:
        table.add_row(a.pattern, human_size(a.size_bytes), str(a.path))
    table.add_section()
    total = sum(a.size_bytes for a in artifacts)
    table.add_row("", f"[bold]{human_size(total)}[/bold]", f"[dim]{len(artifacts):,} directories[/dim]")
    console.print(table)


def _print_summary(artifacts: list[purge.ArtifactScan], *, title: str) -> None:
    """One row per pattern (count + size), largest first."""
    table = Table(title=title)
    table.add_column("Pattern", style="dim")
    table.add_column("Directories", justify="right")
    table.add_column("Size", justify="right")
    grouped: dict[str, tuple[int, int]] = {}
    for a in artifacts:
        count, size = grouped.get(a.pattern, (0, 0))
        grouped[a.pattern] = (count + 1, size + a.size_bytes)
    for pattern, (count, size) in sorted(grouped.items(), key=lambda item: item[1][1], reverse=True):
        table.add_row(pattern, f"{count:,}", human_size(size))
    table.add_section()
    total = sum(a.size_bytes for a in artifacts)
    table.add_row("[bold]Total[/bold]", f"[bold]{len(artifacts):,}[/bold]", f"[bold]{human_size(total)}[/bold]")
    console.print(table)


@app.command("scan")
def scan_cmd(
    path: Path = typer.Argument(..., help="Root directory to scan."),
) -> None:
    """List dev artifact directories under PATH without deleting anything."""
    path = path.expanduser()
    if not path.exists():
        warn(f"Path does not exist: {path}")
        raise typer.Exit(1)

    with console.status(f"Scanning {path}…") if not output.json_enabled() else _null():
        artifacts = purge.scan_artifacts(path)

    if output.json_enabled():
        output.emit([{"path": str(a.path), "pattern": a.pattern, "size_bytes": a.size_bytes}
                     for a in artifacts])
        return

    if not artifacts:
        success(f"No artifact directories found under {path}.")
        return

    _print_details(artifacts, title=f"Artifact directories under {path}")
    console.print("\nRun [cyan]sifty purge clean PATH --apply[/cyan] to remove them.")


@app.command("clean")
def clean_cmd(
    path: Path = typer.Argument(..., help="Root directory to clean."),
    apply: bool = typer.Option(False, "--apply", help="Actually move artifacts to the Recycle Bin."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    details: bool = typer.Option(False, "--details", help="Show every matching directory and its full path."),
) -> None:
    """Remove dev artifact directories under PATH (dry-run unless --apply)."""
    path = path.expanduser()
    if not path.exists():
        warn(f"Path does not exist: {path}")
        raise typer.Exit(1)

    with console.status(f"Scanning {path}…"):
        artifacts = purge.scan_artifacts(path)

    if not artifacts:
        success("No artifact directories found.")
        return

    total = sum(a.size_bytes for a in artifacts)
    if details:
        _print_details(artifacts, title=f"Artifact directories under {path}")
    else:
        _print_summary(artifacts, title=f"Artifact summary under {path}")
    if not apply:
        console.print("[dim]Dry-run - re-run with --apply to remove.[/dim]")
        return

    if not confirm(
        f"Move {len(artifacts):,} artifact directories ({human_size(total)}) to the Recycle Bin?",
        assume_yes=yes,
    ):
        warn("Cancelled.")
        return

    with Progress(
        TextColumn("Moving to the Recycle Bin"), BarColumn(), MofNCompleteColumn(),
        TimeRemainingColumn(), console=console, transient=True,
    ) as progress:
        task = progress.add_task("purge", total=len(artifacts))
        result = purge.purge_artifacts(
            artifacts, dry_run=False,
            on_progress=lambda done, _total: progress.update(task, completed=done),
        )
    history.record_clean("purge", str(path), result.bytes_freed, result.items, result.trashed)
    success(f"Sent {result.items:,} directories ({human_size(result.bytes_freed)}) to the Recycle Bin.")
    if result.skipped:
        warn(f"{len(result.skipped):,} skipped (in use or protected).")


class _null:
    def __enter__(self): return self
    def __exit__(self, *_): return False
