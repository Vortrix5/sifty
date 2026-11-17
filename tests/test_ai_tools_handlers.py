"""Tests for the AI tool handlers not covered in test_tools.py.

The lazily-imported core functions inside each handler are monkeypatched, so
nothing touches the real system.
"""

from __future__ import annotations

from types import SimpleNamespace

from sifty.ai import tools
from sifty.core import (
    apps,
    disk,
    junk,
    monitor,
    optimize,
    purge,
    registry_scan,
    schedule,
    startup,
    vcs,
)
from sifty.core.monitor import ProcInfo, SystemSnapshot
from sifty.core.optimize import OptimizeOp
from sifty.windows import admin

# --- scan_junk / clean_junk ------------------------------------------------


def test_scan_junk_empty(monkeypatch):
    monkeypatch.setattr(junk, "scan", lambda: [])
    res = tools.get("scan_junk").handler({})
    assert not res.has_table
    assert "already tidy" in res.summary


def test_clean_junk_nothing(monkeypatch):
    monkeypatch.setattr(junk, "clean", lambda only=None, dry_run=True: SimpleNamespace(items=0, bytes_freed=0))
    res = tools.get("clean_junk").handler({"categories": []})
    assert "Nothing was cleaned" in res.summary


def test_clean_junk_removes(monkeypatch):
    captured = {}

    def clean(only=None, dry_run=True):
        captured["only"] = only
        captured["dry_run"] = dry_run
        return SimpleNamespace(items=5, bytes_freed=2048)

    monkeypatch.setattr(junk, "clean", clean)
    res = tools.get("clean_junk").handler({"categories": ["user-temp"]})
    assert "Cleaned 5 items" in res.summary
    assert captured["only"] == {"user-temp"}
    assert captured["dry_run"] is False


# --- analyze_disk ----------------------------------------------------------


def test_analyze_disk_missing_path():
    res = tools.get("analyze_disk").handler({"path": "Z:/does/not/exist"})
    assert "does not exist" in res.summary


def test_analyze_disk_empty(tmp_path):
    res = tools.get("analyze_disk").handler({"path": str(tmp_path)})
    assert "No files found" in res.summary


def test_analyze_disk_os_error(monkeypatch, tmp_path):
    monkeypatch.setattr(disk, "biggest", lambda p, n: (_ for _ in ()).throw(OSError("denied")))
    res = tools.get("analyze_disk").handler({"path": str(tmp_path)})
    assert "Could not read" in res.summary


# --- find_duplicates -------------------------------------------------------


def test_find_duplicates_none(monkeypatch, tmp_path):
    monkeypatch.setattr(disk, "find_duplicates", lambda p, **k: {})
    res = tools.get("find_duplicates").handler({"path": str(tmp_path)})
    assert "No duplicate files" in res.summary


def test_find_duplicates_scan_os_error(monkeypatch, tmp_path):
    monkeypatch.setattr(disk, "find_duplicates", lambda p, **k: (_ for _ in ()).throw(OSError("denied")))
    res = tools.get("find_duplicates").handler({"path": str(tmp_path)})
    assert "Could not scan" in res.summary


def test_find_duplicates_stat_error_size_zero(monkeypatch, tmp_path):
    # Group of files that don't exist → g[0].stat() raises → size 0, still reported.
    ghosts = [tmp_path / "g1.bin", tmp_path / "g2.bin"]
    monkeypatch.setattr(disk, "find_duplicates", lambda p, **k: {"h": ghosts})
    res = tools.get("find_duplicates").handler({"path": str(tmp_path)})
    assert res.has_table
    assert res.rows[0][0] == "2"


# --- list_apps -------------------------------------------------------------


def test_list_apps_none(monkeypatch):
    monkeypatch.setattr(apps, "installed_apps", lambda: [])
    res = tools.get("list_apps").handler({})
    assert "No installed apps" in res.summary


# --- uninstall_app ---------------------------------------------------------


