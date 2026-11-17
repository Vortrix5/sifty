"""Tests for the scheduler primitive and schedule orchestration (schtasks mocked)."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from sifty.core import schedule
from sifty.windows import scheduler


def test_scheduler_create_builds_schtasks_args(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return SimpleNamespace(returncode=0, stdout="SUCCESS", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, _msg = scheduler.create("weekly", "cmd here", sc="WEEKLY", day="SUN", time="03:00")
    assert ok is True
    args = captured["args"]
    assert args[:2] == ["schtasks", "/Create"]
    assert "/TN" in args and "\\Sifty\\weekly" in args
    assert "/SC" in args and "WEEKLY" in args
    assert "/D" in args and "SUN" in args


def test_scheduler_query_filters_sifty_folder(monkeypatch):
    csv = '"\\Sifty\\weekly","3:00:00 AM","Ready"\n"\\Microsoft\\Other","N/A","Ready"'
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=csv, stderr=""),
    )
    assert scheduler.query() == ["weekly"]


@pytest.fixture
def temp_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def test_schedule_add_records_and_lists(temp_appdata, monkeypatch):
    monkeypatch.setattr(scheduler, "create", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(scheduler, "query", lambda: ["weekly"])

    ok, _ = schedule.add("weekly", "myprofile", "cmd", "WEEKLY", "SUN", "03:00")
    assert ok is True
    rows = schedule.list_schedules()
    assert len(rows) == 1
    assert rows[0]["profile"] == "myprofile" and rows[0]["active"] is True


def test_schedule_remove(temp_appdata, monkeypatch):
    monkeypatch.setattr(scheduler, "create", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(scheduler, "delete", lambda name: True)
    monkeypatch.setattr(scheduler, "query", lambda: [])
    schedule.add("weekly", "p", "cmd", "WEEKLY", "SUN", "03:00")
    assert schedule.remove("weekly") is True
    assert schedule.list_schedules() == []


def test_sifty_command_runs_clean_profile():
    cmd = schedule.sifty_command("weekly")
    assert "-m sifty clean --profile" in cmd and "--apply --yes" in cmd


def test_agent_command_is_background_and_not_apply():
    cmd = schedule.agent_command()
    assert "-m sifty agent run --background" in cmd
    assert "--apply" not in cmd  # the scheduled task is never forced to delete
