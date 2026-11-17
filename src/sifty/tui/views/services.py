"""Services screen: toggle a curated set of optional Windows services."""

from __future__ import annotations

import logging

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Static

from ...core import history, services
from ..modals import ConfirmModal
from .base import BaseView

logger = logging.getLogger("sifty.tui")


class ServicesView(BaseView):
    def compose(self) -> ComposeResult:
        yield Static("Optional services", classes="title")
        yield Static(
            "A vetted set of services that are usually safe to disable. Highlight "
            "one, then Disable/Enable. Changes need administrator rights (F2).",
            classes="subtle",
        )
        yield DataTable(id="services-table")
        with Horizontal(classes="actions"):
            yield Button("Refresh", id="refresh")
            yield Button("Enable", id="enable")
            yield Button("Disable", id="disable", variant="warning")
        yield Static("", id="services-status", classes="status")

    def on_mount(self) -> None:
        self._services: list = []
        table = self.query_one("#services-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Service", "State", "What it is")
        if self.workers_enabled():
            self.load()

    @work(thread=True, exclusive=True)
    def load(self) -> None:
        try:
            items = services.list_services()
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Services enumeration failed")
            self.app.call_from_thread(self._status, f"Failed: {exc}")
            return
        self.app.call_from_thread(self._populate, items)

    def _populate(self, items) -> None:
        self._services = items
        table = self.query_one("#services-table", DataTable)
        table.clear()
        for s in items:
            if not s.present:
                state = "absent"
            elif s.start_type == "disabled":
                state = "[yellow]disabled[/yellow]"
            else:
                state = f"[green]{s.start_type}[/green]"
            table.add_row(s.label, state, s.description)
        self._status(f"{len(items)} curated services")

    def _status(self, msg: str) -> None:
        self.query_one("#services-status", Static).update(msg)

    def _highlighted(self):
        table = self.query_one("#services-table", DataTable)
        if table.row_count == 0:
            return None
        idx = table.cursor_row
        if idx is not None and 0 <= idx < len(self._services):
            return self._services[idx]
        return None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh":
            self.load()
        elif event.button.id in ("enable", "disable"):
            self._toggle_flow(event.button.id)

    @work
    async def _toggle_flow(self, action: str) -> None:
        svc = self._highlighted()
        if svc is None:
            self._status("No service selected.")
            return
        if not svc.present:
            self.app.notify(
                f"'{svc.label}' isn't installed on this PC - nothing to change.",
                severity="warning", title="Services",
            )
            return
        mode = "disabled" if action == "disable" else "manual"
        ok = await self.app.push_screen_wait(
            ConfirmModal(
                f"Set '{svc.label}' to {mode}?\n{svc.description}",
                confirm_label=action.capitalize(),
            )
        )
        if ok:
            self._status(f"Applying ({mode})…")
            self.apply(svc.name, mode, action)

    @work(thread=True, exclusive=True)
    def apply(self, name: str, mode: str, action: str) -> None:
        ok = services.set_start_type(name, mode)
        if ok:
            history.record_clean(f"service-{action}", name, 0, 0, [])
        self.app.call_from_thread(self._after_apply, name, mode, ok)

    def _after_apply(self, name: str, mode: str, ok: bool) -> None:
        if ok:
            self.app.notify(f"Set '{name}' to {mode}.", title="Services")
            self.load()
        else:
            self.app.notify(
                f"Couldn't change '{name}' - needs administrator rights (F2).",
                severity="warning", title="Services",
            )