def test_uninstall_app_failure(monkeypatch):
    monkeypatch.setattr(apps, "uninstall_app", lambda name: (False, "not found"))
    res = tools.get("uninstall_app").handler({"name": "Ghost"})
    assert "failed" in res.summary


# --- toggle_startup --------------------------------------------------------


def test_toggle_startup_failure(monkeypatch):
    monkeypatch.setattr(startup, "set_enabled", lambda name, enabled: False)
    res = tools.get("toggle_startup").handler({"name": "Ghost", "enable": True})
    assert "Could not enable" in res.summary


# --- schedule_maintenance --------------------------------------------------


def test_schedule_maintenance_no_profile():
    res = tools.get("schedule_maintenance").handler({"name": "t", "frequency": "DAILY", "time": "03:00"})
    assert "no profile" in res.summary


def test_schedule_maintenance_weekly_success(monkeypatch):
    monkeypatch.setattr(schedule, "sifty_command", lambda profile: f"sifty clean --profile {profile}")
    monkeypatch.setattr(schedule, "add", lambda *a, **k: (True, "created"))
    res = tools.get("schedule_maintenance").handler(
        {"name": "wk", "profile": "deep", "frequency": "WEEKLY", "day": "SUN", "time": "02:00"}
    )
    assert "Scheduled 'wk'" in res.summary
    assert "Weekly SUN" in res.summary


def test_schedule_maintenance_failure(monkeypatch):
    monkeypatch.setattr(schedule, "sifty_command", lambda profile: "cmd")
    monkeypatch.setattr(schedule, "add", lambda *a, **k: (False, "schtasks error"))
    res = tools.get("schedule_maintenance").handler(
        {"name": "t", "profile": "p", "frequency": "DAILY", "time": "03:00"}
    )
    assert "Failed to create" in res.summary


# --- prune_worktrees -------------------------------------------------------


def test_prune_worktrees_missing_path():
    res = tools.get("prune_worktrees").handler({"path": "Z:/nope"})
    assert "does not exist" in res.summary


def test_prune_worktrees_none(monkeypatch, tmp_path):
    monkeypatch.setattr(vcs, "find_orphan_worktrees", lambda p: [])
    res = tools.get("prune_worktrees").handler({"path": str(tmp_path)})
    assert "No orphaned worktrees" in res.summary


def test_prune_worktrees_prunes(monkeypatch, tmp_path):
    monkeypatch.setattr(vcs, "find_orphan_worktrees", lambda p: [object()])
    monkeypatch.setattr(
        vcs, "prune_worktrees", lambda p, dry_run=True: SimpleNamespace(items=2, bytes_freed=4096, skipped=["x"])
    )
    res = tools.get("prune_worktrees").handler({"path": str(tmp_path)})
    assert "Pruned 2" in res.summary
    assert "1 skipped" in res.summary


# --- find_orphan_apps ------------------------------------------------------


def test_find_orphan_apps_none(monkeypatch):
    monkeypatch.setattr(registry_scan, "find_orphan_uninstall_entries", lambda: [])
    res = tools.get("find_orphan_apps").handler({})
    assert "looks clean" in res.summary


def test_find_orphan_apps_table(monkeypatch):
    entry = SimpleNamespace(display_name="Ghost App", reason="missing executable", hive="HKLM")
    monkeypatch.setattr(registry_scan, "find_orphan_uninstall_entries", lambda: [entry])
    res = tools.get("find_orphan_apps").handler({})
    assert res.has_table
    assert res.rows[0] == ["Ghost App", "missing executable", "HKLM"]


# --- scan / purge artifacts ------------------------------------------------


def test_scan_artifacts_missing_path():
    res = tools.get("scan_project_artifacts").handler({"path": "Z:/nope"})
    assert "does not exist" in res.summary


def test_scan_artifacts_none(monkeypatch, tmp_path):
    monkeypatch.setattr(purge, "scan_artifacts", lambda p: [])
    res = tools.get("scan_project_artifacts").handler({"path": str(tmp_path)})
    assert "No artifact directories" in res.summary


