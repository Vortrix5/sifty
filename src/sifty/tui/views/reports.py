"""Reports screen: space reclaimed over time + undo the last clean."""

from __future__ import annotations

import logging

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Static

from ...console import human_size
from ...core import history, undo
from ..modals import ConfirmModal
from ..widgets import Panel
from .base import BaseView

logger = logging.getLogger("sifty.tui")


class ReportsView(BaseView):
    def compose(self) -> ComposeResult:
        yield Static("Reports", classes="title")
        yield Panel(Static("…", id="reports-summary"), title="Totals")
        yield DataTable(id="runs-table")
        with Horizontal(classes="actions"):
            yield Button("Refresh", id="refresh")
            yield Button("Undo last clean", id="undo", variant="warning")
        yield Static("", id="reports-status", classes="status")

    def on_mount(self) -> None:
        table = self.query_one("#runs-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("When (UTC)", "Action", "Detail", "Items", "Freed", "Restorable")
        if self.workers_enabled():
            self.load()

    @work(thread=True, exclusive=True)
    def load(self) -> None:
        try:
            runs = history.recent_runs(50)
            summ = history.summary()
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Reports load failed")
            self.app.call_from_thread(self._status, f"Failed: {exc}")
            return
        self.app.call_from_thread(self._populate, runs, summ)

    def _populate(self, runs, summ) -> None:
        self.query_one("#reports-summary", Static).update(
            f"[b]{summ['runs']}[/b] runs · [b]{human_size(summ['bytes_freed'])}[/b] "
            f"reclaimed · [b]{summ['items']:,}[/b] items"
        )
        table = self.query_one("#runs-table", DataTable)
        table.clear()
        for r in runs:
            table.add_row(
                r.ts, r.action, r.detail, f"{r.items:,}",
                human_size(r.bytes_freed), str(r.restorable) if r.restorable else "-",
            )
        self._status(f"{len(runs)} recent runs" if runs else "No history yet.")

    def _status(self, msg: str) -> None:
        self.query_one("#reports-status", Static).update(msg)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh":
            self.load()
        elif event.button.id == "undo":
            self._undo_flow()  # launches the worker below

    @work
    async def _undo_flow(self) -> None:
        run = undo.last_undoable()
        if run is None:
            self._status("Nothing to undo.")
            return
        ok = await self.app.push_screen_wait(
            ConfirmModal(
                f"Restore {run.restorable} item(s) from the {run.action} clean "
                f"at {run.ts}?",
                confirm_label="Restore",
            )
        )
        if ok:
            self._status("Restoring from the Recycle Bin…")
            self.do_undo(run.id)

    @work(thread=True, exclusive=True)
    def do_undo(self, run_id: int) -> None:
        restored, failed = undo.undo(run_id)
        self.app.call_from_thread(self._after_undo, restored, failed)

    def _after_undo(self, restored: int, failed: int) -> None:
        self.app.notify(
            f"Restored {restored} item(s)" + (f" · {failed} failed" if failed else ""),
            severity="information" if failed == 0 else "warning",
            title="Undo",
        )
        self.load()
