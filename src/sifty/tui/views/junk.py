"""Junk screen: pick categories, preview, confirm, send to Recycle Bin."""

from __future__ import annotations

import logging

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, SelectionList, Static
from textual.widgets.selection_list import Selection

from ...console import human_size
from ...core import history, junk
from ...windows.admin import is_admin, relaunch_as_admin
from ..modals import ConfirmModal
from .base import BaseView

logger = logging.getLogger("sifty.tui")


class JunkView(BaseView):
    def compose(self) -> ComposeResult:
        yield Static("Junk cleanup", classes="title")
        yield Static(
            "Tick what to remove, then Clean. Everything goes to the Recycle Bin.",
            classes="subtle",
        )
        yield SelectionList(id="junk-list")
        with Horizontal(classes="actions"):
            yield Button("Rescan", id="rescan")
            yield Button("Clean selected", id="clean", variant="warning")
        yield Static("", id="junk-status", classes="status")

    def on_mount(self) -> None:
        self._cats: dict = {}
        if self.workers_enabled():
            self.load()

    @work(thread=True, exclusive=True)
    def load(self) -> None:
        try:
            cats = junk.scan()
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Junk scan failed")
            self.app.call_from_thread(self._set_status, f"Scan failed: {exc}")
            return
        self.app.call_from_thread(self._populate, cats)

    def _populate(self, cats) -> None:
        self._cats = {c.category.key: c for c in cats}
        sl = self.query_one("#junk-list", SelectionList)
        sl.clear_options()
        for c in cats:
            label = f"{c.category.label}  ·  {human_size(c.size)}  ({c.file_count:,} files)"
            sl.add_option(Selection(label, c.category.key, c.size > 0))
        total = sum(c.size for c in cats)
        self._set_status(f"{len(cats)} categories · {human_size(total)} reclaimable")

    def _set_status(self, msg: str) -> None:
        self.query_one("#junk-status", Static).update(msg)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "rescan":
            self._set_status("Rescanning…")
            self.load()
        elif event.button.id == "clean":
            self._clean()  # launches the worker below

    @work
    async def _clean(self) -> None:
        # Must run in a worker: push_screen_wait() requires a worker context.
        keys = set(self.query_one("#junk-list", SelectionList).selected)
        if not keys:
            self._set_status("Nothing selected.")
            return

        # If admin-only categories are selected without elevation, offer to
        # restart elevated. Declining just cleans what's reachable.
        needs_admin = any(
            self._cats[k].category.requires_admin for k in keys if k in self._cats
        )
        if needs_admin and not is_admin():
            elevate = await self.app.push_screen_wait(
                ConfirmModal(
                    "Some selected items (Windows Temp / Update cache) need "
                    "administrator rights. Restart Sifty as administrator?",
                    confirm_label="Restart as admin",
                )
            )
            if elevate:
                if relaunch_as_admin():
                    self.app.exit(message="Relaunching Sifty as administrator…")
                return

        size = sum(self._cats[k].size for k in keys if k in self._cats)
        files = sum(self._cats[k].file_count for k in keys if k in self._cats)
        plural = "y" if len(keys) == 1 else "ies"
        ok = await self.app.push_screen_wait(
            ConfirmModal(
                f"Move {files:,} files ({human_size(size)}) from {len(keys)} "
                f"categor{plural} to the Recycle Bin?",
                confirm_label="Clean",
            )
        )
        if ok:
            self._set_status("Cleaning…")
            self.apply_clean(keys)

    @work(thread=True, exclusive=True)
    def apply_clean(self, keys: set[str]) -> None:
        result = junk.clean(only=keys, dry_run=False)
        for reason in result.skipped:
            logger.warning("junk clean skipped: %s", reason)
        history.record_clean(
            "junk", ",".join(sorted(keys)),
            result.bytes_freed, result.items, result.trashed,
        )
        self.app.call_from_thread(
            self._after_clean, result.bytes_freed, result.items, len(result.skipped)
        )

    def _after_clean(self, freed: int, items: int, skipped: int) -> None:
        msg = f"Sent {items:,} items ({human_size(freed)}) to the Recycle Bin."
        if skipped:
            # Be honest about what stayed behind - otherwise a re-clean where
            # everything is locked looks like the button "does nothing".
            reason = (
                "need administrator rights (F2) or are in use"
                if not is_admin() else "are in use by running apps"
            )
            msg += f"\n{skipped:,} item(s) skipped - they {reason}."
        self.app.notify(
            msg,
            title="Junk cleaned",
            severity="warning" if skipped and not items else "information",
            timeout=8 if skipped else 5,
        )
        if skipped:
            self._set_status(f"{skipped:,} item(s) could not be removed - see `sifty logs`.")
        self.load()
