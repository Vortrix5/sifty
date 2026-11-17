"""`sifty watch` - warn (and toast) when a volume runs low on free space."""

from __future__ import annotations

import typer

from ...console import error, human_size, success, warn
from ...core import schedule, watch
from ...windows import notify, scheduler
from .. import output

app = typer.Typer(no_args_is_help=True, help="Watch free disk space; toast when it gets low.")


@app.command("check")
def check_cmd(
    threshold: int = typer.Option(None, "--threshold", help="Free-space threshold in GB (default: config)."),
) -> None:
    """Check volumes now; toast if any is below the threshold (used by the task)."""
    lows = watch.low_space(threshold)
    thr = watch.threshold_gb(threshold)
    if output.json_enabled():
        output.emit({
            "threshold_gb": thr,
            "low": [{"drive": v.mountpoint, "free": v.free, "total": v.total} for v in lows],
        })
        return
    if not lows:
        success(f"All volumes have more than {thr} GB free.")
        return
    detail = " · ".join(f"{v.mountpoint} {human_size(v.free)} free" for v in lows)
    notify.toast("Low disk space", detail)
    warn(f"Low disk space - {detail}")


@app.command("schedule")
def schedule_cmd(
    threshold: int = typer.Option(5, "--threshold", help="Free-space threshold in GB."),
    sc: str = typer.Option("DAILY", "--sc", help="DAILY or WEEKLY."),
    time: str = typer.Option("09:00", "--time", help="Start time, HH:MM (24h)."),
) -> None:
    """Schedule a periodic low-disk-space check (toasts when low)."""
    ok, message = scheduler.create("watch", schedule.watch_command(threshold), sc.upper(), "SUN", time)
    if ok:
        success(f"Scheduled low-disk watch ({sc.lower()} {time}, alert below {threshold} GB).")
    else:
        error(f"Failed to create task: {message}")
        raise typer.Exit(1)


@app.command("unschedule")
def unschedule_cmd() -> None:
    """Remove the scheduled low-disk-space check."""
    if scheduler.delete("watch"):
        success("Removed the low-disk watch task.")
    else:
        error("No watch task to remove.")
        raise typer.Exit(1)
