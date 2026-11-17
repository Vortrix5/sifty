"""Pilot coverage for TUI infra (app, state, palette, picker) and the
worker/flow paths of the reports, junk, cleanup, apps and AI views.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import DataTable, Input, Select, SelectionList, Static

from sifty.ai.agent import FallbackEvent, FinalAnswerEvent, ToolCallEvent, ToolResultEvent
from sifty.core.junk import CategoryScan, JunkCategory
from sifty.core.leftovers import Leftover
from sifty.core.models import CleanResult, InstalledApp, Run
from sifty.core.registry_scan import OrphanEntry
from sifty.tui import state
from sifty.tui.app import SECTIONS, SiftyApp
from sifty.tui.commands import SiftyCommands, _entries
from sifty.tui.modals import ConfirmModal
from sifty.tui.screens.path_picker import PathPicker
from sifty.tui.views.ai import AIView
from sifty.tui.views.apps import AppsView
from sifty.tui.views.cleanup import CleanupView
from sifty.tui.views.junk import JunkView
from sifty.tui.views.reports import ReportsView


def _make_app() -> SiftyApp:
    return SiftyApp(start_workers=False)


@pytest.fixture(autouse=True)
def _sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr("sifty.core.history.record_clean", lambda *a, **k: None)


def _status(pilot, sel):
    return str(pilot.app.query_one(sel, Static).render())


# === state =================================================================


def test_state_roundtrip_dedup_and_cap(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert state.recent_paths() == []
    for i in range(15):
        state.add_recent_path(f"C:/p{i}")
    paths = state.recent_paths()
    assert len(paths) == 10  # capped
    state.add_recent_path("C:/p5")  # dedup → front
    assert state.recent_paths()[0] == "C:/p5"


def test_state_load_malformed(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    f = state._state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("{ not json", encoding="utf-8")
    assert state.recent_paths() == []


# === command palette =======================================================


def test_command_entries_count():
    class _Dummy:
        async def show(self, key):
            ...

        def action_elevate(self):
            ...

    assert len(_entries(_Dummy())) >= len(SECTIONS) + 1


async def test_command_palette_search_and_discover():
    async with _make_app().run_test() as pilot:
        provider = SiftyCommands(pilot.app.screen)
        discovered = [hit async for hit in provider.discover()]
        assert discovered
        hits = [hit async for hit in provider.search("home")]
        assert hits


# === app actions ===========================================================


async def test_app_elevate_already_admin(monkeypatch):
    monkeypatch.setattr("sifty.tui.app.is_admin", lambda: True)
    async with _make_app().run_test() as pilot:
        pilot.app.action_elevate()
        await pilot.pause()


async def test_app_elevate_declined(monkeypatch):
    monkeypatch.setattr("sifty.tui.app.is_admin", lambda: False)
    monkeypatch.setattr("sifty.tui.app.relaunch_as_admin", lambda: False)
    async with _make_app().run_test() as pilot:
        pilot.app.action_elevate()
        await pilot.pause()


async def test_app_show_unknown_key_is_noop():
    async with _make_app().run_test() as pilot:
        await pilot.app.show("does-not-exist")
        await pilot.pause()


async def test_app_sidebar_navigation():
    async with _make_app().run_test() as pilot:
        await pilot.pause()
        sidebar = pilot.app.query_one("#sidebar")
        sidebar.index = 2  # Disk
        await pilot.press("enter")  # fires on_list_view_selected
        await pilot.pause()
        await pilot.pause()


# === path picker ===========================================================


async def test_path_picker_set_root_and_ok(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    async with _make_app().run_test(size=(120, 40)) as pilot:
        picked = {}
        picker = PathPicker(tmp_path, [str(tmp_path)], drives=[str(tmp_path), str(sub)])
        pilot.app.push_screen(picker, lambda r: picked.update(r=r))
        await pilot.pause()
        picker._set_root(str(sub))  # drive-switch logic
        await pilot.pause()
        await pilot.click("#ok")
        await pilot.pause()
        assert picked["r"] == sub


async def test_path_picker_cancel(tmp_path):
    async with _make_app().run_test(size=(120, 40)) as pilot:
        picked = {"r": "unset"}
        pilot.app.push_screen(PathPicker(tmp_path), lambda r: picked.update(r=r))
        await pilot.pause()
        await pilot.click("#cancel")
        await pilot.pause()
        assert picked["r"] is None


# === reports ===============================================================


async def test_reports_load_and_undo_confirmed(monkeypatch):
    run = Run(1, "2026-01-01T00:00:00", "junk", "temp", 600, 3, True, 3)
    monkeypatch.setattr("sifty.core.history.recent_runs", lambda n: [run])
    monkeypatch.setattr("sifty.core.history.summary", lambda: {"runs": 1, "bytes_freed": 600, "items": 3})
    monkeypatch.setattr("sifty.core.undo.last_undoable", lambda: run)
    monkeypatch.setattr("sifty.core.undo.undo", lambda rid: (3, 0))
    async with _make_app().run_test() as pilot:
        await pilot.app.show("reports")
        await pilot.pause()
        view = pilot.app.query_one(ReportsView)
        view.load()
        await pilot.pause()
        await pilot.pause()
        assert pilot.app.query_one("#runs-table", DataTable).row_count == 1
        view._undo_flow()
        await pilot.pause()
        await pilot.pause()
        assert isinstance(pilot.app.screen, ConfirmModal)
        await pilot.click("#confirm")
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
        view._after_undo(3, 1)  # failed branch
        await pilot.pause()


async def test_reports_undo_nothing(monkeypatch):
    monkeypatch.setattr("sifty.core.undo.last_undoable", lambda: None)
    async with _make_app().run_test() as pilot:
        await pilot.app.show("reports")
        await pilot.pause()
        view = pilot.app.query_one(ReportsView)
        view._undo_flow()
        await pilot.pause()
        await pilot.pause()
        assert "Nothing to undo" in _status(pilot, "#reports-status")


# === junk ==================================================================


async def test_junk_load_and_apply(monkeypatch):
    cats = [CategoryScan(JunkCategory("user-temp", "User temp", "", []), 600, 3, [])]
    monkeypatch.setattr("sifty.core.junk.scan", lambda: cats)
    monkeypatch.setattr("sifty.core.junk.clean", lambda only=None, dry_run=True: CleanResult(600, 3, [], []))
    async with _make_app().run_test() as pilot:
        await pilot.app.show("junk")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(JunkView)
        view.load()
        await pilot.pause()
        await pilot.pause()
        assert pilot.app.query_one("#junk-list", SelectionList).option_count == 1
        view.apply_clean({"user-temp"})
        await pilot.pause()
        await pilot.pause()
        view._after_clean(600, 3, 2)  # skipped branch
        await pilot.pause()


async def test_junk_clean_confirmed(monkeypatch):
    applied = {}
    cats = [CategoryScan(JunkCategory("user-temp", "User temp", "", []), 600, 3, [])]
    monkeypatch.setattr("sifty.core.junk.scan", lambda: cats)
    monkeypatch.setattr(
        "sifty.core.junk.clean",
        lambda only=None, dry_run=True: applied.update(only=only) or CleanResult(600, 3, [], []),
    )
    async with _make_app().run_test() as pilot:
        await pilot.app.show("junk")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(JunkView)
        view._populate(cats)
        await pilot.pause()
        view._clean()
        await pilot.pause()
        await pilot.pause()
        assert isinstance(pilot.app.screen, ConfirmModal)
        await pilot.click("#confirm")
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
        assert applied.get("only") == {"user-temp"}


async def test_junk_clean_nothing_selected(monkeypatch):
    monkeypatch.setattr("sifty.core.junk.scan", lambda: [])
    async with _make_app().run_test() as pilot:
        await pilot.app.show("junk")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(JunkView)
        view._populate([])
        view._clean()
        await pilot.pause()
        await pilot.pause()
        assert "Nothing selected" in _status(pilot, "#junk-status")


# === cleanup ===============================================================


async def test_cleanup_scan_all_modes(monkeypatch, tmp_path):
    monkeypatch.setattr("sifty.core.disk.find_duplicates", lambda p, m: {})
    monkeypatch.setattr("sifty.core.cleanup.choose_duplicate_deletions", lambda g, recent_days=7: [])
    monkeypatch.setattr("sifty.core.cleanup.find_large_files", lambda p, recent_days=7: [(tmp_path / "big", 9000)])
    monkeypatch.setattr("sifty.core.cleanup.find_stale_downloads", lambda: [(tmp_path / "old", 100, 0.0)])
    monkeypatch.setattr("sifty.tui.views.cleanup.find_orphan_worktrees", lambda p: [])
    async with _make_app().run_test() as pilot:
        await pilot.app.show("cleanup")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(CleanupView)
        for mode in ("duplicates", "large", "stale", "worktrees"):
            view._mode = mode
            view.scan()
            await pilot.pause()
            await pilot.pause()


async def test_cleanup_clean_confirmed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "sifty.core.cleanup.find_large_files", lambda p, recent_days=7: [(tmp_path / "big", 9000)]
    )
    monkeypatch.setattr(
        "sifty.core.cleanup.trash_paths", lambda paths, dry_run=True: CleanResult(9000, 1, [], [])
    )
    async with _make_app().run_test(size=(160, 60)) as pilot:
        await pilot.app.show("cleanup")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(CleanupView)
        view._mode = "large"
        view.scan()
        await pilot.pause()
        await pilot.pause()
        await pilot.click("#select-all")
        await pilot.pause()
        await pilot.click("#deselect-all")
        await pilot.pause()
        view._marked = {str(tmp_path / "big")}
        view._clean_flow()
        await pilot.pause()
        await pilot.pause()
        assert isinstance(pilot.app.screen, ConfirmModal)
        await pilot.click("#confirm")
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()


async def test_cleanup_scan_failed(monkeypatch):
    monkeypatch.setattr(
        "sifty.core.cleanup.find_large_files",
        lambda p, recent_days=7: (_ for _ in ()).throw(OSError("boom")),
    )
    async with _make_app().run_test() as pilot:
        await pilot.app.show("cleanup")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(CleanupView)
        view._mode = "large"
        view.scan()
        await pilot.pause()
        await pilot.pause()
        assert "Failed" in _status(pilot, "#cleanup-status")


async def test_cleanup_clean_nothing_marked():
    async with _make_app().run_test() as pilot:
        await pilot.app.show("cleanup")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(CleanupView)
        view._marked.clear()
        view._clean_flow()
        await pilot.pause()
        await pilot.pause()
        assert "Nothing marked" in _status(pilot, "#cleanup-status")


# === apps ==================================================================


async def test_apps_load_sort_filter(monkeypatch):
    appslist = [
        InstalledApp("Alpha", "1", "Pub", 10, "", "HKCU"),
        InstalledApp("Beta", "2", "Pub", 2000, "", "HKCU"),
    ]
    monkeypatch.setattr("sifty.core.apps.installed_apps", lambda: appslist)
    async with _make_app().run_test() as pilot:
        await pilot.app.show("apps")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(AppsView)
        view.load()
        await pilot.pause()
        await pilot.pause()
        assert pilot.app.query_one("#apps-table", DataTable).row_count == 2
        view._set_sort("name")
        view._set_sort("name")  # toggle reverse
        view._set_sort("size")
        await pilot.pause()
        pilot.app.query_one("#apps-filter", Input).value = "alph"
        await pilot.pause()
        assert pilot.app.query_one("#apps-table", DataTable).row_count == 1


async def test_apps_orphans_flow(monkeypatch):
    monkeypatch.setattr("sifty.core.apps.installed_apps", lambda: [])
    monkeypatch.setattr(
        "sifty.tui.views.apps.find_orphan_uninstall_entries",
        lambda: [OrphanEntry("HKLM", "k", "Ghost", "x.exe", "missing executable")],
    )
    async with _make_app().run_test() as pilot:
        await pilot.app.show("apps")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(AppsView)
        view.load_orphans()
        await pilot.pause()
        await pilot.pause()
        assert pilot.app.query_one("#orphans-table", DataTable).row_count == 1
        view._populate_orphans([])  # empty branch


async def test_apps_uninstall_nothing(monkeypatch):
    monkeypatch.setattr("sifty.core.apps.installed_apps", lambda: [])
    async with _make_app().run_test() as pilot:
        await pilot.app.show("apps")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(AppsView)
        view._uninstall_flow()
        await pilot.pause()
        await pilot.pause()
        assert "Nothing selected" in _status(pilot, "#apps-status")


async def test_apps_do_bulk_uninstall(monkeypatch):
    monkeypatch.setattr("sifty.core.apps.installed_apps", lambda: [])
    monkeypatch.setattr("sifty.core.apps.uninstall_app", lambda n: (True, "removed"))
    monkeypatch.setattr("sifty.tui.views.apps.find_leftovers", lambda n: [])
    async with _make_app().run_test() as pilot:
        await pilot.app.show("apps")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(AppsView)
        view.do_bulk_uninstall(["Alpha", "Beta"])
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()


async def test_apps_after_bulk_mixed(monkeypatch):
    monkeypatch.setattr("sifty.core.apps.installed_apps", lambda: [])
    monkeypatch.setattr("sifty.tui.views.apps.find_leftovers", lambda n: [])
    async with _make_app().run_test() as pilot:
        await pilot.app.show("apps")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(AppsView)
        view._after_bulk([("Alpha", True, "ok"), ("Beta", False, "fail")])
        await pilot.pause()
        await pilot.pause()


async def test_apps_offer_and_clean_leftovers(monkeypatch):
    monkeypatch.setattr("sifty.core.apps.installed_apps", lambda: [])
    monkeypatch.setattr(
        "sifty.tui.views.apps.clean_leftovers", lambda items, dry_run=True: CleanResult(100, 1, [], [])
    )
    async with _make_app().run_test() as pilot:
        await pilot.app.show("apps")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(AppsView)
        view._offer_leftovers([Leftover(Path("C:/Alpha"), 100, "data-dir")])
        await pilot.pause()
        await pilot.pause()
        assert isinstance(pilot.app.screen, ConfirmModal)
        await pilot.click("#confirm")
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()


# === ai ====================================================================


async def test_ai_submit_with_tool_and_final(monkeypatch):
    monkeypatch.setattr("sifty.ai.client.OllamaClient.is_available", lambda self: True)
    monkeypatch.setattr("sifty.tui.views.ai.ai_context.build", lambda: "ctx")

    def fake_run(client, msgs, **k):
        yield ToolCallEvent("scan_junk", {}, "read")
        yield FinalAnswerEvent("All done.")

    monkeypatch.setattr("sifty.tui.views.ai.agent_run", fake_run)
    async with _make_app().run_test() as pilot:
        await pilot.app.show("ai")
        await pilot.pause()
        view = pilot.app.query_one(AIView)
        view._submit("clean my pc")
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
        assert pilot.app.query("#chat-log Static")


async def test_ai_submit_fallback(monkeypatch):
    monkeypatch.setattr("sifty.ai.client.OllamaClient.is_available", lambda self: True)
    monkeypatch.setattr("sifty.tui.views.ai.ai_context.build", lambda: "")
    monkeypatch.setattr(
        "sifty.tui.views.ai.agent_run", lambda c, m, **k: iter([FallbackEvent("plain answer")])
    )
    monkeypatch.setattr(
        "sifty.ai.client.OllamaClient.chat_stream",
        lambda self, s, u, messages=None: iter(["plain ", "answer"]),
    )
    async with _make_app().run_test() as pilot:
        await pilot.app.show("ai")
        await pilot.pause()
        view = pilot.app.query_one(AIView)
        view._submit("hi")
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()


async def test_ai_submit_unavailable(monkeypatch):
    monkeypatch.setattr("sifty.ai.client.OllamaClient.is_available", lambda self: False)
    async with _make_app().run_test() as pilot:
        await pilot.app.show("ai")
        await pilot.pause()
        view = pilot.app.query_one(AIView)
        view._submit("hi")
        await pilot.pause()
        await pilot.pause()


async def test_ai_autonomy_change(monkeypatch):
    persisted = {}
    monkeypatch.setattr("sifty.tui.views.ai.set_autonomy", lambda lvl: persisted.update(lvl=lvl))
    async with _make_app().run_test() as pilot:
        await pilot.app.show("ai")
        await pilot.pause()
        pilot.app.query_one("#autonomy", Select).value = "full_auto"
        await pilot.pause()
        assert persisted.get("lvl") == "full_auto"


async def test_ai_check_status(monkeypatch):
    monkeypatch.setattr("sifty.ai.client.OllamaClient.is_available", lambda self: True)
    async with _make_app().run_test() as pilot:
        await pilot.app.show("ai")
        await pilot.pause()
        view = pilot.app.query_one(AIView)
        view.check_status()
        await pilot.pause()
        await pilot.pause()
        assert "connected" in _status(pilot, "#ai-status")


async def test_ai_tool_result_skipped_and_plain():
    async with _make_app().run_test() as pilot:
        await pilot.app.show("ai")
        await pilot.pause()
        view = pilot.app.query_one(AIView)
        view._show_tool_result(ToolResultEvent("scan_junk", "done", skipped=True))
        view._show_tool_result(ToolResultEvent("scan_junk", "plain result"))
        await pilot.pause()
