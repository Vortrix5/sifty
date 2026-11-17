"""Disk screen: biggest items under a path, and duplicate detection."""

from __future__ import annotations

import logging
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Input, Static, Tree

from ...console import human_size
from ...core import disk
from .. import state
from ..screens.path_picker import PathPicker
from ..widgets import Panel
from .base import BaseView

logger = logging.getLogger("sifty.tui")


class DiskView(BaseView):
    def compose(self) -> ComposeResult:
        yield Static("Disk analysis", classes="title")
        yield Input(value=str(Path.home()), id="disk-path", placeholder="Folder to analyze")
        with Horizontal(classes="actions"):
            yield Button("Browse…", id="browse")
            yield Button("Analyze", id="analyze", variant="primary")
            yield Button("Find duplicates", id="dupes")
        yield Panel(Tree("(no analysis yet)", id="biggest-tree"), title="Biggest items", id="biggest-panel")
        yield Static("", id="disk-status", classes="status")

    def on_mount(self) -> None:
        # No auto-analysis - only scan when the user asks (Analyze / Browse).
        self.query_one("#biggest-panel").display = False
        self._status("Choose a folder and press Analyze.")

    def _start_analyze(self) -> None:
        self.query_one("#biggest-panel").display = True
        self._status(f"Analyzing {self._path()}…")
        self.query_one("#biggest-tree", Tree).loading = True
        self.analyze()

    def _path(self) -> Path:
        raw = self.query_one("#disk-path", Input).value or str(Path.home())
        return Path(raw).expanduser()

    def _status(self, msg: str) -> None:
        self.query_one("#disk-status", Static).update(msg)

    @work(thread=True, exclusive=True, group="disk")
    def analyze(self) -> None:
        path = self._path()
        try:
            items = disk.biggest(path, 20)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Disk analyze failed for %s", path)
            self.app.call_from_thread(self._finish_error, exc)
            return
        self.app.call_from_thread(self._show_biggest, path, items)

    def _finish_error(self, exc: Exception) -> None:
        self.query_one("#biggest-tree", Tree).loading = False
        self.query_one("#biggest-panel").display = False
        self._status(f"Failed: {exc}")

    def _show_biggest(self, path: Path, items) -> None:
        tree = self.query_one("#biggest-tree", Tree)
        tree.loading = False
        if not items:
            self.query_one("#biggest-panel").display = False
            self._status(f"No files found in {path}.")
            return
        self.query_one("#biggest-panel").display = True
        tree.clear()
        tree.root.set_label(str(path))
        tree.root.expand()
        for entry, size in items:
            suffix = "\\" if entry.is_dir() else ""
            tree.root.add_leaf(f"{entry.name}{suffix}  -  {human_size(size)}")
        self._status(f"Top {len(items)} items in {path}")

    @work(thread=True, exclusive=True, group="disk")
    def find_dupes(self) -> None:
        path = self._path()
        try:
            groups = disk.find_duplicates(path, 1024)
            wasted = sum(disk._entry_size(ps[0]) * (len(ps) - 1) for ps in groups.values())
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Duplicate scan failed for %s", path)
            self.app.call_from_thread(self._status, f"Failed: {exc}")
            return
        self.app.call_from_thread(
            self._status,
            f"{len(groups)} duplicate groups · {human_size(wasted)} reclaimable by de-duping",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "analyze":
            self._start_analyze()
        elif event.button.id == "dupes":
            self._status("Hashing files… (this can take a while)")
            self.find_dupes()
        elif event.button.id == "browse":
            self._browse()  # launches the worker below

    @work
    async def _browse(self) -> None:
        # Worker context required for push_screen_wait.
        drives = [v.mountpoint for v in disk.volumes()]
        picked = await self.app.push_screen_wait(
            PathPicker(self._path(), state.recent_paths(), drives=drives)
        )
        if picked is None:
            return
        self.query_one("#disk-path", Input).value = str(picked)
        state.add_recent_path(str(picked))
        self._start_analyze()
