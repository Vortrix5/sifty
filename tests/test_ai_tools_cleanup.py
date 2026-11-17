"""Tests for the cleanup, services and virtual-disk agent tools.

Core functions are monkeypatched so nothing touches the real system; deletion
is stubbed at ``safety.send_to_trash`` so the safety checks still run.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from sifty.ai import tools
from sifty.core import cleanup, history, optimize, safety, services
from sifty.core.optimize import OptimizeOp
from sifty.windows import admin, hyperv


@pytest.fixture
def trashed(monkeypatch, tmp_path):
    """Record trash calls instead of touching the Recycle Bin, and isolate history."""
    calls: list[Path] = []
    monkeypatch.setattr(safety, "send_to_trash", lambda p: calls.append(Path(p)))
    monkeypatch.setattr(safety, "audit", lambda _m: None)
    monkeypatch.setattr(history, "record_clean", lambda *a, **k: 1)
    return calls


def _old(path: Path, days: int) -> None:
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


# --- stale downloads -------------------------------------------------------


def test_find_stale_downloads_lists_old_items(monkeypatch, tmp_path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "old.zip").write_bytes(b"x" * 100)
    (downloads / "new.zip").write_bytes(b"x" * 100)
    _old(downloads / "old.zip", 400)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    res = tools.get("find_stale_downloads").handler({"days": 180})
    assert [r[0] for r in res.rows] == ["old.zip"]


def test_stale_days_has_a_floor():
    # "days=0" must never mean "everything in Downloads".
    assert tools._stale_days({"days": 0}) == 180     # falsy -> default
    assert tools._stale_days({"days": 1}) == 30
    assert tools._stale_days({"days": "junk"}) == 180
    assert tools._stale_days({}) == 180


def test_clean_stale_downloads_trashes_only_old(monkeypatch, tmp_path, trashed):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "old.iso").write_bytes(b"x" * 100)
    (downloads / "recent.pdf").write_bytes(b"x" * 100)
    _old(downloads / "old.iso", 400)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    res = tools.get("clean_stale_downloads").handler({"days": 1})
    assert trashed == [downloads / "old.iso"]
    assert "Moved 1" in res.summary


def test_clean_stale_downloads_nothing(monkeypatch):
    monkeypatch.setattr(cleanup, "find_stale_downloads", lambda days: [])
    res = tools.get("clean_stale_downloads").handler({})
    assert "Nothing in Downloads" in res.summary


# --- large files -----------------------------------------------------------


def test_find_large_files_missing_path():
    res = tools.get("find_large_files").handler({"path": "Z:/nope/nowhere"})
    assert "does not exist" in res.summary


def test_find_large_files_table(monkeypatch, tmp_path):
    big = tmp_path / "big.iso"
    monkeypatch.setattr(cleanup, "find_large_files", lambda p, m, top=50: [(big, 500 * 1024 * 1024)])
    res = tools.get("find_large_files").handler({"path": str(tmp_path), "min_mb": 200})
    assert res.rows == [["500.0 MB", str(big)]]


def test_clean_large_files_files_only(tmp_path, trashed):
    f = tmp_path / "setup.exe"
    f.write_bytes(b"x" * 10)
    folder = tmp_path / "Projects"
    folder.mkdir()

    res = tools.get("clean_large_files").handler(
        {"paths": [str(f), str(folder), str(tmp_path / "missing.bin")]}
    )
    assert trashed == [f]          # the folder and the missing path are refused
    assert "2 skipped" in res.summary


def test_clean_large_files_refuses_protected(monkeypatch, tmp_path, trashed):
    win = tmp_path / "Windows"
    win.mkdir()
    sys_file = win / "big.dll"
    sys_file.write_bytes(b"x" * 10)
    monkeypatch.setenv("SystemRoot", str(win))

    res = tools.get("clean_large_files").handler({"paths": [str(sys_file)]})
    assert trashed == []
    assert "skipped" in res.summary


def test_clean_large_files_nothing_valid(trashed):
    res = tools.get("clean_large_files").handler({"paths": []})
    assert "Nothing removed" in res.summary
    assert trashed == []


# --- services --------------------------------------------------------------


def test_list_services_only_present(monkeypatch):
    from sifty.core.models import ServiceInfo
    monkeypatch.setattr(services, "list_services", lambda: [
        ServiceInfo("Fax", "Fax", "d", "manual", True),
        ServiceInfo("RetailDemo", "Retail Demo", "d", "absent", False),
    ])
    res = tools.get("list_services").handler({})
    assert [r[0] for r in res.rows] == ["Fax"]


def test_manage_service_refuses_unlisted(monkeypatch):
    called = []
    monkeypatch.setattr(services, "set_start_type", lambda n, m: called.append(n) or True)
    res = tools.get("manage_service").handler({"name": "RpcSs", "mode": "disabled"})
    assert "not on Sifty's list" in res.summary
    assert called == []


def test_manage_service_needs_admin(monkeypatch):
    monkeypatch.setattr(services, "is_present", lambda n: True)
    monkeypatch.setattr(admin, "is_admin", lambda: False)
    res = tools.get("manage_service").handler({"name": "Fax", "mode": "disabled"})
    assert "administrator" in res.summary


def test_manage_service_sets(monkeypatch):
    monkeypatch.setattr(services, "is_present", lambda n: True)
    monkeypatch.setattr(admin, "is_admin", lambda: True)
    monkeypatch.setattr(services, "set_start_type", lambda n, m: True)
    res = tools.get("manage_service").handler({"name": "Fax", "mode": "disabled"})
    assert "Set 'Fax' to disabled" in res.summary


# --- virtual disks ---------------------------------------------------------


def test_compact_virtual_disks_none(monkeypatch):
    monkeypatch.setattr(hyperv, "list_vhdx_files", lambda: [])
    res = tools.get("compact_virtual_disks").handler({})
    assert "No Hyper-V" in res.summary


def test_compact_virtual_disks_needs_admin(monkeypatch):
    monkeypatch.setattr(hyperv, "list_vhdx_files", lambda: [(Path("ext4.vhdx"), 10 * 1024**3)])
    monkeypatch.setattr(admin, "is_admin", lambda: False)
    ran = []
    monkeypatch.setattr(optimize, "run_op", lambda op, dry_run=True: ran.append(op) or (True, ""))
    res = tools.get("compact_virtual_disks").handler({})
    assert "administrator" in res.summary
    assert ran == []


def test_compact_virtual_disks_runs(monkeypatch):
    monkeypatch.setattr(hyperv, "list_vhdx_files", lambda: [(Path("ext4.vhdx"), 1)])
    monkeypatch.setattr(admin, "is_admin", lambda: True)
    monkeypatch.setattr(optimize, "run_op", lambda op, dry_run=True: (True, "1/1 VHD(s) compacted"))
    res = tools.get("compact_virtual_disks").handler({})
    assert "finished" in res.summary


def test_optimize_system_leaves_vhd_compact_to_its_own_tool(monkeypatch):
    monkeypatch.setattr(admin, "is_admin", lambda: True)
    ops = [OptimizeOp("dns-flush", "Flush DNS", "d", "instant"),
           OptimizeOp("compact-vhd", "Compact", "d", "x", requires_admin=True)]
    monkeypatch.setattr(optimize, "list_operations", lambda: ops)
    ran = []
    monkeypatch.setattr(optimize, "run_op", lambda op, dry_run=True: ran.append(op.key) or (True, "ok"))
    tools.get("optimize_system").handler({})
    assert ran == ["dns-flush"]


def test_every_destructive_new_tool_is_high_risk():
    for name in ("clean_stale_downloads", "clean_large_files", "manage_service", "compact_virtual_disks"):
        assert tools.get(name).risk == "high"
    for name in ("find_stale_downloads", "find_large_files", "list_services"):
        assert tools.get(name).risk == "read"
