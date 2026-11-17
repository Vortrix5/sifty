"""Tests for the read-only health checkup engine (domain scans mocked)."""

from __future__ import annotations

from types import SimpleNamespace

from sifty.core import checkup


def test_run_checkup_reports_all_domains(monkeypatch):
    monkeypatch.setattr(checkup, "CHECKS", [
        ("junk", lambda: checkup.Finding("junk", "Junk files", "1.0 GB reclaimable", "attention", "junk", "Clean junk")),
        ("disk", lambda: checkup.Finding("disk", "Disk space", "all volumes have headroom", "ok", "", "")),
    ])
    findings = checkup.run_checkup()
    assert [f.domain for f in findings] == ["junk", "disk"]
    assert findings[0].severity == "attention"
    assert findings[1].severity == "ok"


def test_run_checkup_only_filter(monkeypatch):
    calls = []
    monkeypatch.setattr(checkup, "CHECKS", [
        ("junk", lambda: calls.append("junk") or checkup.Finding("junk", "Junk files", "x", "ok", "", "")),
        ("disk", lambda: calls.append("disk") or checkup.Finding("disk", "Disk space", "x", "ok", "", "")),
    ])
    findings = checkup.run_checkup(only={"disk"})
    assert [f.domain for f in findings] == ["disk"]
    assert calls == ["disk"]


def test_run_checkup_survives_failing_check(monkeypatch):
    def boom():
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(checkup, "CHECKS", [("junk", boom)])
    findings = checkup.run_checkup()
    assert len(findings) == 1
    assert "check failed" in findings[0].summary
    assert findings[0].severity == "ok"  # a failed probe is reported, not alarmed


def test_junk_check_severity_thresholds(monkeypatch):
    from sifty.core import junk

    class FakeScan:
        def __init__(self, size):
            self.size = size

    monkeypatch.setattr(junk, "scan", lambda: [FakeScan(2 << 30)])
    f = checkup._check_junk()
    assert f.severity == "attention" and f.action_key == "junk"

    monkeypatch.setattr(junk, "scan", lambda: [FakeScan(10)])
    f = checkup._check_junk()
    assert f.severity == "ok" and f.action_label == ""

    monkeypatch.setattr(junk, "scan", lambda: [FakeScan(100 << 20)])  # 100 MB → info
    f = checkup._check_junk()
    assert f.severity == "info"


# --- human_size ------------------------------------------------------------


def test_human_size_units():
    assert checkup.human_size(0) == "0 B"
    assert checkup.human_size(512) == "512 B"
    assert checkup.human_size(1536) == "1.5 KB"
    assert checkup.human_size(5 * 1024 * 1024) == "5.0 MB"
    assert checkup.human_size(3 * (1 << 30)) == "3.0 GB"
    assert checkup.human_size(2 * (1 << 40)) == "2.0 TB"


# --- _check_updates --------------------------------------------------------


def test_check_updates_winget_unavailable(monkeypatch):
    from sifty.windows import winget

    monkeypatch.setattr(winget, "available", lambda: False)
    f = checkup._check_updates()
    assert f.severity == "ok"
    assert "unavailable" in f.summary


def test_check_updates_up_to_date(monkeypatch):
    from sifty.core import updates
    from sifty.windows import winget

    monkeypatch.setattr(winget, "available", lambda: True)
    monkeypatch.setattr(updates, "list_upgrades", lambda: [])
    assert checkup._check_updates().severity == "ok"


def test_check_updates_a_few(monkeypatch):
    from sifty.core import updates
    from sifty.windows import winget

    monkeypatch.setattr(winget, "available", lambda: True)
    monkeypatch.setattr(updates, "list_upgrades", lambda: [1, 2])
    f = checkup._check_updates()
    assert f.severity == "info"
    assert f.action_key == "updates"


def test_check_updates_many(monkeypatch):
    from sifty.core import updates
    from sifty.windows import winget

    monkeypatch.setattr(winget, "available", lambda: True)
    monkeypatch.setattr(updates, "list_upgrades", lambda: list(range(7)))
    assert checkup._check_updates().severity == "attention"


# --- _check_orphans --------------------------------------------------------


def test_check_orphans_none(monkeypatch):
    from sifty.core import registry_scan

    monkeypatch.setattr(registry_scan, "find_orphan_uninstall_entries", lambda: [])
    assert checkup._check_orphans().severity == "ok"


def test_check_orphans_singular(monkeypatch):
    from sifty.core import registry_scan

    monkeypatch.setattr(registry_scan, "find_orphan_uninstall_entries", lambda: [object()])
    f = checkup._check_orphans()
    assert f.severity == "info"
    assert "entry" in f.summary and "entries" not in f.summary


def test_check_orphans_plural(monkeypatch):
    from sifty.core import registry_scan

    monkeypatch.setattr(registry_scan, "find_orphan_uninstall_entries", lambda: [object(), object()])
    assert "entries" in checkup._check_orphans().summary


# --- _check_stale ----------------------------------------------------------


def test_check_stale_none(monkeypatch):
    from sifty.core import cleanup

    monkeypatch.setattr(cleanup, "find_stale_downloads", lambda: [])
    assert checkup._check_stale().severity == "ok"


def test_check_stale_info(monkeypatch):
    from sifty.core import cleanup

    monkeypatch.setattr(cleanup, "find_stale_downloads", lambda: [("a", 1000, 0.0)])
    f = checkup._check_stale()
    assert f.severity == "info"
    assert f.action_key == "cleanup"


def test_check_stale_attention(monkeypatch):
    from sifty.core import cleanup

    monkeypatch.setattr(cleanup, "find_stale_downloads", lambda: [("a", 2 << 30, 0.0)])
    assert checkup._check_stale().severity == "attention"


# --- _check_disk -----------------------------------------------------------


def test_check_disk_ok(monkeypatch):
    from sifty.core import watch

    monkeypatch.setattr(watch, "low_space", lambda: [])
    assert checkup._check_disk().severity == "ok"


def test_check_disk_low(monkeypatch):
    from sifty.core import watch

    monkeypatch.setattr(watch, "low_space", lambda: [SimpleNamespace(mountpoint="C:\\", free=5 << 30)])
    f = checkup._check_disk()
    assert f.severity == "attention"
    assert "C:\\" in f.summary


# --- _check_startup --------------------------------------------------------


def test_check_startup_ok(monkeypatch):
    from sifty.core import startup

    monkeypatch.setattr(startup, "list_entries", lambda: [SimpleNamespace(enabled=True) for _ in range(3)])
    assert checkup._check_startup().severity == "ok"


def test_check_startup_many(monkeypatch):
    from sifty.core import startup

    monkeypatch.setattr(startup, "list_entries", lambda: [SimpleNamespace(enabled=True) for _ in range(10)])
    f = checkup._check_startup()
    assert f.severity == "info"
    assert f.action_key == "startup"


# --- run_checkup -----------------------------------------------------------


def test_run_checkup_empty_when_only_matches_nothing():
    assert checkup.run_checkup(only={"does-not-exist"}) == []
