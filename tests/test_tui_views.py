"""Pilot coverage for TUI view workers, button handlers and confirm flows.

Workers run in threads but call the real core functions, which are monkeypatched
to canned values; `start_workers=False` keeps on_mount from auto-firing them.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from textual.widgets import DataTable, SelectionList, Static, Tree

from sifty.core.models import CleanResult, ServiceInfo, Upgrade
from sifty.core.monitor import ProcInfo, SystemSnapshot
from sifty.tui.app import SiftyApp
from sifty.tui.modals import ConfirmModal
from sifty.tui.views.disk import DiskView
from sifty.tui.views.home import HomeView
from sifty.tui.views.monitor import MonitorView
from sifty.tui.views.optimize import OptimizeView
from sifty.tui.views.purge import PurgeView
from sifty.tui.views.services import ServicesView
from sifty.tui.views.updates import UpdatesView


def _make_app() -> SiftyApp:
    return SiftyApp(start_workers=False)


@pytest.fixture(autouse=True)
def _sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr("sifty.core.history.record_clean", lambda *a, **k: None)


def _status(pilot, sel):
    return str(pilot.app.query_one(sel, Static).render())


# --- monitor ---------------------------------------------------------------


async def test_monitor_apply_and_poll(monkeypatch):
    snap = SystemSnapshot(
        95.0, 8.0, 16.0, 80.0, 1024, 2048, 512, 256,
        [ProcInfo(1, "hot", 60.0, 200.0), ProcInfo(2, "warm", 30.0, 0.5), ProcInfo(3, "cool", 5.0, 50.0)],
    )
    monkeypatch.setattr("sifty.tui.views.monitor.snapshot", lambda *a, **k: snap)
    async with _make_app().run_test() as pilot:
        await pilot.app.show("monitor")
        await pilot.pause()
        view = pilot.app.query_one(MonitorView)
        view._apply(snap)
        await pilot.pause()
        assert pilot.app.query_one("#mon-procs", DataTable).row_count == 3
        view._poll()  # thread worker: snapshot() → _apply
        await pilot.pause()
        await pilot.pause()
        assert pilot.app.query_one("#mon-procs", DataTable).row_count == 3


# --- disk ------------------------------------------------------------------


async def test_disk_analyze_worker(monkeypatch, tmp_path):
    monkeypatch.setattr("sifty.core.disk.biggest", lambda p, n: [(tmp_path / "big.bin", 5000)])
    async with _make_app().run_test() as pilot:
        await pilot.app.show("disk")
        await pilot.pause()
        view = pilot.app.query_one(DiskView)
        view.analyze()
        await pilot.pause()
        await pilot.pause()
        assert len(pilot.app.query_one("#biggest-tree", Tree).root.children) == 1


async def test_disk_find_dupes_worker(monkeypatch, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"x" * 2000)
    b.write_bytes(b"x" * 2000)
    monkeypatch.setattr("sifty.core.disk.find_duplicates", lambda p, m: {"h": [a, b]})
    async with _make_app().run_test() as pilot:
        await pilot.app.show("disk")
        await pilot.pause()
        view = pilot.app.query_one(DiskView)
        view.find_dupes()
        await pilot.pause()
        await pilot.pause()
        assert "reclaimable" in _status(pilot, "#disk-status")


async def test_disk_buttons_dispatch(monkeypatch):
    monkeypatch.setattr("sifty.core.disk.biggest", lambda p, n: [])
    monkeypatch.setattr("sifty.core.disk.find_duplicates", lambda p, m: {})
    async with _make_app().run_test() as pilot:
        await pilot.app.show("disk")
        await pilot.pause()
        await pilot.click("#analyze")
        await pilot.pause()
        await pilot.pause()
        await pilot.click("#dupes")
        await pilot.pause()
        await pilot.pause()
        assert "duplicate groups" in _status(pilot, "#disk-status")  # dupes ran last


# --- services --------------------------------------------------------------


async def test_services_load_and_after_apply(monkeypatch):
    items = [
        ServiceInfo("DiagTrack", "Telemetry", "desc", "auto", True),
        ServiceInfo("Fax", "Fax", "desc", "absent", False),
        ServiceInfo("X", "X", "d", "disabled", True),
    ]
    monkeypatch.setattr("sifty.core.services.list_services", lambda: items)
    async with _make_app().run_test() as pilot:
        await pilot.app.show("services")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(ServicesView)
        view.load()
        await pilot.pause()
        await pilot.pause()
        assert pilot.app.query_one("#services-table", DataTable).row_count == 3
        view._after_apply("DiagTrack", "disabled", True)
        await pilot.pause()
        view._after_apply("DiagTrack", "disabled", False)
        await pilot.pause()


async def test_services_toggle_no_selection():
    async with _make_app().run_test() as pilot:
        await pilot.app.show("services")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(ServicesView)
        view._toggle_flow("disable")
        await pilot.pause()
        await pilot.pause()
        assert "No service selected" in _status(pilot, "#services-status")


async def test_services_disable_confirmed(monkeypatch):
    applied = {}
    monkeypatch.setattr(
        "sifty.core.services.list_services",
        lambda: [ServiceInfo("DiagTrack", "Telemetry", "desc", "auto", True)],
    )
    monkeypatch.setattr(
        "sifty.core.services.set_start_type", lambda n, m: applied.update(call=(n, m)) or True
    )
    async with _make_app().run_test() as pilot:
        await pilot.app.show("services")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(ServicesView)
        view._populate([ServiceInfo("DiagTrack", "Telemetry", "desc", "auto", True)])
        await pilot.pause()
        view._toggle_flow("disable")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(pilot.app.screen, ConfirmModal)
        await pilot.click("#confirm")
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
        assert applied.get("call") == ("DiagTrack", "disabled")


# --- updates ---------------------------------------------------------------


async def test_updates_check_worker(monkeypatch):
    monkeypatch.setattr(
        "sifty.core.updates.list_upgrades", lambda: [Upgrade("FF", "Mozilla.Firefox", "120", "121")]
    )
    async with _make_app().run_test() as pilot:
        await pilot.app.show("updates")
        await pilot.pause()
        view = pilot.app.query_one(UpdatesView)
        view.check()
        await pilot.pause()
        await pilot.pause()
        assert pilot.app.query_one("#updates-table", DataTable).row_count == 1


async def test_updates_apply_worker_and_after(monkeypatch):
    ups = [Upgrade("FF", "Mozilla.Firefox", "120", "121")]
    monkeypatch.setattr("sifty.core.updates.list_upgrades", lambda: ups)
    monkeypatch.setattr("sifty.core.updates.apply_upgrades", lambda i=None: 0)
    async with _make_app().run_test() as pilot:
        await pilot.app.show("updates")
        await pilot.pause()
        view = pilot.app.query_one(UpdatesView)
        view._populate(ups)
        await pilot.pause()
        assert view._selected() is not None
        view.apply("Mozilla.Firefox")
        await pilot.pause()
        await pilot.pause()
        view._after_apply(1)  # error branch
        await pilot.pause()


async def test_updates_apply_flow_no_selection(monkeypatch):
    monkeypatch.setattr("sifty.core.updates.list_upgrades", lambda: [])
    async with _make_app().run_test() as pilot:
        await pilot.app.show("updates")
        await pilot.pause()
        view = pilot.app.query_one(UpdatesView)
        view._ups = []
        view._apply_flow(selected_only=True)
        await pilot.pause()
        await pilot.pause()
        assert "No update selected" in _status(pilot, "#updates-status")
        view._apply_flow(selected_only=False)
        await pilot.pause()
        await pilot.pause()
        assert "Nothing to upgrade" in _status(pilot, "#updates-status")


async def test_updates_apply_all_confirmed(monkeypatch):
    applied = {}
    ups = [Upgrade("FF", "Mozilla.Firefox", "120", "121")]
    monkeypatch.setattr("sifty.core.updates.list_upgrades", lambda: ups)
    monkeypatch.setattr("sifty.core.updates.apply_upgrades", lambda i=None: applied.update(ran=True) or 0)
    async with _make_app().run_test() as pilot:
        await pilot.app.show("updates")
        await pilot.pause()
        view = pilot.app.query_one(UpdatesView)
        view._populate(ups)
        await pilot.pause()
        view._apply_flow(selected_only=False)
        await pilot.pause()
        await pilot.pause()
        assert isinstance(pilot.app.screen, ConfirmModal)
        await pilot.click("#confirm")
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
        assert applied.get("ran") is True


# --- purge -----------------------------------------------------------------


def _artifact(tmp_path, name="node_modules", size=5000):
    return SimpleNamespace(path=tmp_path / name, pattern=name, size_bytes=size)


async def test_purge_scan_worker_and_marks(monkeypatch, tmp_path):
    art = _artifact(tmp_path)
    monkeypatch.setattr("sifty.core.purge.scan_artifacts", lambda p: [art])
    async with _make_app().run_test(size=(160, 60)) as pilot:
        await pilot.app.show("purge")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(PurgeView)
        view.scan()
        await pilot.pause()
        await pilot.pause()
        assert pilot.app.query_one("#purge-table", DataTable).row_count == 1
        await pilot.click("#deselect-all")
        await pilot.pause()
        assert view._marked == set()
        await pilot.click("#select-all")
        await pilot.pause()
        assert len(view._marked) == 1
        view._toggle_mark(str(art.path))  # toggle off
        assert view._marked == set()


async def test_purge_scan_empty(monkeypatch, tmp_path):
    monkeypatch.setattr("sifty.core.purge.scan_artifacts", lambda p: [])
    async with _make_app().run_test() as pilot:
        await pilot.app.show("purge")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(PurgeView)
        view.scan()
        await pilot.pause()
        await pilot.pause()
        assert "No artifact directories" in _status(pilot, "#purge-status")


async def test_purge_scan_failed(monkeypatch):
    monkeypatch.setattr(
        "sifty.core.purge.scan_artifacts", lambda p: (_ for _ in ()).throw(OSError("boom"))
    )
    async with _make_app().run_test() as pilot:
        await pilot.app.show("purge")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(PurgeView)
        view.scan()
        await pilot.pause()
        await pilot.pause()
        assert "Failed" in _status(pilot, "#purge-status")


async def test_purge_flow_nothing_then_confirmed(monkeypatch, tmp_path):
    art = _artifact(tmp_path)
    monkeypatch.setattr("sifty.core.purge.scan_artifacts", lambda p: [art])
    monkeypatch.setattr(
        "sifty.core.purge.purge_artifacts", lambda paths, dry_run=True: CleanResult(5000, 1, [], [])
    )
    async with _make_app().run_test() as pilot:
        await pilot.app.show("purge")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(PurgeView)
        view.scan()
        await pilot.pause()
        await pilot.pause()
        # nothing-marked branch
        view._marked.clear()
        view._purge_flow()
        await pilot.pause()
        assert "Nothing marked" in _status(pilot, "#purge-status")
        # marked → confirm → do_purge
        view._marked = {str(art.path)}
        view._purge_flow()
        await pilot.pause()
        await pilot.pause()
        assert isinstance(pilot.app.screen, ConfirmModal)
        await pilot.click("#confirm")
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()


# --- optimize --------------------------------------------------------------


async def test_optimize_run_selected(monkeypatch):
    monkeypatch.setattr("sifty.core.optimize.run_op", lambda op, dry_run=True: (True, "done"))
    async with _make_app().run_test() as pilot:
        await pilot.app.show("optimize")
        await pilot.pause()
        await pilot.pause()
        await pilot.click("#run")
        # Drain the run worker before the context tears the app down, otherwise
        # its thread keeps updating the table after the view unmounts.
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        assert pilot.app.query_one("#results-panel").display


async def test_optimize_run_op_error(monkeypatch):
    monkeypatch.setattr(
        "sifty.core.optimize.run_op", lambda op, dry_run=True: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    async with _make_app().run_test() as pilot:
        await pilot.app.show("optimize")
        await pilot.pause()
        await pilot.pause()
        await pilot.click("#run")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        assert pilot.app.query_one("#results-panel").display


async def test_optimize_nothing_selected():
    async with _make_app().run_test() as pilot:
        await pilot.app.show("optimize")
        await pilot.pause()
        await pilot.pause()
        view = pilot.app.query_one(OptimizeView)
        view.query_one("#optimize-list", SelectionList).deselect_all()
        await pilot.pause()
        await pilot.click("#run")
        await pilot.pause()
        assert "Nothing selected" in _status(pilot, "#optimize-status")


# --- home ------------------------------------------------------------------


async def test_home_run_checkup_button(monkeypatch):
    from sifty.core.checkup import Finding

    monkeypatch.setattr(
        "sifty.core.checkup.run_checkup", lambda: [Finding("disk", "Disk", "ok", "ok", "", "")]
    )
    monkeypatch.setattr("sifty.core.anomaly.detect", lambda **k: [])
    monkeypatch.setattr("sifty.core.anomaly.record_snapshot", lambda **k: None)
    async with _make_app().run_test() as pilot:
        await pilot.pause()
        await pilot.click("#run-checkup")
        await pilot.pause()
        await pilot.pause()
        assert pilot.app.query(".finding-row")


async def test_home_run_agent_button(monkeypatch):
    from sifty.core.agent_run import AgentRunResult, AutoFixOutcome
    from sifty.core.checkup import Finding

    captured = {}

    def fake_run_agent(**kw):
        captured.update(kw)
        return AgentRunResult(
            [Finding("junk", "Junk files", "cleaned", "ok", "junk", "")],
            [], AutoFixOutcome(["user-temp"], 5, 1000, [], 9), False, "done", [],
        )

    monkeypatch.setattr("sifty.core.agent_run.run_agent", fake_run_agent)
    async with _make_app().run_test() as pilot:
        await pilot.pause()
        await pilot.click("#run-agent")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(pilot.app.screen, ConfirmModal)
        await pilot.click("#confirm")
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
        assert captured.get("apply") is True     # the button opts into auto-fix
        assert pilot.app.query(".finding-row")   # result rendered on Home


async def test_home_fix_junk_confirmed(monkeypatch):
    from sifty.core.checkup import Finding

    monkeypatch.setattr("sifty.core.junk.clean", lambda dry_run=False: CleanResult(1000, 3, [], []))
    async with _make_app().run_test() as pilot:
        await pilot.pause()
        view = pilot.app.query_one(HomeView)
        await view._show_findings(
            [Finding("junk", "Junk files", "1 GB reclaimable", "attention", "junk", "Clean junk")]
        )
        await pilot.pause()
        await pilot.click("#fix-junk")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(pilot.app.screen, ConfirmModal)
        await pilot.click("#confirm")
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()
