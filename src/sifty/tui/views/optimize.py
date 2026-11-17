"""Optimize screen: run non-destructive system cache cleanup operations."""

from __future__ import annotations

import logging

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, SelectionList, Static
from textual.widgets.selection_list import Selection

from ...core import optimize
from ...windows.admin import is_admin
from ..widgets import Panel
from .base import BaseView

logger = logging.getLogger("sifty.tui")

# (icon, theme-hex-color) pairs for each row state
_PENDING = ("·", "#9aa5ce")   # muted dot - not started yet
_RUNNING = ("⟳", "#e0af68")   # yellow - in progress
_OK      = ("✓", "#9ece6a")   # green  - succeeded
_FAIL    = ("✗", "#f7768e")   # red    - failed


class OptimizeView(BaseView):
    def compose(self) -> ComposeResult:
        yield Static("System optimize", classes="title")
        yield Static(
            "Non-destructive cache cleanup - each operation is safe and rebuilds "
            "automatically. Admin-only items are greyed out when not elevated (F2).",
            classes="subtle",
        )
        yield SelectionList(id="optimize-list")
        with Horizontal(classes="actions"):
            yield Button("Run selected", id="run", variant="primary")
        yield Panel(DataTable(id="results-table"), title="Results", id="results-panel")
        yield Static("", id="optimize-status", classes="status")

    def on_mount(self) -> None:
        self._ops: list[optimize.OptimizeOp] = optimize.list_operations()
        self._admin = is_admin()

        sl = self.query_one("#optimize-list", SelectionList)
        for op in self._ops:
            if op.requires_admin and not self._admin:
                label = f"{op.label}  [dim](needs admin - F2)[/dim]"
                sl.add_option(Selection(label, op.key, initial_state=False))
            else:
                label = f"{op.label}  [dim]· {op.reversible}[/dim]"
                sl.add_option(Selection(label, op.key, initial_state=True))

        table = self.query_one("#results-table", DataTable)
        table.cursor_type = "none"
        self._col_status = table.add_column(" ", width=3)
        self._col_op     = table.add_column("Operation", width=36)
        self._col_detail = table.add_column("Detail", width=50)

        self.query_one("#results-panel").display = False
        self._set_status("Select operations and press Run selected.")

    def _set_status(self, msg: str) -> None:
        self.query_one("#optimize-status", Static).update(msg)

    @staticmethod
    def _cell(icon: str, color: str) -> Text:
        return Text(icon, style=f"bold {color}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "run":
            return
        keys = set(self.query_one("#optimize-list", SelectionList).selected)
        if not keys:
            self._set_status("Nothing selected.")
            return
        ops = [op for op in self._ops if op.key in keys]

        table = self.query_one("#results-table", DataTable)
        table.clear()
        for op in ops:
            icon, color = _PENDING
            table.add_row(self._cell(icon, color), op.label, "Pending…", key=op.key)

        self.query_one("#results-panel").display = True
        self._set_status(f"Running {len(ops)} operation(s)…")
        self.query_one("#run", Button).disabled = True
        self.run_ops(ops)

    @work(thread=True, exclusive=True, group="optimize-run")
    def run_ops(self, ops: list[optimize.OptimizeOp]) -> None:
        for op in ops:
            self.app.call_from_thread(self._update_row, op.key, _RUNNING, "Running…")
            try:
                ok, msg = optimize.run_op(op, dry_run=False)
                self.app.call_from_thread(self._update_row, op.key, _OK if ok else _FAIL, msg)
            except Exception as exc:
                logger.exception("Optimize op %s failed", op.key)
                self.app.call_from_thread(self._update_row, op.key, _FAIL, str(exc))
        self.app.call_from_thread(self._done)

    def _update_row(self, row_key: str, status: tuple[str, str], detail: str) -> None:
        # The worker runs in a thread; if the user switched views mid-run the
        # view is already unmounted, so there's nothing (and no table) to update.
        if not self.is_mounted:
            return
        icon, color = status
        table = self.query_one("#results-table", DataTable)
        table.update_cell(row_key, self._col_status, self._cell(icon, color))
        table.update_cell(row_key, self._col_detail, detail)

    def _done(self) -> None:
        if not self.is_mounted:
            return
        self.query_one("#run", Button).disabled = False
        self._set_status("")
