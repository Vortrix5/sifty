"""Tests for the safety guardrails - the backstop against destroying the system.

These run on any OS: they exercise the path logic directly and only mock the
actual Recycle Bin call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sifty.core import safety
from sifty.core.safety import ProtectedPathError, is_protected, trash


@pytest.fixture(autouse=True)
def fixed_roots(monkeypatch, tmp_path):
    """Pin the protected roots to a predictable layout under tmp_path."""
    windows = tmp_path / "Windows"
    program_files = tmp_path / "Program Files"
    program_data = tmp_path / "ProgramData"
    profile = tmp_path / "Users" / "tester"
    for d in (windows, program_files, program_data, profile):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("SystemRoot", str(windows))
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.setenv("ProgramData", str(program_data))
    monkeypatch.setenv("SystemDrive", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: profile))
    return tmp_path


def test_refuses_protected_root_itself(fixed_roots):
    assert is_protected(fixed_roots / "Windows")
    assert is_protected(fixed_roots / "Program Files")


def test_refuses_file_inside_protected_root(fixed_roots):
    assert is_protected(fixed_roots / "Windows" / "System32" / "kernel32.dll")


def test_refuses_ancestor_of_protected_root(fixed_roots):
    # Deleting the drive root would take Windows with it.
    assert is_protected(fixed_roots)


def test_refuses_user_profile_root(fixed_roots):
    assert is_protected(fixed_roots / "Users" / "tester")


def test_allows_path_outside_all_roots(fixed_roots, tmp_path):
    outside = tmp_path / "scratch" / "junk.tmp"
    assert not is_protected(outside)


def test_allowed_subtree_carve_out(fixed_roots):
    """A temp dir inside Windows is deletable only when vouched for."""
    temp_dir = fixed_roots / "Windows" / "Temp"
    target = temp_dir / "leftover.tmp"
    assert is_protected(target)  # blocked by default
    assert not is_protected(target, allow_subtrees=[temp_dir])  # carve-out


def test_extra_protected_paths(fixed_roots, tmp_path):
    vault = tmp_path / "scratch" / "vault"
    assert not is_protected(vault)
    assert is_protected(vault, extra_protected=[vault])


def test_trash_dry_run_does_not_delete(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(safety, "send_to_trash", lambda p: called.append(p))
    target = tmp_path / "scratch" / "x.tmp"
    target.parent.mkdir(parents=True)
    target.write_text("data")

    assert trash(target, dry_run=True) is True
    assert called == []  # nothing actually trashed
    assert target.exists()


def test_trash_apply_calls_send2trash(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(safety, "send_to_trash", lambda p: called.append(p))
    monkeypatch.setattr(safety, "audit", lambda msg: None)
    target = tmp_path / "scratch" / "x.tmp"
    target.parent.mkdir(parents=True)
    target.write_text("data")

    assert trash(target, dry_run=False) is True
    assert called == [target]


def test_trash_refuses_protected_even_with_apply(fixed_roots, monkeypatch):
    monkeypatch.setattr(safety, "send_to_trash", lambda p: (_ for _ in ()).throw(AssertionError("must not delete")))
    with pytest.raises(ProtectedPathError):
        trash(fixed_roots / "Windows" / "System32", dry_run=False)
