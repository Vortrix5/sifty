"""`sifty optimize` - non-destructive system cache cleanup."""

from __future__ import annotations

import typer
from rich.table import Table

from ...console import confirm, console, success, warn
from ...core import optimize
from ...windows.admin import is_admin
from .. import output

app = typer.Typer(no_args_is_help=True, help="Non-destructive system optimization: DNS, caches, Prefetch, DISM.")


@app.command("list")
def list_cmd() -> None:
    """Show available optimization operations and whether they need admin rights."""
    ops = optimize.list_operations()
    if output.json_enabled():
        output.emit([{
            "key": op.key, "label": op.label, "description": op.description,
            "reversible": op.reversible, "requires_admin": op.requires_admin,
        } for op in ops])
        return
    table = Table(title="Optimization operations")
    table.add_column("Key", style="dim")
    table.add_column("Operation")
    table.add_column("Reversible", justify="center")
    table.add_column("Admin?", justify="center")
    for op in ops:
        admin_str = "[yellow]yes[/yellow]" if op.requires_admin else "[green]no[/green]"
        table.add_row(op.key, op.label, op.reversible, admin_str)
    console.print(table)
    console.print("\nRun [cyan]sifty optimize run --apply[/cyan] to execute all safe operations.")


@app.command("run")
def run_cmd(
    apply: bool = typer.Option(False, "--apply", help="Actually execute the operations."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    key: list[str] = typer.Option(None, "--op", "-o", help="Run specific operation key(s) only."),
) -> None:
    """Run optimization operations (dry-run by default).

    Without --apply, shows what each operation would do.  Admin-only operations
    are skipped automatically when not running as administrator.
    """
    ops = optimize.list_operations()
    if key:
        valid = {op.key for op in ops}
        unknown = set(key) - valid
        if unknown:
            warn(f"Unknown operation(s): {', '.join(sorted(unknown))}. "
                 f"Valid keys: {', '.join(sorted(valid))}")
            raise typer.Exit(1)
        ops = [op for op in ops if op.key in set(key)]

    admin = is_admin()
    runnable = [op for op in ops if not op.requires_admin or admin]
    skipped_admin = [op for op in ops if op.requires_admin and not admin]

    if not runnable:
        warn("No operations to run (all require administrator rights - use F2 or --admin to elevate).")
        raise typer.Exit(1)

    if not apply:
        console.print("[bold]Operations that would run:[/bold]")
        for op in runnable:
            console.print(f"  [cyan]{op.key}[/cyan]  {op.description}")
        if skipped_admin:
            console.print(f"\n[dim]{len(skipped_admin):,} admin-only operation(s) skipped "
                          f"(relaunch with --admin to include them).[/dim]")
        console.print("\n[dim]Dry-run - re-run with --apply to execute.[/dim]")
        return

    if not confirm(f"Run {len(runnable):,} optimization operation(s)?", assume_yes=yes):
        warn("Cancelled.")
        return

    for op in runnable:
        console.print(f"  [cyan]{op.key}[/cyan]  {op.label}…", end=" ")
        ok, msg = optimize.run_op(op, dry_run=False)
        if ok:
            console.print(f"[green]{msg}[/green]")
        else:
            console.print(f"[red]failed: {msg}[/red]")

    if skipped_admin:
        warn(f"{len(skipped_admin):,} admin-only operation(s) skipped "
             f"(relaunch with --admin to include them).")
    success("Optimization complete.")
