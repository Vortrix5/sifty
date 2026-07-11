"""Scheduling orchestration: register cleanup profiles as Windows tasks.

The actual task is created via the ``windows.scheduler`` primitive; we keep a
small local record (``%APPDATA%\\sifty\\schedules.json``) mapping a task name to
the profile + human-readable schedule, since Task Scheduler doesn't store that.
"""

from __future__ import annotations

import json
import sys

from ..infra.config import app_data_dir
from ..windows import scheduler

__all__ = ["sifty_command", "watch_command", "agent_command", "add", "remove", "list_schedules"]


def _file():
    return app_data_dir() / "schedules.json"


def _load() -> dict:
    path = _file()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def _save(data: dict) -> None:
    try:
        _file().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def sifty_command(profile: str) -> str:
    """The command a scheduled task runs to clean a profile."""
    return f'"{sys.executable}" -m sifty clean --profile "{profile}" --apply --yes'


def watch_command(threshold_gb: int) -> str:
    """The command a scheduled task runs to check free space and toast."""
    return f'"{sys.executable}" -m sifty watch check --threshold {threshold_gb}'


def agent_command() -> str:
    """The command a scheduled task runs for a proactive maintenance pass.

    No --apply: whether it auto-cleans is driven by the [agent].auto_fix config,
    so the task itself is inert-by-default and can't be made destructive by the
    schedule alone.
    """
    return f'"{sys.executable}" -m sifty agent run --background'


def add(name: str, profile: str, command: str, sc: str, day: str, time: str) -> tuple[bool, str]:
    """Register a task and record its profile + schedule. Returns (ok, message)."""
    ok, message = scheduler.create(name, command, sc=sc, day=day, time=time)
    if ok:
        data = _load()
        when = f"{sc.title()} {day} {time}" if sc.upper() == "WEEKLY" else f"{sc.title()} {time}"
        data[name] = {"profile": profile, "schedule": when}
        _save(data)
    return ok, message


def remove(name: str) -> bool:
    ok = scheduler.delete(name)
    data = _load()
    if name in data:
        del data[name]
        _save(data)
    return ok


def list_schedules() -> list[dict]:
    """Recorded schedules, flagged with whether the task still exists."""
    data = _load()
    live = set(scheduler.query())
    return [
        {"name": name, "profile": spec.get("profile", ""),
         "schedule": spec.get("schedule", ""), "active": name in live}
        for name, spec in sorted(data.items())
    ]
