"""Headless smoke tests for the TUI, via Textual's Pilot harness.

`start_workers=False` keeps the slow real-system workers from firing; we then
call each view's `_populate`/render method directly with fake data to verify the
widgets and Textual API usage without touching the OS.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Button, DataTable, Input, Select, SelectionList, Static, Tree

from sifty.core.apps import InstalledApp
from sifty.core.junk import CategoryScan, JunkCategory
from sifty.core.models import Run, ServiceInfo, StartupEntry
from sifty.core.updates import Upgrade
from sifty.tui.app import SECTIONS, SiftyApp
from sifty.tui.commands import SiftyCommands, _entries
from sifty.tui.modals import ConfirmModal
from sifty.tui.views import (
    SUBVIEW_LABELS,
    VIEWS,
    AIView,
    AppsView,
    CleanupView,
    CleanView,
    DiskView,
    HomeView,
    JunkView,
    OptimizeView,
    PurgeView,
    ReportsView,
    ServicesView,
    StartupView,
    UpdatesView,
)


def _make_app() -> SiftyApp:
    return SiftyApp(start_workers=False)


@pytest.fixture(autouse=True)
def _isolate_appdata(monkeypatch, tmp_path):
    # Keep TUI tests off the real %APPDATA% (ai_memory.db conversation history,
    # snapshots, etc.) so stored state can't leak into or pollute a test.
    monkeypatch.setenv("APPDATA", str(tmp_path))


def test_command_palette_entries_cover_sections_and_admin():
    class _Dummy:
        async def show(self, key):
            ...

        def action_elevate(self):
            ...

    entries = _entries(_Dummy())
    titles = [t for t, _h, _c in entries]
    assert "Go to Home" in titles
    # Consolidated sub-screens stay directly reachable via deep-link entries.
    assert "Go to Junk" in titles
    assert "Go to Startup" in titles
    assert "Restart as administrator" in titles
    assert len(entries) == len(SECTIONS) + len(SUBVIEW_LABELS) + 1


async def test_home_is_slim_no_stat_cards():
    async with _make_app().run_test() as pilot:
        await pilot.pause()
        # The redundant per-domain stat cards are gone - Home is checkup + volumes.
        assert not pilot.app.query(".stat-card")
        assert pilot.app.screen.query_one("#run-checkup", Button)
        assert pilot.app.screen.query_one("#vol-body", Static)


async def test_home_checkup_renders_findings_with_action_buttons():
    from sifty.core.checkup import Finding

    findings = [
        Finding("junk", "Junk files", "1.2 GB reclaimable", "attention", "junk", "Clean junk"),
        Finding("disk", "Disk space", "all volumes have headroom", "ok", "", ""),
    ]
    async with _make_app().run_test() as pilot:
        await pilot.pause()
        view = pilot.app.query_one(HomeView)
        await view._show_findings(findings)
        await pilot.pause()
        # One action button for the actionable finding, none for the ok one.
        fix_buttons = pilot.app.query(".fix")
        assert len(fix_buttons) == 1
        assert fix_buttons[0].id == "fix-junk"
        assert str(fix_buttons[0].label) == "Clean junk…"  # direct fix, not a deep link


async def test_home_checkup_direct_fix_opens_confirm():
    from sifty.core.checkup import Finding

    async with _make_app().run_test() as pilot:
        await pilot.pause()
        view = pilot.app.query_one(HomeView)
        await view._show_findings(
            [Finding("junk", "Junk files", "600 B reclaimable", "info", "junk", "Clean junk")]
        )
        await pilot.pause()
        await pilot.click("#fix-junk")
        await pilot.pause()
        await pilot.pause()
        # Direct fixes never run without the usual confirmation.
        assert isinstance(pilot.app.screen, ConfirmModal)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(pilot.app.screen, ConfirmModal)


async def test_home_checkup_review_button_navigates():
    from sifty.core.checkup import Finding

    async with _make_app().run_test() as pilot:
        await pilot.pause()
        view = pilot.app.query_one(HomeView)
        await view._show_findings(
            [Finding("startup", "Startup programs", "23 programs", "info",
                     "startup", "Review startup")]
        )
        await pilot.pause()
        await pilot.click("#fix-startup")
        await pilot.pause()
        await pilot.pause()
        assert pilot.app.query(StartupView)  # review domains deep-link, not fix


async def test_command_palette_registered():
    async with _make_app().run_test() as pilot:
        assert SiftyCommands in type(pilot.app).COMMANDS


async def test_app_boots_with_full_sidebar():
    async with _make_app().run_test() as pilot:
        sidebar = pilot.app.query_one("#sidebar")
        assert len(sidebar.children) == len(SECTIONS)


async def test_home_renders_volume_gauges():
    async with _make_app().run_test() as pilot:
        pilot.app.query_one(HomeView)
        body = pilot.app.query_one("#vol-body", Static)
        assert "free" in str(body.render())  # at least one volume rendered


# nav key -> the concrete view class that should end up mounted after show().
_NAV_EXPECT = {
    "junk": JunkView,
    "purge": PurgeView,
    "optimize": OptimizeView,
    "cleanup": CleanupView,
    "startup": StartupView,
    "services": ServicesView,
}


async def test_navigation_mounts_each_view():
    async with _make_app().run_test() as pilot:
        # Top-level sections mount their group/standalone view directly.
        for key, view_cls in VIEWS.items():
            await pilot.app.show(key)
            await pilot.pause()
            await pilot.pause()
            assert pilot.app.query_one(view_cls)
        # Deep-link keys mount their group and lazily mount the tab's sub-view.
        for key, view_cls in _NAV_EXPECT.items():
            await pilot.app.show(key)
            await pilot.pause()
            await pilot.pause()
            assert pilot.app.query_one(view_cls)


async def test_clean_group_lazily_mounts_only_active_tab():
    async with _make_app().run_test() as pilot:
        await pilot.app.show("clean")
        await pilot.pause()
        await pilot.pause()
        group = pilot.app.query_one(CleanView)
        # Only the first tab's view is instantiated; the rest stay dormant.
        assert pilot.app.query(JunkView)
        assert not pilot.app.query(PurgeView)
        assert not pilot.app.query(OptimizeView)
        assert not pilot.app.query(CleanupView)
        # Switching tabs mounts the requested sub-view on demand.
        group.activate_tab("purge")
        await pilot.pause()
        await pilot.pause()
        assert pilot.app.query(PurgeView)


async def test_junk_view_populates_selection_list():
    cats = [
        CategoryScan(JunkCategory("user-temp", "User temp", "", []), 600, 3, []),
        CategoryScan(JunkCategory("browser-cache", "Browser cache", "", []), 0, 0, []),
    ]
    async with _make_app().run_test() as pilot:
        await pilot.app.show("junk")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(JunkView)
        view._populate(cats)
        sl = pilot.app.query_one("#junk-list", SelectionList)
        assert sl.option_count == 2


async def test_apps_view_populates_table():
    apps = [
        InstalledApp("App A", "1.0", "Pub", 1024, "", "HKCU"),
        InstalledApp("App B", "2.0", "Pub", 2048, "", "HKLM"),
    ]
    async with _make_app().run_test() as pilot:
        await pilot.app.show("apps")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(AppsView)
        view._populate(apps)
        table = pilot.app.query_one("#apps-table", DataTable)
        assert table.row_count == 2
        assert view._selected_app() is not None  # cursor on a row


async def test_apps_filter_narrows_and_marking_selects():
    apps = [
        InstalledApp("Alpha", "1", "Pub", 10, "", "HKCU"),
        InstalledApp("Beta", "1", "Pub", 20, "", "HKCU"),
    ]
    async with _make_app().run_test() as pilot:
        await pilot.app.show("apps")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(AppsView)
        view._populate(apps)
        await pilot.pause()
        table = pilot.app.query_one("#apps-table", DataTable)
        assert table.row_count == 2

        # Fuzzy filter narrows the table.
        pilot.app.query_one("#apps-filter", Input).value = "alph"
        await pilot.pause()
        assert table.row_count == 1

        # Marking the highlighted row drives the bulk action target.
        view.action_toggle_mark()
        await pilot.pause()
        assert {a.name for a in view._apps_for_action()} == {"Alpha"}


async def test_apps_row_click_toggles_mark():
    apps = [
        InstalledApp("Alpha", "1", "Pub", 10, "", "HKCU"),
        InstalledApp("Beta", "1", "Pub", 20, "", "HKCU"),
    ]
    async with _make_app().run_test() as pilot:
        await pilot.app.show("apps")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(AppsView)
        view._populate(apps)
        await pilot.pause()
        view._toggle_mark("Alpha")
        view._toggle_mark("Beta")
        view._toggle_mark("Alpha")  # toggling off
        assert {a.name for a in view._apps_for_action()} == {"Beta"}


async def test_cleanup_view_populates_and_marks():
    rows = [(Path("C:/a.bin"), 100), (Path("C:/b.bin"), 200)]
    async with _make_app().run_test() as pilot:
        await pilot.app.show("cleanup")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(CleanupView)
        view._mode = "large"
        view._populate(rows, premark=False)
        await pilot.pause()
        table = pilot.app.query_one("#cleanup-table", DataTable)
        assert table.row_count == 2
        assert view._marked == set()
        key = str(rows[0][0])
        view._toggle_mark(key)
        assert view._marked == {key}


async def test_cleanup_duplicates_premark():
    rows = [(Path("C:/dup.bin"), 50)]
    async with _make_app().run_test() as pilot:
        await pilot.app.show("cleanup")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(CleanupView)
        view._mode = "duplicates"
        view._populate(rows, premark=True)  # redundant copies pre-marked
        await pilot.pause()
        assert view._marked == {str(rows[0][0])}


async def test_startup_view_populates_with_buttons_and_no_click_toggle():
    entries = [
        StartupEntry("Spotify", "C:/spotify.exe", "HKCU Run", enabled=True, kind="hkcu-run"),
        StartupEntry("OldThing", "C:/old.exe", "HKCU Run (disabled)", enabled=False, kind="hkcu-run"),
    ]
    async with _make_app().run_test() as pilot:
        await pilot.app.show("startup")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(StartupView)
        view._populate(entries)
        await pilot.pause()
        table = pilot.app.query_one("#startup-table", DataTable)
        assert table.row_count == 2  # disabled entries are listed too
        # Explicit Enable/Disable buttons exist (Services pattern)…
        assert pilot.app.screen.query_one("#enable", Button)
        assert pilot.app.screen.query_one("#disable", Button)
        # …and selecting a row must NOT toggle it (the old instant-toggle bug).
        assert not hasattr(view, "on_data_table_row_selected")


async def test_startup_buttons_skip_noop_state_change():
    entries = [
        StartupEntry("Spotify", "C:/spotify.exe", "HKCU Run", enabled=True, kind="hkcu-run"),
    ]
    async with _make_app().run_test() as pilot:
        await pilot.app.show("startup")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(StartupView)
        view._populate(entries)
        await pilot.pause()
        # "Enable" on an already-enabled entry is a no-op with a status message,
        # not a registry write.
        await pilot.click("#enable")
        await pilot.pause()
        status = pilot.app.screen.query_one("#startup-status", Static)
        assert "already enabled" in str(status.render())


async def test_services_view_populates():
    items = [
        ServiceInfo("DiagTrack", "Telemetry", "Diagnostics", "auto", True),
        ServiceInfo("Fax", "Fax", "Fax service", "absent", False),
    ]
    async with _make_app().run_test() as pilot:
        await pilot.app.show("services")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(ServicesView)
        view._populate(items)
        await pilot.pause()
        table = pilot.app.query_one("#services-table", DataTable)
        assert table.row_count == 2
        assert view._highlighted() is not None


async def test_reports_view_populates():
    runs = [Run(1, "2026-01-01T00:00:00+00:00", "junk", "user-temp", 600, 3, True, 3)]
    summ = {"runs": 1, "bytes_freed": 600, "items": 3}
    async with _make_app().run_test() as pilot:
        await pilot.app.show("reports")
        await pilot.pause()
        view = pilot.app.query_one(ReportsView)
        view._populate(runs, summ)
        await pilot.pause()
        table = pilot.app.query_one("#runs-table", DataTable)
        assert table.row_count == 1
        summary = pilot.app.query_one("#reports-summary", Static)
        assert "reclaimed" in str(summary.render())


async def test_disk_view_buttons_are_on_screen():

    async with _make_app().run_test(size=(120, 40)) as pilot:
        await pilot.app.show("disk")
        await pilot.pause()
        width = pilot.app.size.width
        for sel in ("#browse", "#analyze", "#dupes"):
            btn = pilot.app.screen.query_one(sel, Button)
            region = btn.region
            assert region.width > 0
            assert region.right <= width  # not pushed off the right edge


async def test_updates_view_populates_table():
    ups = [Upgrade("Firefox", "Mozilla.Firefox", "120.0", "121.0")]
    async with _make_app().run_test() as pilot:
        await pilot.app.show("updates")
        await pilot.pause()
        view = pilot.app.query_one(UpdatesView)
        view._populate(ups)
        table = pilot.app.query_one("#updates-table", DataTable)
        assert table.row_count == 1


async def test_junk_clean_opens_confirm_in_worker():
    # Regression: push_screen_wait must run in a worker. Before the fix this
    # path raised WorkerError instead of opening the confirm dialog.
    cats = [CategoryScan(JunkCategory("user-temp", "User temp", "", []), 600, 3, [])]
    async with _make_app().run_test() as pilot:
        await pilot.app.show("junk")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(JunkView)
        view._populate(cats)  # the size>0 category is selected by default
        await pilot.pause()

        view._clean()  # launches the worker that awaits push_screen_wait
        await pilot.pause()
        await pilot.pause()

        assert isinstance(pilot.app.screen, ConfirmModal)  # dialog opened, no crash
        await pilot.press("escape")  # cancel
        await pilot.pause()
        assert not isinstance(pilot.app.screen, ConfirmModal)


async def test_ai_view_has_autonomy_and_no_quick_buttons():
    async with _make_app().run_test() as pilot:
        await pilot.app.show("ai")
        await pilot.pause()
        select = pilot.app.query_one("#autonomy", Select)
        assert select.value in ("ask", "low_risk_auto", "full_auto")
        # The old quick-action button row is gone (it overlapped the chat box).
        assert len(pilot.app.query(".quick")) == 0


async def test_ai_inline_approval_run_and_skip():
    import threading

    async with _make_app().run_test() as pilot:
        await pilot.app.show("ai")
        await pilot.pause()
        view = pilot.app.query_one(AIView)

        # Approve: the worker-side holder flips to True and the row collapses.
        done, holder = threading.Event(), {"ok": False}
        view._mount_approval("Run optimize_system() - risk: low", done, holder)
        await pilot.pause()
        assert len(pilot.app.query(".approval-row")) == 1
        await pilot.click(".approve")
        await pilot.pause()
        assert done.is_set() and holder["ok"] is True
        assert len(pilot.app.query(".approval-row")) == 0

        # Skip: holder stays False.
        done2, holder2 = threading.Event(), {"ok": False}
        view._mount_approval("Run clean_junk() - risk: high", done2, holder2)
        await pilot.pause()
        await pilot.click(".deny")
        await pilot.pause()
        assert done2.is_set() and holder2["ok"] is False


async def test_ai_pending_approval_denied_on_unmount():
    import threading

    async with _make_app().run_test() as pilot:
        await pilot.app.show("ai")
        await pilot.pause()
        view = pilot.app.query_one(AIView)
        done, holder = threading.Event(), {"ok": False}
        view._mount_approval("Run clean_junk() - risk: high", done, holder)
        await pilot.pause()
        await pilot.app.show("home")  # navigate away with the approval pending
        await pilot.pause()
        assert done.is_set() and holder["ok"] is False  # worker not stuck


async def test_ai_view_replays_stored_conversation():
    async with _make_app().run_test() as pilot:
        # Seed a prior conversation on the app before opening the screen.
        pilot.app._ai_messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello there"},
        ]
        await pilot.app.show("ai")
        await pilot.pause()
        pilot.app.query_one(AIView)
        # Both turns were re-rendered into the transcript as message widgets.
        assert len(pilot.app.query("#chat-log Static")) >= 2


async def test_ai_tool_result_offers_follow_up_action():
    from sifty.ai.agent import ToolResultEvent
    from sifty.ai.tools import ToolResult

    table = ToolResult("found junk", title="Junk", columns=["Category", "Size"],
                       rows=[["user-temp", "600 B"]])
    async with _make_app().run_test() as pilot:
        await pilot.app.show("ai")
        await pilot.pause()
        view = pilot.app.query_one(AIView)
        view._show_tool_result(ToolResultEvent("scan_junk", "found junk", table=table))
        await pilot.pause()
        buttons = pilot.app.query(".ai-action")
        assert len(buttons) == 1
        assert buttons[0]._nav_key == "junk"


async def test_ai_tool_result_no_action_when_empty_or_unmapped():
    from sifty.ai.agent import ToolResultEvent
    from sifty.ai.tools import ToolResult

    async with _make_app().run_test() as pilot:
        await pilot.app.show("ai")
        await pilot.pause()
        view = pilot.app.query_one(AIView)
        # Empty result table → nothing to act on.
        view._show_tool_result(ToolResultEvent(
            "scan_junk", "no junk", table=ToolResult("no junk", rows=[])
        ))
        # A destructive tool isn't in the follow-up map.
        view._show_tool_result(ToolResultEvent(
            "clean_junk", "cleaned", table=ToolResult("cleaned", rows=[["x"]])
        ))
        await pilot.pause()
        assert len(pilot.app.query(".ai-action")) == 0


async def test_ai_follow_up_button_navigates():
    from sifty.ai.agent import ToolResultEvent
    from sifty.ai.tools import ToolResult

    table = ToolResult("found junk", rows=[["user-temp", "600 B"]])
    async with _make_app().run_test() as pilot:
        await pilot.app.show("ai")
        await pilot.pause()
        view = pilot.app.query_one(AIView)
        view._show_tool_result(ToolResultEvent("scan_junk", "found junk", table=table))
        await pilot.pause()
        await pilot.click(".ai-action")
        await pilot.pause()
        await pilot.pause()
        assert pilot.app.query(JunkView)  # deep-linked into Clean → Junk


async def test_disk_view_shows_biggest_items():
    async with _make_app().run_test() as pilot:
        await pilot.app.show("disk")
        await pilot.pause()
        view = pilot.app.query_one(DiskView)
        view._show_biggest(Path("C:/demo"), [(Path("big.bin"), 5000), (Path("small.txt"), 10)])
        tree = pilot.app.query_one("#biggest-tree", Tree)
        assert len(tree.root.children) == 2
