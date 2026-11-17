"""Tests for core.optimize: operation dispatch and helpers.

Subprocess, junk.clean, hyperv and safety.trash are all mocked, so nothing
touches the system. The audit log is silenced so tests don't write to %APPDATA%.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from sifty.core import optimize
from sifty.core.optimize import OptimizeOp


def _op(key="dns-flush", **kw):
    return OptimizeOp(key, kw.get("label", key), "desc", "instant", kw.get("requires_admin", False))


@pytest.fixture(autouse=True)
def _silence_audit(monkeypatch):
    monkeypatch.setattr(optimize, "audit", lambda msg: None)


# --- list_operations -------------------------------------------------------


def test_list_operations_keys_and_admin_flags():
    ops = optimize.list_operations()
    assert [o.key for o in ops] == [
        "dns-flush",
        "thumbnail-cache",
        "prefetch",
        "update-cache",
        "dism-cleanup",
        "compact-vhd",
    ]
    assert any(o.requires_admin for o in ops)


# --- run_op dispatch -------------------------------------------------------


def test_run_op_dry_run_does_nothing():
    ok, msg = optimize.run_op(_op("dns-flush", label="Flush DNS cache"), dry_run=True)
    assert ok is True
    assert msg.startswith("[dry-run]")
    assert "Flush DNS cache" in msg


def test_run_op_dns_flush(monkeypatch):
    monkeypatch.setattr(
        optimize.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="Successfully flushed the DNS cache.", stderr=""),
    )
    ok, msg = optimize.run_op(_op("dns-flush"), dry_run=False)
    assert ok is True
    assert "Successfully flushed" in msg


def test_run_op_thumbnail_cache(monkeypatch):
    monkeypatch.setattr(
        "sifty.core.junk.clean",
        lambda only=None, dry_run=True: SimpleNamespace(items=5, bytes_freed=2048, skipped=[]),
    )
    ok, msg = optimize.run_op(_op("thumbnail-cache"), dry_run=False)
    assert ok is True
    assert msg == "5 items freed"


def test_run_op_thumbnail_cache_nothing_freed(monkeypatch):
    monkeypatch.setattr(
        "sifty.core.junk.clean",
        lambda only=None, dry_run=True: SimpleNamespace(items=0, bytes_freed=0, skipped=[]),
    )
    ok, msg = optimize.run_op(_op("thumbnail-cache"), dry_run=False)
    assert ok is True
    assert msg == "0 items freed"


def test_run_op_update_cache_with_skips(monkeypatch):
    monkeypatch.setattr(
        "sifty.core.junk.clean",
        lambda only=None, dry_run=True: SimpleNamespace(items=2, bytes_freed=100, skipped=["a", "b"]),
    )
    ok, msg = optimize.run_op(_op("update-cache"), dry_run=False)
    assert ok is True
    assert msg == "2 items freed (2 skipped)"


def test_run_op_dism(monkeypatch):
    captured = {}

    def run(cmd, **k):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="The operation completed successfully.", stderr="")

    monkeypatch.setattr(optimize.subprocess, "run", run)
    ok, msg = optimize.run_op(_op("dism-cleanup"), dry_run=False)
    assert ok is True
    assert captured["cmd"][0] == "DISM"


def test_run_op_prefetch(monkeypatch, tmp_path):
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    prefetch = tmp_path / "Prefetch"
    prefetch.mkdir()
    (prefetch / "boot.pf").write_bytes(b"x" * 128)
    trashed = []
    monkeypatch.setattr(
        "sifty.core.safety.trash",
        lambda p, allow_subtrees=None, dry_run=True: trashed.append(p),
    )
    ok, msg = optimize.run_op(_op("prefetch"), dry_run=False)
    assert ok is True
    assert msg == "1 items freed"
    assert len(trashed) == 1


def test_run_op_compact_vhd_none_found(monkeypatch):
    monkeypatch.setattr("sifty.windows.hyperv.list_vhdx_files", lambda: [])
    ok, msg = optimize.run_op(_op("compact-vhd"), dry_run=False)
    assert ok is True
    assert "no .vhdx" in msg


def test_run_op_compact_vhd_partial_success(monkeypatch):
    monkeypatch.setattr(
        "sifty.windows.hyperv.list_vhdx_files", lambda: [("a.vhdx", 100), ("b.vhdx", 200)]
    )
    monkeypatch.setattr(
        "sifty.windows.hyperv.compact_vhdx", lambda path: (path == "a.vhdx", "msg")
    )
    ok, msg = optimize.run_op(_op("compact-vhd"), dry_run=False)
    assert ok is True
    assert msg == "1/2 VHD(s) compacted"


def test_run_op_unknown_key():
    ok, msg = optimize.run_op(_op("bogus"), dry_run=False)
    assert ok is False
    assert "Unknown operation" in msg


# --- _clean_output ---------------------------------------------------------


def test_clean_output_strips_progress_and_blanks():
    raw = (
        "Deployment Image Servicing and Management tool\n"
        "[===========65.0%==========]\n"
        "\n"
        "The operation completed successfully.\n"
    )
    assert optimize._clean_output(raw) == "The operation completed successfully."


def test_clean_output_empty():
    assert optimize._clean_output("") == ""
    assert optimize._clean_output("\n   \n") == ""


# --- _run_subprocess error paths -------------------------------------------


def test_run_subprocess_command_not_found(monkeypatch):
    monkeypatch.setattr(optimize.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    ok, msg = optimize._run_subprocess(["nope"], _op())
    assert ok is False
    assert "Command not found" in msg


def test_run_subprocess_timeout(monkeypatch):
    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=60)

    monkeypatch.setattr(optimize.subprocess, "run", _boom)
    ok, msg = optimize._run_subprocess(["x"], _op(), timeout=60)
    assert ok is False
    assert "Timed out" in msg


def test_run_subprocess_os_error(monkeypatch):
    def _boom(*a, **k):
        raise OSError("access denied")

    monkeypatch.setattr(optimize.subprocess, "run", _boom)
    ok, msg = optimize._run_subprocess(["x"], _op())
    assert ok is False
    assert "access denied" in msg


def test_run_subprocess_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        optimize.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=2, stdout="", stderr="")
    )
    ok, msg = optimize._run_subprocess(["x"], _op())
    assert ok is False
    assert msg == "exit 2"


# --- _entry_bytes ----------------------------------------------------------


def test_entry_bytes_file(tmp_path):
    f = tmp_path / "f.bin"
    f.write_bytes(b"x" * 500)
    assert optimize._entry_bytes(f) == 500


def test_entry_bytes_directory_recurses(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "a").write_bytes(b"x" * 100)
    sub = d / "sub"
    sub.mkdir()
    (sub / "b").write_bytes(b"y" * 200)
    assert optimize._entry_bytes(d) == 300


def test_entry_bytes_missing_returns_zero(tmp_path):
    assert optimize._entry_bytes(tmp_path / "ghost") == 0


def test_entry_bytes_skips_unstattable_files(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "a").write_bytes(b"x" * 10)

    base = type(d)

    class _Bad(base):
        # is_dir stays True so the walk runs; per-file stat fails → skipped.
        def is_dir(self, *a, **k):
            return True

        def stat(self, *a, **k):
            raise OSError("stat failed")

    assert optimize._entry_bytes(_Bad(str(d))) == 0


# --- _delete_dir_contents --------------------------------------------------


def test_delete_dir_contents_missing_dir(tmp_path):
    ok, msg = optimize._delete_dir_contents(tmp_path / "nope", _op("prefetch"))
    assert ok is True
    assert "nothing to do" in msg


def test_delete_dir_contents_iterdir_error():
    class _FakeDir:
        def exists(self):
            return True

        def iterdir(self):
            raise OSError("permission denied")

    ok, msg = optimize._delete_dir_contents(_FakeDir(), _op("prefetch"))
    assert ok is False
    assert "permission denied" in msg


def test_delete_dir_contents_skips_protected(monkeypatch, tmp_path):
    from sifty.core.safety import ProtectedPathError

    d = tmp_path / "d"
    d.mkdir()
    (d / "ok.txt").write_bytes(b"x" * 10)
    (d / "bad.txt").write_bytes(b"y" * 20)

    def trash(p, allow_subtrees=None, dry_run=True):
        if p.name == "bad.txt":
            raise ProtectedPathError("refused")

    monkeypatch.setattr("sifty.core.safety.trash", trash)
    ok, msg = optimize._delete_dir_contents(d, _op("prefetch"))
    assert ok is True
    assert msg == "1 items freed (1 skipped)"
