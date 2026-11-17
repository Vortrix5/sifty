"""CliRunner coverage for read-only CLI commands (table + --json + validation).

Core functions are monkeypatched, so invoking commands does no real OS work.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from sifty.cli.app import app
from sifty.core.checkup import Finding
from sifty.core.models import (
    CategoryScan,
    InstalledApp,
    JunkCategory,
    Profile,
    Run,
    ServiceInfo,
    StartupEntry,
    Upgrade,
    VolumeUsage,
)

runner = CliRunner()


@pytest.fixture(autouse=True)
def _sandbox_appdata(monkeypatch, tmp_path):
    # Keep config/profiles/history/logs out of the real %APPDATA%.
    monkeypatch.setenv("APPDATA", str(tmp_path))


def _json(result):
    return json.loads(result.stdout)


# --- junk ------------------------------------------------------------------


def test_junk_scan_table(monkeypatch):
    cats = [CategoryScan(JunkCategory("user-temp", "User temp", "", requires_admin=True), 2048, 5, [])]
    monkeypatch.setattr("sifty.core.junk.scan", lambda only=None: cats)
    result = runner.invoke(app, ["junk", "scan"])
    assert result.exit_code == 0
    assert "User temp" in result.stdout


def test_junk_scan_json(monkeypatch):
    cats = [CategoryScan(JunkCategory("user-temp", "User temp", ""), 2048, 5, [])]
    monkeypatch.setattr("sifty.core.junk.scan", lambda only=None: cats)
    result = runner.invoke(app, ["--json", "junk", "scan"])
    assert result.exit_code == 0
    assert _json(result)[0]["key"] == "user-temp"


def test_junk_clean_dry_run(monkeypatch):
    from sifty.core.models import CleanResult

    monkeypatch.setattr("sifty.core.junk.clean", lambda **k: CleanResult(2048, 5, [], []))
    result = runner.invoke(app, ["junk", "clean"])
    assert result.exit_code == 0
    assert "Dry-run" in result.stdout


def test_junk_clean_nothing(monkeypatch):
    from sifty.core.models import CleanResult

    monkeypatch.setattr("sifty.core.junk.clean", lambda **k: CleanResult(0, 0, [], []))
    result = runner.invoke(app, ["junk", "clean"])
    assert result.exit_code == 0
    assert "already tidy" in result.stdout


# --- disk ------------------------------------------------------------------


def test_disk_volumes_table(monkeypatch):
    vols = [VolumeUsage("C:", "C:\\", "NTFS", 100, 60, 40)]
    monkeypatch.setattr("sifty.core.disk.volumes", lambda: vols)
    result = runner.invoke(app, ["disk", "volumes"])
    assert result.exit_code == 0
    assert "C:" in result.stdout


def test_disk_analyze_missing_path():
    result = runner.invoke(app, ["disk", "analyze", "Z:/does/not/exist"])
    assert result.exit_code == 1


def test_disk_analyze_table(monkeypatch, tmp_path):
    (tmp_path / "big.bin").write_bytes(b"x" * 100)
    monkeypatch.setattr("sifty.core.disk.biggest", lambda p, n: [(tmp_path / "big.bin", 100)])
    result = runner.invoke(app, ["disk", "analyze", str(tmp_path)])
    assert result.exit_code == 0
    assert "big.bin" in result.stdout


def test_disk_duplicates_json(monkeypatch, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"x" * 50)
    b.write_bytes(b"x" * 50)
    monkeypatch.setattr("sifty.core.disk.find_duplicates", lambda p, m: {"h": [a, b]})
    result = runner.invoke(app, ["--json", "disk", "duplicates", str(tmp_path)])
    assert result.exit_code == 0
    assert _json(result)["groups"][0]["copies"] == 2


def test_disk_duplicates_none(monkeypatch, tmp_path):
    monkeypatch.setattr("sifty.core.disk.find_duplicates", lambda p, m: {})
    result = runner.invoke(app, ["disk", "duplicates", str(tmp_path)])
    assert result.exit_code == 0
    assert "No duplicates" in result.stdout


# --- apps ------------------------------------------------------------------


def test_apps_list_table(monkeypatch):
    items = [InstalledApp("App A", "1.0", "Pub", 5000, "", "HKCU")]
    monkeypatch.setattr("sifty.core.apps.installed_apps", lambda: items)
    result = runner.invoke(app, ["apps", "list", "--by-size"])
    assert result.exit_code == 0
    assert "App A" in result.stdout


def test_apps_list_json_limit(monkeypatch):
    items = [InstalledApp(f"App{i}", "1.0", "Pub", i * 1000, "", "HKCU") for i in range(5)]
    monkeypatch.setattr("sifty.core.apps.installed_apps", lambda: items)
    result = runner.invoke(app, ["--json", "apps", "list", "--limit", "2"])
    assert result.exit_code == 0
    assert len(_json(result)) == 2


def test_apps_startup_json(monkeypatch):
    entries = [StartupEntry("Spotify", "spotify.exe", "HKCU Run")]
    monkeypatch.setattr("sifty.core.apps.startup_entries", lambda: entries)
    result = runner.invoke(app, ["--json", "apps", "startup"])
    assert result.exit_code == 0
    assert _json(result)[0]["name"] == "Spotify"


def test_apps_orphans_none(monkeypatch):
    monkeypatch.setattr("sifty.core.registry_scan.find_orphan_uninstall_entries", lambda: [])
    result = runner.invoke(app, ["apps", "orphans"])
    assert result.exit_code == 0
    assert "No orphaned" in result.stdout


def test_apps_orphans_table(monkeypatch):
    from sifty.core.registry_scan import OrphanEntry

    entries = [OrphanEntry("HKLM", "k", "Ghost App", "C:/x.exe", "missing executable")]
    monkeypatch.setattr("sifty.core.registry_scan.find_orphan_uninstall_entries", lambda: entries)
    result = runner.invoke(app, ["apps", "orphans"])
    assert result.exit_code == 0
    assert "Ghost App" in result.stdout


def test_apps_leftovers_none(monkeypatch):
    monkeypatch.setattr("sifty.core.leftovers.find_leftovers", lambda name, pub="": [])
    result = runner.invoke(app, ["apps", "leftovers", "Ghost"])
    assert result.exit_code == 0
    assert "No leftovers" in result.stdout


def test_apps_leftovers_table(monkeypatch, tmp_path):
    from sifty.core.leftovers import Leftover

    items = [Leftover(tmp_path / "Ghost", 1000, "data-dir")]
    monkeypatch.setattr("sifty.core.leftovers.find_leftovers", lambda name, pub="": items)
    result = runner.invoke(app, ["apps", "leftovers", "Ghost"])
    assert result.exit_code == 0
    assert "Dry-run" in result.stdout


# --- update ----------------------------------------------------------------


def test_update_check_winget_missing(monkeypatch):
    monkeypatch.setattr("sifty.windows.winget.available", lambda: False)
    result = runner.invoke(app, ["update", "check"])
    assert result.exit_code == 1


def test_update_check_up_to_date(monkeypatch):
    monkeypatch.setattr("sifty.windows.winget.available", lambda: True)
    monkeypatch.setattr("sifty.core.updates.list_upgrades", lambda: [])
    result = runner.invoke(app, ["update", "check"])
    assert result.exit_code == 0
    assert "up to date" in result.stdout


def test_update_check_table(monkeypatch):
    monkeypatch.setattr("sifty.windows.winget.available", lambda: True)
    monkeypatch.setattr(
        "sifty.core.updates.list_upgrades", lambda: [Upgrade("Firefox", "Mozilla.Firefox", "120", "121")]
    )
    result = runner.invoke(app, ["update", "check"])
    assert result.exit_code == 0
    assert "Firefox" in result.stdout


def test_update_check_json(monkeypatch):
    monkeypatch.setattr("sifty.windows.winget.available", lambda: True)
    monkeypatch.setattr(
        "sifty.core.updates.list_upgrades", lambda: [Upgrade("Firefox", "Mozilla.Firefox", "120", "121")]
    )
    result = runner.invoke(app, ["--json", "update", "check"])
    assert result.exit_code == 0
    assert _json(result)[0]["id"] == "Mozilla.Firefox"


# --- cleanup ---------------------------------------------------------------


def test_cleanup_large_missing_path():
    result = runner.invoke(app, ["cleanup", "large", "Z:/nope"])
    assert result.exit_code == 1


def test_cleanup_large_table(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "sifty.core.cleanup.find_large_files", lambda p, m, t, recent_days=0: [(tmp_path / "big.iso", 9000)]
    )
    result = runner.invoke(app, ["cleanup", "large", str(tmp_path)])
    assert result.exit_code == 0
    # Rich truncates long paths at the default 80-col width, so assert on the
    # human-size cell which always renders.
    assert "KB" in result.stdout


def test_cleanup_stale_none(monkeypatch):
    monkeypatch.setattr("sifty.core.cleanup.find_stale_downloads", lambda days: [])
    result = runner.invoke(app, ["cleanup", "stale"])
    assert result.exit_code == 0
    assert "No items" in result.stdout


def test_cleanup_stale_json(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "sifty.core.cleanup.find_stale_downloads", lambda days: [(tmp_path / "old.zip", 1000, 0.0)]
    )
    result = runner.invoke(app, ["--json", "cleanup", "stale"])
    assert result.exit_code == 0
    assert _json(result)[0]["size_bytes"] == 1000


def test_cleanup_worktrees_none(monkeypatch, tmp_path):
    monkeypatch.setattr("sifty.core.vcs.find_orphan_worktrees", lambda p: [])
    result = runner.invoke(app, ["cleanup", "worktrees", str(tmp_path)])
    assert result.exit_code == 0
    assert "No orphaned worktrees" in result.stdout


# --- startup / services / schedule / optimize / purge ----------------------


def test_startup_list_json(monkeypatch):
    entries = [StartupEntry("Spotify", "spotify.exe", "HKCU Run", enabled=True, kind="hkcu-run")]
    monkeypatch.setattr("sifty.core.startup.list_entries", lambda: entries)
    result = runner.invoke(app, ["--json", "startup", "list"])
    assert result.exit_code == 0
    assert _json(result)[0]["enabled"] is True


def test_services_list_table(monkeypatch):
    items = [ServiceInfo("DiagTrack", "Telemetry", "desc", "auto", True)]
    monkeypatch.setattr("sifty.core.services.list_services", lambda: items)
    result = runner.invoke(app, ["services", "list"])
    assert result.exit_code == 0
    assert "Telemetry" in result.stdout


def test_schedule_list_empty(monkeypatch):
    monkeypatch.setattr("sifty.core.schedule.list_schedules", lambda: [])
    result = runner.invoke(app, ["schedule", "list"])
    assert result.exit_code == 0
    assert "No schedules" in result.stdout


def test_optimize_list_table():
    result = runner.invoke(app, ["optimize", "list"])
    assert result.exit_code == 0
    assert "Flush DNS cache" in result.stdout


def test_optimize_list_json():
    result = runner.invoke(app, ["--json", "optimize", "list"])
    assert result.exit_code == 0
    assert any(op["key"] == "dns-flush" for op in _json(result))


def test_purge_scan_missing_path():
    result = runner.invoke(app, ["purge", "scan", "Z:/nope"])
    assert result.exit_code == 1


def test_purge_scan_none(monkeypatch, tmp_path):
    monkeypatch.setattr("sifty.core.purge.scan_artifacts", lambda p: [])
    result = runner.invoke(app, ["purge", "scan", str(tmp_path)])
    assert result.exit_code == 0
    assert "No artifact" in result.stdout


# --- organize / profile / watch / config / ai -----------------------------


def test_organize_preview_not_a_dir(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    result = runner.invoke(app, ["organize", "preview", str(f)])
    assert result.exit_code == 1


def test_organize_preview_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr("sifty.core.organize.plan_organization", lambda p, s: [])
    result = runner.invoke(app, ["organize", "preview", str(tmp_path)])
    assert result.exit_code == 0
    assert "Nothing to organize" in result.stdout


def test_profile_list_empty(monkeypatch):
    monkeypatch.setattr("sifty.core.profiles.list_profiles", lambda: [])
    result = runner.invoke(app, ["profile", "list"])
    assert result.exit_code == 0
    assert "No profiles yet" in result.stdout


def test_profile_list_json(monkeypatch):
    monkeypatch.setattr("sifty.core.profiles.list_profiles", lambda: [Profile("deep", ["user-temp"])])
    result = runner.invoke(app, ["--json", "profile", "list"])
    assert result.exit_code == 0
    assert _json(result)[0]["name"] == "deep"


def test_watch_check_ok(monkeypatch):
    monkeypatch.setattr("sifty.core.watch.low_space", lambda t=None: [])
    monkeypatch.setattr("sifty.core.watch.threshold_gb", lambda t=None: 5)
    result = runner.invoke(app, ["watch", "check"])
    assert result.exit_code == 0
    assert "more than 5 GB free" in result.stdout


def test_watch_check_low_json(monkeypatch):
    monkeypatch.setattr(
        "sifty.core.watch.low_space", lambda t=None: [VolumeUsage("C:", "C:\\", "NTFS", 100, 98, 2)]
    )
    monkeypatch.setattr("sifty.core.watch.threshold_gb", lambda t=None: 5)
    result = runner.invoke(app, ["--json", "watch", "check"])
    assert result.exit_code == 0
    assert _json(result)["threshold_gb"] == 5


def test_config_show(monkeypatch):
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0


def test_config_get_known_key():
    result = runner.invoke(app, ["config", "get", "ai.model"])
    assert result.exit_code == 0


def test_config_get_unknown_key():
    result = runner.invoke(app, ["config", "get", "ai.nonexistent"])
    assert result.exit_code == 1


def test_config_path():
    result = runner.invoke(app, ["config", "path"])
    assert result.exit_code == 0


def test_ai_status_unreachable(monkeypatch):
    monkeypatch.setattr("sifty.ai.client.OllamaClient.is_available", lambda self: False)
    result = runner.invoke(app, ["ai", "status"])
    assert result.exit_code == 0
    # the "not reachable" line goes to the stderr console; the hint is on stdout
    assert "ollama pull" in result.stdout


# --- app.py top-level commands ---------------------------------------------


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "Sifty" in result.stdout


def test_logs_path():
    result = runner.invoke(app, ["logs", "--path"])
    assert result.exit_code == 0


def test_checkup_table(monkeypatch):
    findings = [
        Finding("junk", "Junk files", "1.0 GB reclaimable", "attention", "junk", "Clean junk"),
        Finding("disk", "Disk space", "headroom", "ok", "", ""),
    ]
    monkeypatch.setattr("sifty.core.checkup.run_checkup", lambda: findings)
    result = runner.invoke(app, ["checkup"])
    assert result.exit_code == 0
    assert "Junk files" in result.stdout


def test_checkup_json(monkeypatch):
    findings = [Finding("disk", "Disk space", "headroom", "ok", "", "")]
    monkeypatch.setattr("sifty.core.checkup.run_checkup", lambda: findings)
    result = runner.invoke(app, ["--json", "checkup"])
    assert result.exit_code == 0
    assert _json(result)[0]["domain"] == "disk"


def test_history_empty(monkeypatch):
    monkeypatch.setattr("sifty.core.history.recent_runs", lambda n: [])
    monkeypatch.setattr("sifty.core.history.summary", lambda: {"runs": 0, "bytes_freed": 0, "items": 0})
    result = runner.invoke(app, ["history"])
    assert result.exit_code == 0
    assert "No history yet" in result.stdout


def test_history_json(monkeypatch):
    run = Run(1, "2026-05-01T10:00:00", "junk", "temp", 500, 12, True, 0)
    monkeypatch.setattr("sifty.core.history.recent_runs", lambda n: [run])
    monkeypatch.setattr("sifty.core.history.summary", lambda: {"runs": 1, "bytes_freed": 500, "items": 12})
    result = runner.invoke(app, ["--json", "history"])
    assert result.exit_code == 0
    assert _json(result)["runs"][0]["action"] == "junk"


def test_undo_nothing(monkeypatch):
    monkeypatch.setattr("sifty.core.undo.last_undoable", lambda: None)
    result = runner.invoke(app, ["undo"])
    assert result.exit_code == 0
    assert "Nothing to undo" in result.stdout
