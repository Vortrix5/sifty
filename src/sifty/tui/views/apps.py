"""Apps screen: search, sort, multi-select, and (bulk) uninstall."""

from __future__ import annotations

import logging

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.fuzzy import Matcher
from textual.widgets import Button, DataTable, Input, Static

from ...console import human_size
from ...core import apps as apps_mod
from ...core import history
from ...core.leftovers import Leftover, clean_leftovers, find_leftovers
from ...core.registry_scan import find_orphan_uninstall_entries
from ..modals import ConfirmModal
from ..widgets import Panel
from .base import BaseView

logger = logging.getLogger("sifty.tui")

_MARK = "✓"
_UNMARK = " "


class AppsView(BaseView):
    BINDINGS = [("space", "toggle_mark", "Mark")]

    def compose(self) -> ComposeResult:
        yield Static("Installed apps", classes="title")
        yield Static(
            "Type to filter · click a header to sort · click a row or press Space "
            "to mark · Uninstall acts on marked rows (or the highlighted one).",
            classes="subtle",
        )
        yield Input(placeholder="Filter by name or publisher…", id="apps-filter")
        yield DataTable(id="apps-table")
        with Horizontal(classes="actions"):
            yield Button("Refresh", id="refresh")
            yield Button("Scan orphans", id="scan-orphans")
            yield Button("Clear marks", id="clear-marks")
            yield Button("Uninstall selected", id="uninstall", variant="warning")
        yield Static("", id="apps-status", classes="status")
        yield Panel(DataTable(id="orphans-table"), title="Orphaned registry entries", id="orphans-panel")

    def on_mount(self) -> None:
        self._apps: list = []
        self._filtered: list = []
        self._marked: set[str] = set()
        self._sort_key = "size"
        self._sort_reverse = True
        table = self.query_one("#apps-table", DataTable)
        table.cursor_type = "row"
        self._cols = table.add_columns("", "Name", "Version", "Publisher", "Size")

        orphans_table = self.query_one("#orphans-table", DataTable)
        orphans_table.cursor_type = "none"
        orphans_table.add_columns("Application", "Reason", "Hive")
        self.query_one("#orphans-panel").display = False

        if self.workers_enabled():
            self.load()

    # ----------------------------------------------------------------- data
    @work(thread=True, exclusive=True)
    def load(self) -> None:
        try:
            apps = apps_mod.installed_apps()
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Apps enumeration failed")
            self.app.call_from_thread(self._status, f"Failed: {exc}")
            return
        self.app.call_from_thread(self._populate, apps)

    def _populate(self, apps) -> None:
        self._apps = list(apps)
        self._sort_apps()
        self._apply_filter()

    def _sort_apps(self) -> None:
        if self._sort_key == "name":
            self._apps.sort(key=lambda a: a.name.lower(), reverse=self._sort_reverse)
        else:
            self._apps.sort(key=lambda a: a.size_bytes, reverse=self._sort_reverse)

    def _apply_filter(self) -> None:
        query = self.query_one("#apps-filter", Input).value.strip()
        if not query:
            self._filtered = list(self._apps)
        else:
            matcher = Matcher(query)
            scored = [(matcher.match(f"{a.name} {a.publisher}"), a) for a in self._apps]
            self._filtered = [a for score, a in sorted(scored, key=lambda t: t[0], reverse=True) if score > 0]
        self._rebuild_table()

    def _rebuild_table(self) -> None:
        table = self.query_one("#apps-table", DataTable)
        table.clear()
        for a in self._filtered:
            mark = _MARK if a.name in self._marked else _UNMARK
            table.add_row(
                mark, a.name, a.version or "-", a.publisher or "-",
                human_size(a.size_bytes) if a.size_bytes else "-",
                key=a.name,
            )
        marked = len(self._marked)
        suffix = f" · {marked} marked" if marked else ""
        self._status(f"{len(self._filtered)} of {len(self._apps)} apps{suffix}")

    def _status(self, msg: str) -> None:
        self.query_one("#apps-status", Static).update(msg)

    # -------------------------------------------------------------- selection
    def _highlighted_app(self):
        table = self.query_one("#apps-table", DataTable)
        if table.row_count == 0:
            return None
        idx = table.cursor_row
        if idx is not None and 0 <= idx < len(self._filtered):
            return self._filtered[idx]
        return None

    # Kept for tests / callers that want the current row.
    _selected_app = _highlighted_app

    def _apps_for_action(self) -> list:
        if self._marked:
            return [a for a in self._apps if a.name in self._marked]
        app_obj = self._highlighted_app()
        return [app_obj] if app_obj else []

    def _toggle_mark(self, name: str) -> None:
        table = self.query_one("#apps-table", DataTable)
        if name in self._marked:
            self._marked.discard(name)
            table.update_cell(name, self._cols[0], _UNMARK)
        else:
            self._marked.add(name)
            table.update_cell(name, self._cols[0], _MARK)
        marked = len(self._marked)
        self._status(f"{len(self._filtered)} of {len(self._apps)} apps · {marked} marked")

    def action_toggle_mark(self) -> None:
        app_obj = self._highlighted_app()
        if app_obj:
            self._toggle_mark(app_obj.name)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Clicking (or Enter on) a row toggles its mark - easy multi-select."""
        if event.row_key is not None and event.row_key.value is not None:
            self._toggle_mark(event.row_key.value)

    # ----------------------------------------------------------------- events
    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "apps-filter":
            self._apply_filter()

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        if event.column_index == 1:
            self._set_sort("name")
        elif event.column_index == 4:
            self._set_sort("size")

    def _set_sort(self, key: str) -> None:
        if self._sort_key == key:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_key = key
            self._sort_reverse = (key == "size")
        self._sort_apps()
        self._apply_filter()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh":
            self._status("Refreshing…")
            self.load()
        elif event.button.id == "scan-orphans":
            self._status("Scanning registry for orphaned entries…")
            self.query_one("#orphans-panel").display = False
            self.load_orphans()
        elif event.button.id == "clear-marks":
            self._marked.clear()
            self._rebuild_table()
        elif event.button.id == "uninstall":
            self._uninstall_flow()  # launches the worker below

    # --------------------------------------------------------------- orphans
    @work(thread=True, exclusive=True, group="apps-orphans")
    def load_orphans(self) -> None:
        try:
            entries = find_orphan_uninstall_entries()
        except Exception as exc:
            logger.exception("Orphan scan failed")
            self.app.call_from_thread(self._status, f"Orphan scan failed: {exc}")
            return
        self.app.call_from_thread(self._populate_orphans, entries)

    def _populate_orphans(self, entries) -> None:
        panel = self.query_one("#orphans-panel")
        table = self.query_one("#orphans-table", DataTable)
        table.clear()
        if not entries:
            self._status("No orphaned registry entries found.")
            panel.display = False
            return
        for e in entries:
            table.add_row(e.display_name, e.reason, e.hive)
        panel.display = True
        self._status(f"{len(entries)} orphaned entr{'y' if len(entries) == 1 else 'ies'} found (read-only report).")

    # --------------------------------------------------------------- uninstall
    @work
    async def _uninstall_flow(self) -> None:
        # Worker context required for push_screen_wait.
        targets = self._apps_for_action()
        if not targets:
            self._status("Nothing selected.")
            return
        names = [a.name for a in targets]
        listing = "\n".join(f"  • {n}" for n in names[:10])
        if len(names) > 10:
            listing += f"\n  …and {len(names) - 10} more"
        ok = await self.app.push_screen_wait(
            ConfirmModal(
                f"Uninstall {len(names)} app(s)? This runs winget and may open "
                f"each app's own uninstaller.\n\n{listing}",
                confirm_label="Uninstall",
            )
        )
        if ok:
            self.do_bulk_uninstall(names)

    @work(thread=True, exclusive=True)
    def do_bulk_uninstall(self, names: list[str]) -> None:
        results = []
        for i, name in enumerate(names, 1):
            self.app.call_from_thread(self._status, f"Uninstalling {i}/{len(names)}: {name}…")
            results.append((name, *apps_mod.uninstall_app(name)))
        self.app.call_from_thread(self._after_bulk, results)

    def _after_bulk(self, results) -> None:
        succeeded = sum(1 for _n, ok, _m in results if ok)
        failed = len(results) - succeeded
        severity = "information" if failed == 0 else "warning"
        self.app.notify(
            f"Uninstalled {succeeded}/{len(results)}"
            + (f" · {failed} failed" if failed else ""),
            severity=severity, title="Uninstall",
        )
        self._marked.clear()
        self.load()
        uninstalled = [n for n, ok, _m in results if ok]
        if uninstalled:
            self.scan_leftovers(uninstalled)

    # --------------------------------------------------------------- leftovers
    @work(thread=True, exclusive=True, group="apps-leftovers")
    def scan_leftovers(self, names: list[str]) -> None:
        """After an uninstall, look for what the uninstaller left behind."""
        items: list[Leftover] = []
        for name in names:
            try:
                items.extend(find_leftovers(name))
            except Exception:
                logger.exception("Leftover scan failed for %s", name)
        if items:
            self.app.call_from_thread(self._offer_leftovers, items)

    @work
    async def _offer_leftovers(self, items: list[Leftover]) -> None:
        total = sum(i.size_bytes for i in items)
        listing = "\n".join(f"  {i.path}" for i in items[:8])
        if len(items) > 8:
            listing += f"\n  …and {len(items) - 8} more"
        ok = await self.app.push_screen_wait(
            ConfirmModal(
                f"The uninstaller left {len(items)} item(s) "
                f"({human_size(total)}) behind:\n\n{listing}\n\n"
                "Send them to the Recycle Bin?",
                confirm_label="Clean leftovers",
            )
        )
        if ok:
            self.do_clean_leftovers(items)

    @work(thread=True, exclusive=True, group="apps-leftovers")
    def do_clean_leftovers(self, items: list[Leftover]) -> None:
        result = clean_leftovers(items, dry_run=False)
        history.record_clean(
            "leftovers", f"{len(items)} items",
            result.bytes_freed, result.items, result.trashed,
        )
        self.app.call_from_thread(
            self.app.notify,
            f"Sent {result.items} leftover item(s) "
            f"({human_size(result.bytes_freed)}) to the Recycle Bin.",
            title="Leftovers",
        )
