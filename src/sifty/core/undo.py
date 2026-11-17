"""Undo the last clean - restore its trashed items from the Recycle Bin."""

from __future__ import annotations

from ..windows import recyclebin
from . import history
from .models import Run

__all__ = ["last_undoable", "undo"]


def last_undoable() -> Run | None:
    """The most recent run that still has restorable items, or None."""
    return history.last_restorable_run()


def undo(run_id: int) -> tuple[int, int]:
    """Restore a run's trashed items. Returns (restored, failed)."""
    items = history.items_to_restore(run_id)
    restored_ids: list[int] = []
    failed = 0
    for item_id, path in items:
        if recyclebin.restore(path):
            restored_ids.append(item_id)
        else:
            failed += 1
    history.mark_restored(restored_ids)
    return len(restored_ids), failed
