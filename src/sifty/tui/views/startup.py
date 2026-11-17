"""Startup screen: highlight a program, then Enable/Disable it (reversible)."""

from __future__ import annotations

import logging

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Static

from ...core import startup
from .base import BaseView

logger = logging.getLogger("sifty.tui")


class StartupView(BaseView):
    def compose(self) -> ComposeResult:
        yield Static("Startup programs", classes="title")
        yield Static(
            "Highlight a row, then Enable/Disable (reversible - disabled entries "
            "stay in the list). HKLM entries need administrator rights (F2).",
            classes="subtle",
        )
        yield DataTable(id="startup-table")
        with Horizontal(classes="actions"):
            yield Button("Refresh", id="refresh")
            yield Button("Enable", id="enable")
            yield Button("Disable", id="disable", variant="warning")
        yield Static("", id="startup-status", classes="status")

    def on_mount(self) -> None:
        self._entries: list = []
        table = self.query_one("#startup-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Name", "State", "Origin", "Command")
        if self.workers_enabled():
            self.load()

    @work(thread=True, exclusive=True)
    def load(self) -> None:
        try:
            entries = startup.list_entries()
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Startup enumeration failed")
            self.app.call_from_thread(self._status, f"Failed: {exc}")
            return
        self.app.call_from_thread(self._populate, entries)

    def _populate(self, entries) -> None:
        # Stable name sort so a row stays put after Enable/Disable + reload.
        self._entries = sorted(entries, key=lambda e: e.name.lower())
        table = self.query_one("#startup-table", DataTable)
        table.clear()
        for i, e in enumerate(self._entries):
            state = "[green]enabled[/green]" if e.enabled else "[yellow]disabled[/yellow]"
            table.add_row(e.name, state, e.location, e.command, key=str(i))
        enabled = sum(1 for e in self._entries if e.enabled)
        disabled = len(self._entries) - enabled
        self._status(f"{len(self._entries)} programs · {enabled} enabled · {disabled} disabled")

    def _status(self, msg: str) -> None:
        self.query_one("#startup-status", Static).update(msg)

    def _highlighted(self):
        table = self.query_one("#startup-table", DataTable)
        if table.row_count == 0:
            return None
        idx = table.cursor_row
        if idx is not None and 0 <= idx < len(self._entries):
            return self._entries[idx]
        return None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh":
            self.load()
        elif event.button.id in ("enable", "disable"):
            entry = self._highlighted()
            if entry is None:
                self._status("No program selected.")
                return
            want_enabled = event.button.id == "enable"
            if entry.enabled == want_enabled:
                self._status(f"'{entry.name}' is already {'enabled' if want_enabled else 'disabled'}.")
                return
            self.toggle(entry, want_enabled)

    @work(thread=True, exclusive=True)
    def toggle(self, entry, want_enabled: bool) -> None:
        ok = startup.enable(entry) if want_enabled else startup.disable(entry)
        self.app.call_from_thread(self._after_toggle, entry.name, want_enabled, ok)

    def _after_toggle(self, name: str, want_enabled: bool, ok: bool) -> None:
        if ok:
            self.app.notify(
                f"{'Enabled' if want_enabled else 'Disabled'} '{name}'.", title="Startup"
            )
            self.load()
        else:
            self.app.notify(
                f"Couldn't change '{name}' - HKLM entries need admin (F2).",
                severity="warning", title="Startup",
            )
