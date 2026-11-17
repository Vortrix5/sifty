"""Tests for file organization planning, applying, and undoing moves."""

from __future__ import annotations

import pytest

from sifty.core import organize


@pytest.fixture(autouse=True)
def session_sandbox(tmp_path, monkeypatch):
    """Keep the organize-undo session file out of the real %APPDATA%."""
    monkeypatch.setattr(organize, "_session_file", lambda: tmp_path / "session.json")


def test_plan_by_type_routes_extensions(tmp_path):
    (tmp_path / "photo.jpg").write_text("x")
    (tmp_path / "notes.pdf").write_text("x")
    (tmp_path / "weird.xyz").write_text("x")

    moves = organize.plan_organization(tmp_path, "type")
    dest = {m.src.name: m.dest.parent.name for m in moves}
    assert dest["photo.jpg"] == "Images"
    assert dest["notes.pdf"] == "Documents"
    assert dest["weird.xyz"] == "Other"


def test_plan_skips_directories(tmp_path):
    (tmp_path / "file.txt").write_text("x")
    (tmp_path / "subdir").mkdir()
    moves = organize.plan_organization(tmp_path, "type")
    assert [m.src.name for m in moves] == ["file.txt"]


def test_apply_moves_creates_folders_and_moves(tmp_path):
    (tmp_path / "a.png").write_text("x")
    moves = organize.plan_organization(tmp_path, "type")
    organize.apply_moves(moves)
    assert (tmp_path / "Images" / "a.png").exists()
    assert not (tmp_path / "a.png").exists()


def test_apply_moves_avoids_clobber(tmp_path):
    (tmp_path / "a.png").write_text("first")
    (tmp_path / "Images").mkdir()
    (tmp_path / "Images" / "a.png").write_text("existing")

    moves = organize.plan_organization(tmp_path, "type")
    organize.apply_moves(moves)
    assert (tmp_path / "Images" / "a.png").read_text() == "existing"
    assert (tmp_path / "Images" / "a (1).png").read_text() == "first"


def test_undo_last_restores_files_and_removes_empty_folders(tmp_path):
    (tmp_path / "a.png").write_text("photo")
    (tmp_path / "notes.pdf").write_text("doc")
    organize.apply_moves(organize.plan_organization(tmp_path, "type"))
    assert (tmp_path / "Images" / "a.png").exists()

    restored, failed = organize.undo_last()
    assert (restored, failed) == (2, 0)
    assert (tmp_path / "a.png").read_text() == "photo"
    assert (tmp_path / "notes.pdf").read_text() == "doc"
    assert not (tmp_path / "Images").exists()    # emptied folder cleaned up
    assert organize.last_session() == []          # session consumed


def test_undo_skips_files_changed_since(tmp_path):
    (tmp_path / "a.png").write_text("photo")
    organize.apply_moves(organize.plan_organization(tmp_path, "type"))
    # Something new took the original spot - undo must not clobber it.
    (tmp_path / "a.png").write_text("newer file")

    restored, failed = organize.undo_last()
    assert (restored, failed) == (0, 1)
    assert (tmp_path / "a.png").read_text() == "newer file"
    assert (tmp_path / "Images" / "a.png").read_text() == "photo"


def test_undo_with_no_session_is_safe():
    assert organize.undo_last() == (0, 0)
