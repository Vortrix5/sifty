"""`sifty cleanup` - duplicates, large files, and stale downloads."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import typer
from rich.table import Table

from ...console import confirm, console, human_size, success, warn
from ...core import cleanup, disk, history
from .. import output

app = typer.Typer(no_args_is_help=True, help="Smart cleanup: duplicate files, large files, stale downloads.")


@app.command("duplicates")
def duplicates_cmd(
    path: Path = typer.Argument(..., help="Directory to de-duplicate."),
    min_size: int = typer.Option(1024, "--min-size", help="Ignore files smaller than this (bytes)."),
    apply: bool = typer.Option(False, "--apply", help="Trash the redundant copies (keeps one each)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    exclude: list[str] = typer.Option(None, "--exclude", "-x", help="Extra path(s) to never delete."),
    recent_days: int = typer.Option(cleanup.DEFAULT_RECENT_DAYS, "--recent-days",
                                    help="Protect files modified within N days (0 = off)."),
) -> None:
    """Find duplicates and (with --apply) trash all but one copy of each."""
    path = path.expanduser()
    if not path.exists():
        warn(f"Path does not exist: {path}")
        raise typer.Exit(1)

    with console.status(f"Hashing files under {path}…") if not output.json_enabled() else _null():
        groups = disk.find_duplicates(path, min_size)
    to_delete = cleanup.choose_duplicate_deletions(groups, recent_days=recent_days)
    extra = list(exclude) if exclude else None
    preview = cleanup.trash_paths(to_delete, dry_run=True, extra_protected=extra)

    if output.json_enabled():
        output.emit({"groups": len(groups), "redundant": preview.items,
                     "reclaimable_bytes": preview.bytes_freed})
        return
    if preview.items == 0:
        success("No duplicates to remove.")
        return
    console.print(
        f"{len(groups):,} duplicate group(s) · [bold]{preview.items:,}[/bold] redundant copies · "
        f"[bold]{human_size(preview.bytes_freed)}[/bold] reclaimable."
    )
    if not apply:
        console.print("[dim]Dry-run - re-run with --apply to remove the extra copies.[/dim]")
        return
    if not confirm(f"Move {preview.items:,} redundant copies ({human_size(preview.bytes_freed)}) to the Recycle Bin?", assume_yes=yes):
        warn("Cancelled.")
        return
    result = cleanup.trash_paths(to_delete, dry_run=False, extra_protected=extra)
    history.record_clean("cleanup-duplicates", str(path), result.bytes_freed, result.items, result.trashed)
    success(f"Sent {result.items:,} copies ({human_size(result.bytes_freed)}) to the Recycle Bin.")


@app.command("large")
def large_cmd(
    path: Path = typer.Argument(..., help="Directory to scan."),
    min_size: int = typer.Option(cleanup.DEFAULT_LARGE_MIN, "--min-size", help="Minimum file size (bytes)."),
    top: int = typer.Option(30, "--top", "-n", help="How many to show."),
    recent_days: int = typer.Option(cleanup.DEFAULT_RECENT_DAYS, "--recent-days",
                                    help="Omit files modified within N days (0 = off)."),
) -> None:
    """List the biggest files under a path (review, then delete in the TUI)."""
    path = path.expanduser()
    if not path.exists():
        warn(f"Path does not exist: {path}")
        raise typer.Exit(1)

    items = cleanup.find_large_files(path, min_size, top, recent_days=recent_days)
    if output.json_enabled():
        output.emit([{"path": str(p), "size_bytes": s} for p, s in items])
        return
    if not items:
        success(f"No files ≥ {human_size(min_size)} under {path}.")
        return
    table = Table(title=f"Largest files under {path}")
    table.add_column("Size", justify="right")
    table.add_column("Path")
    for p, s in items:
        table.add_row(human_size(s), str(p))
    console.print(table)


@app.command("stale")
def stale_cmd(
    days: int = typer.Option(cleanup.DEFAULT_STALE_DAYS, "--days", help="Older-than threshold (days)."),
    apply: bool = typer.Option(False, "--apply", help="Trash the stale items."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    exclude: list[str] = typer.Option(None, "--exclude", "-x", help="Extra path(s) to never delete."),
) -> None:
    """Find old items in Downloads and (with --apply) trash them."""
    items = cleanup.find_stale_downloads(days)
    if output.json_enabled():
        output.emit([
            {"path": str(p), "size_bytes": s, "modified": datetime.fromtimestamp(m).isoformat()}
            for p, s, m in items
        ])
        return
    if not items:
        success(f"No items in Downloads older than {days} days.")
        return
    total = sum(s for _p, s, _m in items)
    table = Table(title=f"Downloads older than {days} days")
    table.add_column("Size", justify="right")
    table.add_column("Modified", style="dim")
    table.add_column("Name")
    for p, s, m in items:
        table.add_row(human_size(s), datetime.fromtimestamp(m).strftime("%Y-%m-%d"), p.name)
    console.print(table)
    console.print(f"\n[bold]{len(items):,} items · {human_size(total)}[/bold]")
    if not apply:
        console.print("[dim]Dry-run - re-run with --apply to remove them.[/dim]")
        return
    if not confirm(f"Move {len(items):,} stale items ({human_size(total)}) to the Recycle Bin?", assume_yes=yes):
        warn("Cancelled.")
        return
    extra = list(exclude) if exclude else None
    result = cleanup.trash_paths([p for p, _s, _m in items], dry_run=False, extra_protected=extra)
    history.record_clean("cleanup-stale", f"Downloads >{days}d", result.bytes_freed, result.items, result.trashed)
    success(f"Sent {result.items:,} items ({human_size(result.bytes_freed)}) to the Recycle Bin.")


@app.command("worktrees")
def worktrees_cmd(
    path: Path = typer.Argument(..., help="Git repository root to inspect."),
    apply: bool = typer.Option(False, "--apply", help="Prune git metadata and trash orphaned dirs."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Find and remove orphaned git worktrees in a repository."""
    from ...core.vcs import find_orphan_worktrees, prune_worktrees

    path = path.expanduser()
    if not path.exists():
        warn(f"Path does not exist: {path}")
        raise typer.Exit(1)

    with console.status(f"Scanning worktrees in {path}…") if not output.json_enabled() else _null():
        orphans = find_orphan_worktrees(path)

    if output.json_enabled():
        output.emit([{"path": str(o.path), "head": o.head, "reason": o.reason} for o in orphans])
        return

    if not orphans:
        success("No orphaned worktrees found.")
        return

    table = Table(title=f"Orphaned worktrees in {path}")
    table.add_column("Path")
    table.add_column("HEAD", style="dim")
    table.add_column("Reason", style="dim")
    for o in orphans:
        table.add_row(str(o.path), o.head, o.reason)
    console.print(table)

    if not apply:
        console.print("[dim]Dry-run - re-run with --apply to prune and trash them.[/dim]")
        return
    if not confirm(f"Prune {len(orphans):,} orphaned worktree(s)?", assume_yes=yes):
        warn("Cancelled.")
        return
    result = prune_worktrees(path, dry_run=False)
    history.record_clean("cleanup-worktrees", str(path), result.bytes_freed, result.items, result.trashed)
    success(f"Pruned {result.items:,} worktree(s) ({human_size(result.bytes_freed)}).")


class _null:
    """No-op context manager (used to skip the status spinner in JSON mode)."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
