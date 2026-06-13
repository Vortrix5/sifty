"""`sifty junk` - scan and clean junk files."""

from __future__ import annotations

import typer
from rich.table import Table

from ...console import confirm, console, human_size, success, warn
from ...core import history, junk
from .. import output

app = typer.Typer(no_args_is_help=True, help="Scan and clean junk files (temp, caches, update cache).")


@app.command("scan")
def scan_cmd(
    category: list[str] = typer.Option(None, "--category", "-c", help="Limit to category key(s)."),
) -> None:
    """Show how much junk each category holds, without deleting anything."""
    only = set(category) if category else None
    results = junk.scan(only=only)

    if output.json_enabled():
        output.emit([
            {
                "key": r.category.key,
                "label": r.category.label,
                "files": r.file_count,
                "size_bytes": r.size,
                "requires_admin": r.category.requires_admin,
            }
            for r in results
        ])
        return

    table = Table(title="Junk scan")
    table.add_column("Category")
    table.add_column("Key", style="dim")
    table.add_column("Files", justify="right")
    table.add_column("Size", justify="right")
    total = 0
    for r in results:
        total += r.size
        admin = " [yellow](admin)[/yellow]" if r.category.requires_admin else ""
        table.add_row(r.category.label + admin, r.category.key, f"{r.file_count:,}", human_size(r.size))
    table.add_section()
    table.add_row("[bold]Total reclaimable[/bold]", "", "", f"[bold]{human_size(total)}[/bold]")
    console.print(table)
    console.print("\nRun [cyan]sifty junk clean[/cyan] to preview removal (dry-run by default).")


@app.command("clean")
def clean_cmd(
    category: list[str] = typer.Option(None, "--category", "-c", help="Limit to category key(s)."),
    apply: bool = typer.Option(False, "--apply", help="Actually move items to the Recycle Bin."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    exclude: list[str] = typer.Option(None, "--exclude", "-x", help="Extra path(s) to never delete."),
) -> None:
    """Move junk to the Recycle Bin. Dry-run unless --apply is given."""
    only = set(category) if category else None
    extra_protected = list(exclude) if exclude else None

    preview = junk.clean(only=only, dry_run=True, extra_protected=extra_protected)
    if preview.items == 0:
        success("Nothing to clean - you're already tidy.")
        return

    console.print(
        f"Found [bold]{preview.items:,}[/bold] items totalling "
        f"[bold]{human_size(preview.bytes_freed)}[/bold]."
    )
    if not apply:
        console.print("[dim]Dry-run - nothing was deleted. Re-run with --apply to remove.[/dim]")
        return

    if not confirm(f"Move {preview.items:,} items ({human_size(preview.bytes_freed)}) to the Recycle Bin?", assume_yes=yes):
        warn("Cancelled.")
        return

    result = junk.clean(only=only, dry_run=False, extra_protected=extra_protected)
    history.record_clean(
        "junk", ",".join(sorted(only)) if only else "all",
        result.bytes_freed, result.items, result.trashed,
    )
    success(f"Sent {result.items:,} items ({human_size(result.bytes_freed)}) to the Recycle Bin.")
    if result.skipped:
        warn(f"{len(result.skipped):,} item(s) skipped (in use or protected).")
