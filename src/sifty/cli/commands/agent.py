"""`sifty agent` - proactive maintenance: checkup, notify, and optional auto-fix.

`agent run` does a read-only checkup + anomaly scan and toasts a summary. It only
DELETES when told to: a foreground run needs an explicit ``--apply``; the
scheduled ``--background`` run auto-cleans only if ``[agent].auto_fix`` is on in
the config. Either way it touches just the hardcoded low-risk allowlist in
``core/agent_run.py`` and routes through the Recycle Bin.
"""

from __future__ import annotations

import typer

from ...console import console, error, human_size, success
from ...core import agent_run, schedule
from ...infra.config import load_config
from ...windows import scheduler
from .. import output

app = typer.Typer(
    no_args_is_help=True,
    help="Proactive maintenance agent: checkup, notify, and optional auto-fix.",
)


@app.command("run")
def run_cmd(
    apply: bool = typer.Option(
        False, "--apply", help="Auto-clean approved low-risk junk now (foreground opt-in)."
    ),
    background: bool = typer.Option(
        False, "--background", help="Quiet mode for the scheduler: toast only, no console output."
    ),
) -> None:
    """Run a proactive checkup; toast a summary or auto-fix approved low-risk junk."""
    config = load_config()
    # config auto_fix only takes effect for the scheduled/background run; a
    # foreground run must opt in explicitly with --apply.
    if background:
        do_apply = bool(config.section("agent").get("auto_fix", False))
    else:
        do_apply = apply
    result = agent_run.run_agent(apply=do_apply, config=config)

    if background:
        return  # the toast (if any) is the whole output

    if output.json_enabled():
        output.emit(_result_json(result))
        return

    _print_result(result)


def _result_json(result) -> dict:
    return {
        "headline": result.headline,
        "notified": result.notified,
        "findings": [
            {"domain": f.domain, "summary": f.summary, "severity": f.severity}
            for f in result.findings
        ],
        "anomalies": [
            {"kind": a.kind, "summary": a.summary, "severity": a.severity}
            for a in result.anomalies
        ],
        "auto_fixed": (
            {
                "categories": result.auto_fixed.category_keys,
                "items": result.auto_fixed.items,
                "bytes_freed": result.auto_fixed.bytes_freed,
            }
            if result.auto_fixed else None
        ),
    }


_DOT = {"attention": "[red]●[/red]", "info": "[yellow]●[/yellow]", "ok": "[green]●[/green]"}


def _print_result(result) -> None:
    from rich.table import Table

    if result.auto_fixed:
        a = result.auto_fixed
        success(f"Auto-cleaned {a.items:,} items ({human_size(a.bytes_freed)}) to the Recycle Bin.")

    table = Table(title="Checkup", title_style="bold")
    for col in ("Area", "Result", "Severity"):
        table.add_column(col)
    for f in result.findings:
        table.add_row(f.label, f.summary, f"{_DOT.get(f.severity, '')} {f.severity}")
    console.print(table)

    if result.anomalies:
        console.print("\n[b]Recent changes:[/b]")
        for a in result.anomalies:
            console.print(f"  {_DOT.get(a.severity, '')} {a.summary}")

    if result.elevated_skipped:
        console.print(
            f"\n[dim]{len(result.elevated_skipped)} admin-only categor(y/ies) left alone; "
            f"run `sifty --admin junk clean` to include them.[/dim]"
        )

    if not result.auto_fixed and result.headline:
        console.print(f"\n[dim]{result.headline}[/dim]")


@app.command("schedule")
def schedule_cmd(
    sc: str = typer.Option("DAILY", "--sc", help="DAILY or WEEKLY."),
    day: str = typer.Option("SUN", "--day", help="Day for WEEKLY (MON..SUN)."),
    time: str = typer.Option("09:00", "--time", help="Start time, HH:MM (24h)."),
) -> None:
    """Schedule the proactive agent to run periodically."""
    ok, message = scheduler.create("agent", schedule.agent_command(), sc.upper(), day.upper(), time)
    if ok:
        success(f"Scheduled the maintenance agent ({sc.lower()} {time}).")
        auto = load_config().section("agent").get("auto_fix", False)
        mode = "auto-clean low-risk junk" if auto else "notify only"
        console.print(f"[dim]Mode: {mode} (set [agent] auto_fix in your config to change).[/dim]")
    else:
        error(f"Failed to create task: {message}")
        raise typer.Exit(1)


@app.command("unschedule")
def unschedule_cmd() -> None:
    """Remove the scheduled maintenance agent."""
    if scheduler.delete("agent"):
        success("Removed the maintenance agent task.")
    else:
        error("No agent task to remove.")
        raise typer.Exit(1)
