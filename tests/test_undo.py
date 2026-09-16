"""Tests for the undo restore path."""

from __future__ import annotations

from pathlib import Path

import pytest

from sifty.core import history, undo
from sifty.windows import recyclebin


@pytest.fixture
def temp_history(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def test_undo_restores_all_items(temp_history, monkeypatch):
    restored_paths = []

    monkeypatch.setattr(
        recyclebin,
        "restore",
        lambda path: restored_paths.append(path) or True,
    )

    run_id = history.record_clean(
        "junk", "x", 10, 2, [Path("a"), Path("b")]
    )

    restored, failed = undo.undo(run_id)

    assert (restored, failed) == (2, 0)
    assert set(restored_paths) == {"a", "b"}

def test_undo_counts_partial_failure(temp_history, monkeypatch):
    restored_paths = []

    def fake_restore(path):
        restored_paths.append(path)
        return path != "b"
    monkeypatch.setattr(recyclebin, "restore", fake_restore)

    run_id = history.record_clean(
        "junk", "x", 10, 2, [Path("a"), Path("b")]
    )

    restored, failed = undo.undo(run_id)

    assert (restored, failed) == (1, 1)
    assert set(restored_paths) == {"a", "b"}
    assert restored_paths == ["a", "b"]
def test_undo_marks_only_successful_items(temp_history, monkeypatch):
    marked_ids = []

    monkeypatch.setattr(
        recyclebin,
        "restore",
        lambda path: path == "a",
    )
    monkeypatch.setattr(
        history,
        "mark_restored",
        lambda ids: marked_ids.extend(ids),
    )

    run_id = history.record_clean(
        "junk", "x", 10, 2, [Path("a"), Path("b")]
    )

    restored, failed = undo.undo(run_id)

    assert (restored, failed) == (1, 1)
    assert len(marked_ids) == 1


def test_last_undoable_returns_none_when_empty(temp_history):
    assert undo.last_undoable() is None
