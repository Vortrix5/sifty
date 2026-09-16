"""Purge screen: find and remove dev artifact directories."""

from __future__ import annotations

import logging
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Input, Static

from ...console import human_size
from ...core import disk, history, purge
from .. import state
from ..modals import ConfirmModal
from ..screens.path_picker import PathPicker
from ..widgets import Panel
from .base import BaseView

logger = logging.getLogger("sifty.tui")

_MARK = "✓"
_UNMARK = " "


class PurgeView(BaseView):
    BINDINGS = [("space", "toggle_mark", "Mark group")]

    def compose(self) -> ComposeResult:
        yield Static("Dev artifact purge", classes="title")
        yield Static(
            "Scans a project tree for node_modules, dist, __pycache__, target, and "
            "other build artefacts. Groups are selectable by type.",
            classes="subtle",
        )
        yield Input(value=str(Path.home()), id="purge-path", placeholder="Project root to scan")
        with Horizontal(classes="actions"):
            yield Button("Browse…", id="browse")
            yield Button("Scan", id="scan", variant="primary")
        yield Panel(DataTable(id="purge-table"), title="Artifact directories", id="purge-panel")
        with Horizontal(classes="actions", id="purge-actions"):
            yield Button("Select all", id="select-all")
            yield Button("Deselect all", id="deselect-all")
            yield Button("Purge selected", id="purge", variant="warning")
        yield Static("Enter a project root and press Scan.", id="purge-status", classes="status")

    def on_mount(self) -> None:
        self._artifacts: list[purge.ArtifactScan] = []
        self._marked: set[str] = set()
        table = self.query_one("#purge-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("", "Pattern", "Directories", "Size")
        self._groups: dict[str, list[purge.ArtifactScan]] = {}
        self._group_keys: list[str] = []
        self.query_one("#purge-panel").display = False
        self.query_one("#purge-actions").display = False

    def _path(self) -> Path:
        return Path(self.query_one("#purge-path", Input).value or str(Path.home())).expanduser()

    def _status(self, msg: str) -> None:
        self.query_one("#purge-status", Static).update(msg)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "browse":
            self._browse()
        elif bid == "scan":
            self._status(f"Scanning {self._path()}…")
            self.query_one("#purge-panel").display = True
            self.query_one("#purge-actions").display = False
            self.query_one("#purge-table", DataTable).loading = True
            self.scan()
        elif bid == "select-all":
            self._marked = {str(a.path) for a in self._artifacts}
            self._rebuild_table()
        elif bid == "deselect-all":
            self._marked.clear()
            self._rebuild_table()
        elif bid == "purge":
            self._purge_flow()

    @work
    async def _browse(self) -> None:
        # Worker context required for push_screen_wait.
        drives = [v.mountpoint for v in disk.volumes()]
        picked = await self.app.push_screen_wait(
            PathPicker(self._path(), state.recent_paths(), drives=drives)
        )
        if picked is None:
            return
        self.query_one("#purge-path", Input).value = str(picked)
        state.add_recent_path(str(picked))

    @work(thread=True, exclusive=True, group="purge-scan")
    def scan(self) -> None:
        try:
            artifacts = purge.scan_artifacts(self._path())
        except Exception as exc:
            logger.exception("Purge scan failed")
            self.app.call_from_thread(self._scan_failed, exc)
            return
        self.app.call_from_thread(self._populate, artifacts)

    def _scan_failed(self, exc: Exception) -> None:
        self.query_one("#purge-table", DataTable).loading = False
        self.query_one("#purge-panel").display = False
        self._status(f"Failed: {exc}")

    def _populate(self, artifacts: list[purge.ArtifactScan]) -> None:
        self._artifacts = artifacts
        self._marked = {str(a.path) for a in artifacts}  # pre-mark all
        self._groups = {}
        for artifact in artifacts:
            self._groups.setdefault(artifact.pattern, []).append(artifact)
        self.query_one("#purge-table", DataTable).loading = False
        if not artifacts:
            self.query_one("#purge-panel").display = False
            self.query_one("#purge-actions").display = False
            self._status("No artifact directories found.")
            return
        self.query_one("#purge-panel").display = True
        self.query_one("#purge-actions").display = True
        self._rebuild_table()

    def _rebuild_table(self) -> None:
        table = self.query_one("#purge-table", DataTable)
        table.clear()
        groups = sorted(
            self._groups.items(),
            key=lambda item: sum(a.size_bytes for a in item[1]),
            reverse=True,
        )
        self._group_keys = [pattern for pattern, _artifacts in groups]
        for pattern, artifacts in groups:
            paths = {str(a.path) for a in artifacts}
            selected = len(paths & self._marked)
            mark = _MARK if selected == len(paths) else "~" if selected else _UNMARK
            total = sum(a.size_bytes for a in artifacts)
            table.add_row(mark, pattern, f"{len(artifacts):,}", human_size(total), key=pattern)
        total = sum(a.size_bytes for a in self._artifacts if str(a.path) in self._marked)
        self._status(
            f"{len(self._artifacts)} directories · {len(self._marked)} marked "
            f"({human_size(total)}) · {len(self._groups)} groups"
        )

    def _toggle_mark(self, key: str) -> None:
        group = self._groups.get(key)
        if group is not None:
            paths = {str(a.path) for a in group}
            if paths <= self._marked:
                self._marked.difference_update(paths)
            else:
                self._marked.update(paths)
            self._rebuild_table()
            return

        # Keep path toggling available for callers and older integrations that
        # address an individual artifact directly.
        if key in self._marked:
            self._marked.discard(key)
        else:
            self._marked.add(key)
        self._rebuild_table()

    def action_toggle_mark(self) -> None:
        table = self.query_one("#purge-table", DataTable)
        if table.row_count and table.cursor_row is not None and 0 <= table.cursor_row < len(self._group_keys):
            self._toggle_mark(self._group_keys[table.cursor_row])

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key and event.row_key.value:
            self._toggle_mark(event.row_key.value)

    @work
    async def _purge_flow(self) -> None:
        if not self._marked:
            self._status("Nothing marked.")
            return
        paths = [Path(k) for k in self._marked]
        total = sum(a.size_bytes for a in self._artifacts if str(a.path) in self._marked)
        ok = await self.app.push_screen_wait(
            ConfirmModal(
                f"Move {len(paths)} artifact director{'y' if len(paths) == 1 else 'ies'} "
                f"({human_size(total)}) to the Recycle Bin?",
                confirm_label="Purge",
            )
        )
        if ok:
            self._status("Purging…")
            self.do_purge(paths)

    @work(thread=True, exclusive=True, group="purge-scan")
    def do_purge(self, paths: list[Path]) -> None:
        result = purge.purge_artifacts(paths, dry_run=False)
        history.record_clean("purge", str(self._path()), result.bytes_freed, result.items, result.trashed)
        self.app.call_from_thread(self._after_purge, result.bytes_freed, result.items, len(result.skipped))

    def _after_purge(self, freed: int, items: int, skipped: int) -> None:
        self.app.notify(
            f"Purged {items} director{'y' if items == 1 else 'ies'} ({human_size(freed)})"
            + (f" · {skipped} skipped" if skipped else ""),
            title="Purge",
        )
        self.scan()
