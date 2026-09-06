"""CliRunner coverage for destructive / confirm / elevation CLI paths.

All side-effecting core functions are mocked; --yes skips the confirm prompt,
and a couple of tests feed "n" to exercise the cancel branch.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from sifty.cli.app import app
from sifty.core.checkup import Finding
from sifty.core.models import CleanResult, JunkCategory, Move, Profile, Run
from sifty.core.vcs import OrphanWorktree

runner = CliRunner()


@pytest.fixture(autouse=True)
def _sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr("sifty.core.history.record_clean", lambda *a, **k: None)


# --- elevation callback ----------------------------------------------------


def test_admin_elevation_relaunches(monkeypatch):
    monkeypatch.setattr("sifty.cli.app.is_admin", lambda: False)
    monkeypatch.setattr("sifty.cli.app.relaunch_as_admin", lambda: True)
    result = runner.invoke(app, ["--admin", "version"])
    assert result.exit_code == 0  # relaunch → typer.Exit, version never prints


def test_admin_elevation_declined(monkeypatch):
    monkeypatch.setattr("sifty.cli.app.is_admin", lambda: False)
    monkeypatch.setattr("sifty.cli.app.relaunch_as_admin", lambda: False)
    result = runner.invoke(app, ["--admin", "version"])
    assert result.exit_code == 0
    assert "Sifty" in result.stdout  # continues without admin


# --- junk clean ------------------------------------------------------------


def test_junk_clean_apply(monkeypatch):
    monkeypatch.setattr("sifty.core.junk.clean", lambda **k: CleanResult(2048, 5, [], []))
    result = runner.invoke(app, ["junk", "clean", "--apply", "--yes"])
    assert result.exit_code == 0
    assert "Recycle Bin" in result.stdout


def test_junk_clean_cancelled(monkeypatch):
    monkeypatch.setattr("sifty.core.junk.clean", lambda **k: CleanResult(2048, 5, [], []))
    result = runner.invoke(app, ["junk", "clean", "--apply"], input="n\n")
    assert result.exit_code == 0
    assert "Sent" not in result.stdout  # the cancel branch ran, nothing trashed


def test_junk_clean_apply_with_skips(monkeypatch):
    monkeypatch.setattr("sifty.core.junk.clean", lambda **k: CleanResult(2048, 5, ["a", "b"], []))
    result = runner.invoke(app, ["junk", "clean", "--apply", "--yes"])
    assert result.exit_code == 0
    assert "Sent" in result.stdout


# --- cleanup apply paths ---------------------------------------------------


def test_cleanup_duplicates_apply(monkeypatch, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"x" * 2000)
    b.write_bytes(b"x" * 2000)
    monkeypatch.setattr("sifty.core.disk.find_duplicates", lambda p, m: {"h": [a, b]})
    monkeypatch.setattr("sifty.core.cleanup.choose_duplicate_deletions", lambda g, recent_days=0: [b])
    monkeypatch.setattr(
        "sifty.core.cleanup.trash_paths",
        lambda paths, dry_run=True, extra_protected=None: CleanResult(2000, 1, [], []),
    )
    result = runner.invoke(
        app, ["cleanup", "duplicates", str(tmp_path), "--apply", "--yes", "--min-size", "1"]
    )
    assert result.exit_code == 0
    assert "Recycle Bin" in result.stdout


def test_cleanup_stale_apply(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "sifty.core.cleanup.find_stale_downloads", lambda days: [(tmp_path / "old.zip", 1000, 0.0)]
    )
    monkeypatch.setattr(
        "sifty.core.cleanup.trash_paths",
        lambda paths, dry_run=True, extra_protected=None: CleanResult(1000, 1, [], []),
    )
    result = runner.invoke(app, ["cleanup", "stale", "--apply", "--yes"])
    assert result.exit_code == 0
    assert "Recycle Bin" in result.stdout


def test_cleanup_worktrees_apply(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "sifty.core.vcs.find_orphan_worktrees",
        lambda p: [OrphanWorktree(tmp_path / "wt", "abc", "prunable by git")],
    )
    monkeypatch.setattr("sifty.core.vcs.prune_worktrees", lambda p, dry_run=True: CleanResult(2000, 1, [], []))
    result = runner.invoke(app, ["cleanup", "worktrees", str(tmp_path), "--apply", "--yes"])
    assert result.exit_code == 0
    assert "Pruned" in result.stdout


# --- purge -----------------------------------------------------------------


def test_purge_clean_dry(monkeypatch, tmp_path):
    artifacts = [
        SimpleNamespace(path=tmp_path / "dist", pattern="dist", size_bytes=100),
        SimpleNamespace(path=tmp_path / "node_modules", pattern="node_modules", size_bytes=200),
    ]
    monkeypatch.setattr("sifty.core.purge.scan_artifacts", lambda p: artifacts)
    result = runner.invoke(app, ["purge", "clean", str(tmp_path)])
    assert result.exit_code == 0
    assert "Dry-run" in result.stdout
    assert "dist" in result.stdout
    assert "node_modules" in result.stdout
    assert str(tmp_path / "dist") not in result.stdout
    assert str(tmp_path / "node_modules") not in result.stdout


def test_purge_clean_details(monkeypatch, tmp_path):
    artifacts = [
        SimpleNamespace(path=tmp_path / "dist", pattern="dist", size_bytes=100),
        SimpleNamespace(path=tmp_path / "node_modules", pattern="node_modules", size_bytes=200),
    ]
    monkeypatch.setattr("sifty.core.purge.scan_artifacts", lambda p: artifacts)
    result = runner.invoke(app, ["purge", "clean", str(tmp_path), "--details"])
    assert result.exit_code == 0
    assert str(tmp_path / "dist") in result.stdout
    assert str(tmp_path / "node_modules") in result.stdout


def test_purge_clean_apply(monkeypatch, tmp_path):
    artifact = SimpleNamespace(path=tmp_path / "node_modules", pattern="node_modules", size_bytes=5000)
    monkeypatch.setattr("sifty.core.purge.scan_artifacts", lambda p: [artifact])
    monkeypatch.setattr(
        "sifty.core.purge.purge_artifacts", lambda paths, dry_run=True: CleanResult(5000, 1, [], [])
    )
    result = runner.invoke(app, ["purge", "clean", str(tmp_path), "--apply", "--yes"])
    assert result.exit_code == 0
    assert "Recycle Bin" in result.stdout


# --- apps uninstall / leftovers --------------------------------------------


def test_apps_uninstall_dry(monkeypatch):
    monkeypatch.setattr("sifty.windows.winget.available", lambda: True)
    result = runner.invoke(app, ["apps", "uninstall", "Foo"])
    assert result.exit_code == 0
    assert "Dry-run" in result.stdout


def test_apps_uninstall_winget_missing(monkeypatch):
    monkeypatch.setattr("sifty.windows.winget.available", lambda: False)
    result = runner.invoke(app, ["apps", "uninstall", "Foo"])
    assert result.exit_code == 1


def test_apps_uninstall_apply(monkeypatch):
    monkeypatch.setattr("sifty.windows.winget.available", lambda: True)
    monkeypatch.setattr("sifty.core.apps.uninstall_app", lambda n: (True, "removed"))
    monkeypatch.setattr("sifty.core.leftovers.find_leftovers", lambda n, pub="": [])
    result = runner.invoke(app, ["apps", "uninstall", "Foo", "--apply", "--yes"])
    assert result.exit_code == 0
    assert "removed" in result.stdout


def test_apps_uninstall_apply_fail(monkeypatch):
    monkeypatch.setattr("sifty.windows.winget.available", lambda: True)
    monkeypatch.setattr("sifty.core.apps.uninstall_app", lambda n: (False, "not found"))
    result = runner.invoke(app, ["apps", "uninstall", "Foo", "--apply", "--yes"])
    assert result.exit_code == 1


def test_apps_leftovers_apply(monkeypatch, tmp_path):
    from sifty.core.leftovers import Leftover

    items = [Leftover(tmp_path / "Ghost", 1000, "data-dir")]
    monkeypatch.setattr("sifty.core.leftovers.find_leftovers", lambda n, pub="": items)
    monkeypatch.setattr(
        "sifty.core.leftovers.clean_leftovers", lambda items, dry_run=True: CleanResult(1000, 1, [], [])
    )
    result = runner.invoke(app, ["apps", "leftovers", "Ghost", "--apply", "--yes"])
    assert result.exit_code == 0
    assert "Recycle Bin" in result.stdout


# --- startup / services ----------------------------------------------------


def test_startup_disable_success(monkeypatch):
    monkeypatch.setattr("sifty.core.startup.set_enabled", lambda n, e: True)
    result = runner.invoke(app, ["startup", "disable", "Spotify"])
    assert result.exit_code == 0
    assert "Disabled" in result.stdout


def test_startup_disable_fail(monkeypatch):
    monkeypatch.setattr("sifty.core.startup.set_enabled", lambda n, e: False)
    result = runner.invoke(app, ["startup", "disable", "Ghost"])
    assert result.exit_code == 1


def test_startup_enable_success(monkeypatch):
    monkeypatch.setattr("sifty.core.startup.set_enabled", lambda n, e: True)
    result = runner.invoke(app, ["startup", "enable", "Spotify"])
    assert result.exit_code == 0


def test_services_disable_success(monkeypatch):
    monkeypatch.setattr("sifty.core.services.can_manage", lambda n: True)
    monkeypatch.setattr("sifty.core.services.set_start_type", lambda n, m: True)
    result = runner.invoke(app, ["services", "disable", "DiagTrack"])
    assert result.exit_code == 0


def test_services_not_manageable(monkeypatch):
    monkeypatch.setattr("sifty.core.services.can_manage", lambda n: False)
    result = runner.invoke(app, ["services", "disable", "Critical"])
    assert result.exit_code == 1


def test_services_enable_needs_admin(monkeypatch):
    monkeypatch.setattr("sifty.core.services.can_manage", lambda n: True)
    monkeypatch.setattr("sifty.core.services.set_start_type", lambda n, m: False)
    result = runner.invoke(app, ["services", "enable", "DiagTrack"])
    assert result.exit_code == 1


# --- schedule --------------------------------------------------------------


def test_schedule_add_success(monkeypatch):
    monkeypatch.setattr("sifty.core.profiles.get", lambda n: Profile("deep", ["user-temp"]))
    monkeypatch.setattr("sifty.core.schedule.sifty_command", lambda p: "cmd")
    monkeypatch.setattr("sifty.core.schedule.add", lambda *a, **k: (True, "ok"))
    result = runner.invoke(app, ["schedule", "add", "wk", "--profile", "deep"])
    assert result.exit_code == 0


def test_schedule_add_no_profile(monkeypatch):
    monkeypatch.setattr("sifty.core.profiles.get", lambda n: None)
    result = runner.invoke(app, ["schedule", "add", "wk", "--profile", "ghost"])
    assert result.exit_code == 1


def test_schedule_add_fail(monkeypatch):
    monkeypatch.setattr("sifty.core.profiles.get", lambda n: Profile("deep", []))
    monkeypatch.setattr("sifty.core.schedule.sifty_command", lambda p: "cmd")
    monkeypatch.setattr("sifty.core.schedule.add", lambda *a, **k: (False, "err"))
    result = runner.invoke(app, ["schedule", "add", "wk", "--profile", "deep"])
    assert result.exit_code == 1


def test_schedule_remove(monkeypatch):
    monkeypatch.setattr("sifty.core.schedule.remove", lambda n: True)
    result = runner.invoke(app, ["schedule", "remove", "wk"])
    assert result.exit_code == 0


def test_schedule_remove_missing(monkeypatch):
    monkeypatch.setattr("sifty.core.schedule.remove", lambda n: False)
    result = runner.invoke(app, ["schedule", "remove", "ghost"])
    assert result.exit_code == 1


# --- optimize run ----------------------------------------------------------


def test_optimize_run_dry(monkeypatch):
    monkeypatch.setattr("sifty.cli.commands.optimize.is_admin", lambda: True)
    result = runner.invoke(app, ["optimize", "run"])
    assert result.exit_code == 0
    assert "would run" in result.stdout


def test_optimize_run_apply(monkeypatch):
    monkeypatch.setattr("sifty.cli.commands.optimize.is_admin", lambda: True)
    monkeypatch.setattr("sifty.core.optimize.run_op", lambda op, dry_run=True: (True, "done"))
    result = runner.invoke(app, ["optimize", "run", "--apply", "--yes"])
    assert result.exit_code == 0
    assert "complete" in result.stdout


def test_optimize_run_unknown_op():
    result = runner.invoke(app, ["optimize", "run", "--op", "bogus"])
    assert result.exit_code == 1


def test_optimize_run_no_runnable(monkeypatch):
    monkeypatch.setattr("sifty.cli.commands.optimize.is_admin", lambda: False)
    result = runner.invoke(app, ["optimize", "run", "--op", "prefetch"])
    assert result.exit_code == 1


# --- organize --------------------------------------------------------------


def test_organize_apply(monkeypatch, tmp_path):
    moves = [Move(tmp_path / "a.txt", tmp_path / "docs" / "a.txt")]
    monkeypatch.setattr("sifty.core.organize.plan_organization", lambda p, s: moves)
    monkeypatch.setattr("sifty.core.organize.apply_moves", lambda m: len(m))
    result = runner.invoke(app, ["organize", "apply", str(tmp_path), "--yes"])
    assert result.exit_code == 0
    assert "Organized" in result.stdout


def test_organize_undo_nothing(monkeypatch):
    monkeypatch.setattr("sifty.core.organize.last_session", lambda: [])
    result = runner.invoke(app, ["organize", "undo"])
    assert result.exit_code == 0  # warns "nothing to undo" on stderr, then returns


def test_organize_undo(monkeypatch):
    monkeypatch.setattr("sifty.core.organize.last_session", lambda: [("a", "b")])
    monkeypatch.setattr("sifty.core.organize.undo_last", lambda: (1, 0))
    result = runner.invoke(app, ["organize", "undo", "--yes"])
    assert result.exit_code == 0
    assert "Restored" in result.stdout


# --- profile ---------------------------------------------------------------


def test_profile_add(monkeypatch):
    monkeypatch.setattr(
        "sifty.core.junk.junk_categories", lambda: [JunkCategory("user-temp", "User temp", "")]
    )
    monkeypatch.setattr("sifty.core.profiles.save", lambda p: None)
    result = runner.invoke(app, ["profile", "add", "deep", "-c", "user-temp"])
    assert result.exit_code == 0


def test_profile_add_unknown_category(monkeypatch):
    monkeypatch.setattr(
        "sifty.core.junk.junk_categories", lambda: [JunkCategory("user-temp", "User temp", "")]
    )
    result = runner.invoke(app, ["profile", "add", "deep", "-c", "bogus"])
    assert result.exit_code == 1


def test_profile_remove(monkeypatch):
    monkeypatch.setattr("sifty.core.profiles.remove", lambda n: True)
    result = runner.invoke(app, ["profile", "remove", "deep"])
    assert result.exit_code == 0


def test_profile_remove_missing(monkeypatch):
    monkeypatch.setattr("sifty.core.profiles.remove", lambda n: False)
    result = runner.invoke(app, ["profile", "remove", "ghost"])
    assert result.exit_code == 1


# --- watch schedule --------------------------------------------------------


def test_watch_schedule(monkeypatch):
    monkeypatch.setattr("sifty.windows.scheduler.create", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr("sifty.core.schedule.watch_command", lambda t: "cmd")
    result = runner.invoke(app, ["watch", "schedule"])
    assert result.exit_code == 0


def test_watch_schedule_fail(monkeypatch):
    monkeypatch.setattr("sifty.windows.scheduler.create", lambda *a, **k: (False, "err"))
    monkeypatch.setattr("sifty.core.schedule.watch_command", lambda t: "cmd")
    result = runner.invoke(app, ["watch", "schedule"])
    assert result.exit_code == 1


def test_watch_unschedule(monkeypatch):
    monkeypatch.setattr("sifty.windows.scheduler.delete", lambda n: True)
    result = runner.invoke(app, ["watch", "unschedule"])
    assert result.exit_code == 0


def test_watch_unschedule_missing(monkeypatch):
    monkeypatch.setattr("sifty.windows.scheduler.delete", lambda n: False)
    result = runner.invoke(app, ["watch", "unschedule"])
    assert result.exit_code == 1


# --- update apply ----------------------------------------------------------


def test_update_apply_success(monkeypatch):
    monkeypatch.setattr("sifty.windows.winget.available", lambda: True)
    monkeypatch.setattr("sifty.core.updates.apply_upgrades", lambda i: 0)
    result = runner.invoke(app, ["update", "apply", "--yes"])
    assert result.exit_code == 0
    assert "applied" in result.stdout


def test_update_apply_fail(monkeypatch):
    monkeypatch.setattr("sifty.windows.winget.available", lambda: True)
    monkeypatch.setattr("sifty.core.updates.apply_upgrades", lambda i: 5)
    result = runner.invoke(app, ["update", "apply", "--yes"])
    assert result.exit_code == 5


def test_update_apply_winget_missing(monkeypatch):
    monkeypatch.setattr("sifty.windows.winget.available", lambda: False)
    result = runner.invoke(app, ["update", "apply"])
    assert result.exit_code == 1


# --- config set / reset / edit ---------------------------------------------


def test_config_set_and_reset():
    assert runner.invoke(app, ["config", "set", "ai.model", "llama3.2:3b"]).exit_code == 0
    assert runner.invoke(app, ["config", "reset", "ai.model"]).exit_code == 0


def test_config_set_unknown_key():
    assert runner.invoke(app, ["config", "set", "ai.bogus", "x"]).exit_code == 1


def test_config_set_type_mismatch():
    result = runner.invoke(app, ["config", "set", "junk.include_downloads_installers", "notabool"])
    assert result.exit_code == 1


def test_config_reset_not_overridden():
    result = runner.invoke(app, ["config", "reset", "ai.model"])
    assert result.exit_code == 0
    assert "already at its default" in result.stdout


def test_config_edit(monkeypatch):
    monkeypatch.setattr(os, "startfile", lambda p: None, raising=False)
    result = runner.invoke(app, ["config", "edit"])
    assert result.exit_code == 0


# --- ai ask ----------------------------------------------------------------


def test_ai_ask_unreachable(monkeypatch):
    monkeypatch.setattr("sifty.ai.client.OllamaClient.is_available", lambda self: False)
    result = runner.invoke(app, ["ai", "ask", "what can I delete?"])
    assert result.exit_code == 1


def test_ai_ask_answers(monkeypatch):
    monkeypatch.setattr("sifty.ai.client.OllamaClient.is_available", lambda self: True)
    monkeypatch.setattr("sifty.ai.client.OllamaClient.chat", lambda self, s, u: "Delete temp files.")
    result = runner.invoke(app, ["ai", "ask", "help"])
    assert result.exit_code == 0
    assert "Delete temp" in result.stdout


def test_ai_ask_with_path(monkeypatch, tmp_path):
    monkeypatch.setattr("sifty.ai.client.OllamaClient.is_available", lambda self: True)
    monkeypatch.setattr("sifty.core.disk.biggest", lambda p, n: [])
    monkeypatch.setattr(
        "sifty.cli.commands.ai_group.summarize_disk", lambda c, items, q: "Folder answer."
    )
    result = runner.invoke(app, ["ai", "ask", "help", "--path", str(tmp_path)])
    assert result.exit_code == 0
    assert "Folder answer" in result.stdout


def test_ai_ask_path_missing(monkeypatch):
    monkeypatch.setattr("sifty.ai.client.OllamaClient.is_available", lambda self: True)
    result = runner.invoke(app, ["ai", "ask", "help", "--path", "Z:/nope"])
    assert result.exit_code == 1


# --- app.py: clean / history / undo / logs / checkup / tui -----------------


def test_clean_no_profile(monkeypatch):
    monkeypatch.setattr("sifty.core.profiles.get", lambda n: None)
    result = runner.invoke(app, ["clean", "--profile", "ghost"])
    assert result.exit_code == 1


def test_clean_dry_run(monkeypatch):
    monkeypatch.setattr("sifty.core.profiles.get", lambda n: Profile("deep", ["user-temp"]))
    monkeypatch.setattr("sifty.core.junk.clean", lambda **k: CleanResult(1000, 3, [], []))
    result = runner.invoke(app, ["clean", "--profile", "deep"])
    assert result.exit_code == 0
    assert "Dry-run" in result.stdout


def test_clean_apply(monkeypatch):
    monkeypatch.setattr("sifty.core.profiles.get", lambda n: Profile("deep", ["user-temp"]))
    monkeypatch.setattr("sifty.core.junk.clean", lambda **k: CleanResult(1000, 3, [], []))
    result = runner.invoke(app, ["clean", "--profile", "deep", "--apply", "--yes"])
    assert result.exit_code == 0
    assert "Recycle Bin" in result.stdout


def test_clean_nothing(monkeypatch):
    monkeypatch.setattr("sifty.core.profiles.get", lambda n: Profile("deep", ["user-temp"]))
    monkeypatch.setattr("sifty.core.junk.clean", lambda **k: CleanResult(0, 0, [], []))
    result = runner.invoke(app, ["clean", "--profile", "deep"])
    assert result.exit_code == 0
    assert "Nothing to clean" in result.stdout


def test_history_table(monkeypatch):
    monkeypatch.setattr(
        "sifty.core.history.recent_runs",
        lambda n: [Run(1, "2026-05-01T10:00", "junk", "temp", 500, 12, True, 3)],
    )
    monkeypatch.setattr("sifty.core.history.summary", lambda: {"runs": 1, "bytes_freed": 500, "items": 12})
    result = runner.invoke(app, ["history"])
    assert result.exit_code == 0
    assert "junk" in result.stdout


def test_undo_apply(monkeypatch):
    monkeypatch.setattr(
        "sifty.core.undo.last_undoable", lambda: Run(1, "2026-05-01", "junk", "temp", 500, 12, True, 3)
    )
    monkeypatch.setattr("sifty.core.undo.undo", lambda rid: (3, 0))
    result = runner.invoke(app, ["undo", "--yes"])
    assert result.exit_code == 0
    assert "Restored" in result.stdout


def test_checkup_all_ok(monkeypatch):
    monkeypatch.setattr(
        "sifty.core.checkup.run_checkup", lambda: [Finding("disk", "Disk", "ok", "ok", "", "")]
    )
    result = runner.invoke(app, ["checkup"])
    assert result.exit_code == 0
    assert "All clear" in result.stdout


def test_logs_tail():
    result = runner.invoke(app, ["logs", "--tail", "5"])
    assert result.exit_code == 0


def test_logs_no_file(monkeypatch, tmp_path):
    monkeypatch.setattr("sifty.cli.app.log_file", lambda: tmp_path / "nope.log")
    result = runner.invoke(app, ["logs"])
    assert result.exit_code == 0
    assert "No log file" in result.stdout


def test_tui_launch(monkeypatch):
    monkeypatch.setattr("sifty.tui.app.run", lambda: None)
    result = runner.invoke(app, ["tui"])
    assert result.exit_code == 0


# --- selfupdate ------------------------------------------------------------


def test_selfupdate_editable(monkeypatch):
    monkeypatch.setattr("sifty.core.selfupdate.editable_install_path", lambda: "C:/src")
    result = runner.invoke(app, ["selfupdate"])
    assert result.exit_code == 0
    assert "git pull" in result.stdout  # the editable-install hint (warn goes to stderr)


def test_selfupdate_up_to_date(monkeypatch):
    monkeypatch.setattr("sifty.core.selfupdate.editable_install_path", lambda: None)
    monkeypatch.setattr("sifty.core.selfupdate.check_update", lambda: ("0.6.0", None))
    result = runner.invoke(app, ["selfupdate"])
    assert result.exit_code == 0
    assert "latest" in result.stdout


def test_selfupdate_check_only(monkeypatch):
    monkeypatch.setattr("sifty.core.selfupdate.editable_install_path", lambda: None)
    monkeypatch.setattr("sifty.core.selfupdate.check_update", lambda: ("0.5.0", "0.6.0"))
    result = runner.invoke(app, ["selfupdate", "--check"])
    assert result.exit_code == 0
    assert "0.6.0" in result.stdout


def test_selfupdate_apply_success(monkeypatch):
    monkeypatch.setattr("sifty.core.selfupdate.editable_install_path", lambda: None)
    monkeypatch.setattr("sifty.core.selfupdate.check_update", lambda: ("0.5.0", "0.6.0"))
    monkeypatch.setattr("sifty.core.selfupdate.apply_update", lambda: (True, "done"))
    result = runner.invoke(app, ["selfupdate"])
    assert result.exit_code == 0
    assert "Upgraded" in result.stdout


def test_selfupdate_apply_fail(monkeypatch):
    monkeypatch.setattr("sifty.core.selfupdate.editable_install_path", lambda: None)
    monkeypatch.setattr("sifty.core.selfupdate.check_update", lambda: ("0.5.0", "0.6.0"))
    monkeypatch.setattr("sifty.core.selfupdate.apply_update", lambda: (False, "boom"))
    result = runner.invoke(app, ["selfupdate"])
    assert result.exit_code == 1


def test_selfupdate_editable_json(monkeypatch):
    monkeypatch.setattr("sifty.core.selfupdate.editable_install_path", lambda: "C:/src")
    monkeypatch.setattr("sifty.core.selfupdate.current_version", lambda: "0.6.0")
    result = runner.invoke(app, ["--json", "selfupdate"])
    assert result.exit_code == 0


# --- doctor ----------------------------------------------------------------


def test_doctor_table_healthy(monkeypatch):
    import winreg

    import psutil

    from sifty.ai.client import OllamaClient

    monkeypatch.setattr("sifty.windows.winget.available", lambda: True)
    monkeypatch.setattr(OllamaClient, "is_available", lambda self: True)
    monkeypatch.setattr(OllamaClient, "list_models", lambda self: ["qwen2.5:3b"])
    monkeypatch.setattr("sifty.cli.app.is_admin", lambda: True)
    monkeypatch.setattr(psutil, "disk_usage", lambda p: SimpleNamespace(free=50 * 1_073_741_824))
    monkeypatch.setattr(winreg, "OpenKey", lambda *a: object())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda k, n: ("x", 1))
    monkeypatch.setattr(winreg, "CloseKey", lambda k: None)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Administrator" in result.stdout


def test_doctor_table_degraded(monkeypatch):
    import winreg

    import psutil

    from sifty.ai.client import OllamaClient

    monkeypatch.setattr("sifty.windows.winget.available", lambda: False)
    monkeypatch.setattr(OllamaClient, "is_available", lambda self: False)
    monkeypatch.setattr("sifty.cli.app.is_admin", lambda: False)
    monkeypatch.setattr(psutil, "disk_usage", lambda p: (_ for _ in ()).throw(OSError("denied")))
    monkeypatch.setattr(winreg, "OpenKey", lambda *a: (_ for _ in ()).throw(FileNotFoundError()))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "not running" in result.stdout


# --- monitor ---------------------------------------------------------------


def test_monitor_renders_one_frame(monkeypatch):
    from sifty.core.monitor import ProcInfo, SystemSnapshot

    snap = SystemSnapshot(
        50.0, 8.0, 16.0, 75.0, 1024, 2048, 512, 256,
        [ProcInfo(1, "hot", 60.0, 200.0), ProcInfo(2, "warm", 30.0, 0.5), ProcInfo(3, "cool", 5.0, 50.0)],
    )
    state = {"n": 0}

    def fake_snapshot(*a, **k):
        state["n"] += 1
        if state["n"] >= 2:
            raise KeyboardInterrupt
        return snap

    monkeypatch.setattr("sifty.core.monitor.snapshot", fake_snapshot)
    result = runner.invoke(app, ["monitor"])
    assert result.exit_code == 0