def test_scan_artifacts_table(monkeypatch, tmp_path):
    artifact = SimpleNamespace(pattern="node_modules", size_bytes=5000, path=tmp_path / "p" / "node_modules")
    monkeypatch.setattr(purge, "scan_artifacts", lambda p: [artifact])
    res = tools.get("scan_project_artifacts").handler({"path": str(tmp_path)})
    assert res.has_table
    assert res.rows[0][0] == "node_modules"


def test_purge_artifacts_missing_path():
    res = tools.get("purge_artifacts").handler({"path": "Z:/nope"})
    assert "does not exist" in res.summary


def test_purge_artifacts_none(monkeypatch, tmp_path):
    monkeypatch.setattr(purge, "scan_artifacts", lambda p: [])
    res = tools.get("purge_artifacts").handler({"path": str(tmp_path)})
    assert "No artifact directories" in res.summary


def test_purge_artifacts_purges(monkeypatch, tmp_path):
    artifact = SimpleNamespace(pattern="dist", size_bytes=1000, path=tmp_path / "dist")
    monkeypatch.setattr(purge, "scan_artifacts", lambda p: [artifact])
    monkeypatch.setattr(
        purge, "purge_artifacts", lambda paths, dry_run=True: SimpleNamespace(items=1, bytes_freed=1000, skipped=[])
    )
    res = tools.get("purge_artifacts").handler({"path": str(tmp_path)})
    assert "Purged 1" in res.summary


# --- optimize_system -------------------------------------------------------


def test_optimize_system_no_ops_without_admin(monkeypatch):
    monkeypatch.setattr(admin, "is_admin", lambda: False)
    monkeypatch.setattr(
        optimize, "list_operations", lambda: [OptimizeOp("dism", "DISM", "d", "x", requires_admin=True)]
    )
    res = tools.get("optimize_system").handler({})
    assert "No operations available" in res.summary


def test_optimize_system_runs(monkeypatch):
    monkeypatch.setattr(admin, "is_admin", lambda: True)
    ops = [OptimizeOp("dns", "Flush DNS", "d", "instant"), OptimizeOp("thumb", "Thumbnails", "d", "auto")]
    monkeypatch.setattr(optimize, "list_operations", lambda: ops)
    monkeypatch.setattr(optimize, "run_op", lambda op, dry_run=True: (True, "done"))
    res = tools.get("optimize_system").handler({})
    assert res.has_table
    assert len(res.rows) == 2


# --- system_status ---------------------------------------------------------


def _snapshot(processes):
    return SystemSnapshot(
        cpu_percent=25.0,
        memory_used_gb=8.0,
        memory_total_gb=16.0,
        memory_percent=50.0,
        disk_read_bytes=1024,
        disk_write_bytes=2048,
        net_sent_bytes=512,
        net_recv_bytes=256,
        processes=processes,
    )


def test_system_status_with_processes(monkeypatch):
    procs = [ProcInfo(1, "chrome", 50.0, 200.0), ProcInfo(2, "code", 10.0, 100.0)]
    monkeypatch.setattr(monitor, "snapshot", lambda: _snapshot(procs))
    res = tools.get("system_status").handler({})
    assert res.has_table
    assert len(res.rows) == 2
    assert "CPU: 25%" in res.summary
    assert "chrome" in res.summary  # top process named


def test_system_status_no_processes(monkeypatch):
    monkeypatch.setattr(monitor, "snapshot", lambda: _snapshot([]))
    res = tools.get("system_status").handler({})
    assert not res.has_table  # no rows


# --- registry helpers ------------------------------------------------------


def test_ollama_schemas_and_lookup():
    schemas = tools.ollama_schemas()
    assert schemas and all(s["type"] == "function" for s in schemas)
    assert tools.get("scan_junk").to_ollama()["function"]["name"] == "scan_junk"
    assert tools.get("does-not-exist") is None
