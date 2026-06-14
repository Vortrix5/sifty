"""`sifty organize` - sort files in a folder by type or date."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from ...console import confirm, console, success, warn
from ...core import organize

app = typer.Typer(no_args_is_help=True, help="Organize files in a folder by type or date.")


@app.command("preview")
def preview_cmd(
    path: Path = typer.Argument(..., help="Folder to organize."),
    scheme: str = typer.Option("type", "--by", help="'type' or 'date'."),
) -> None:
    """Preview how files would be reorganized (no changes made)."""
    _run(path, scheme, apply=False, yes=False)


@app.command("apply")
def apply_cmd(
    path: Path = typer.Argument(..., help="Folder to organize."),
    scheme: str = typer.Option("type", "--by", help="'type' or 'date'."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Reorganize files into subfolders by type or date."""
    _run(path, scheme, apply=True, yes=yes)


def _run(path: Path, scheme: str, *, apply: bool, yes: bool) -> None:
    path = path.expanduser()
    if not path.is_dir():
        warn(f"Not a directory: {path}")
        raise typer.Exit(1)
    if scheme not in {"type", "date"}:
        warn("--by must be 'type' or 'date'.")
        raise typer.Exit(1)

    moves = organize.plan_organization(path, scheme)
    if not moves:
        success("Nothing to organize - all files are already sorted.")
        return

    table = Table(title=f"Organize '{path}' by {scheme} ({len(moves):,} files)")
    table.add_column("File")
    table.add_column("→ Folder", style="cyan")
    for m in moves[:50]:
        table.add_row(m.src.name, m.dest.parent.name)
    console.print(table)
    if len(moves) > 50:
        console.print(f"[dim]…and {len(moves) - 50:,} more.[/dim]")

    if not apply:
        console.print("[dim]Dry-run - nothing moved. Re-run with 'organize apply' to perform.[/dim]")
        return

    if not confirm(f"Move {len(moves):,} files into subfolders?", assume_yes=yes):
        warn("Cancelled.")
        return

    done = organize.apply_moves(moves)
    success(f"Organized {done:,} files. Undo with [cyan]sifty organize undo[/cyan].")


@app.command("undo")
def undo_cmd(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Move the files from the last 'organize apply' back where they were."""
    pairs = organize.last_session()
    if not pairs:
        warn("Nothing to undo - no organize session recorded.")
        return
    if not confirm(f"Move {len(pairs):,} file(s) back to their original locations?", assume_yes=yes):
        warn("Cancelled.")
        return
    restored, failed = organize.undo_last()
    success(f"Restored {restored:,} file(s).")
    if failed:
        warn(f"{failed:,} file(s) could not be restored (moved or replaced since).")
