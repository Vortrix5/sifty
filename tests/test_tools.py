"""Tests for the AI tool handlers - verifying they match the real core API.

These exercise the handlers directly (the agent tests use fake tools), which is
where three signature mismatches hid: find_duplicates returns a dict, startup
toggling is set_enabled(name, bool), and apply_upgrades returns an int code.
"""

from __future__ import annotations

import sifty.core.apps as apps_mod
import sifty.core.disk as disk_mod
import sifty.core.junk as junk_mod
import sifty.core.startup as startup_mod
import sifty.core.updates as updates_mod
from sifty.ai import tools
from sifty.ai.tools import ToolResult
from sifty.core.models import (
    CategoryScan,
    InstalledApp,
    JunkCategory,
    Upgrade,
)


def test_scan_junk_returns_table(monkeypatch):
    cats = [
        CategoryScan(JunkCategory("user-temp", "User temp", ""), 2048, 5, []),
        CategoryScan(JunkCategory("empty", "Empty", ""), 0, 0, []),  # filtered out
    ]
    monkeypatch.setattr(junk_mod, "scan", lambda: cats)
    res = tools.get("scan_junk").handler({})
    assert isinstance(res, ToolResult)
    assert res.has_table
    assert res.columns[0] == "Key"
    assert len(res.rows) == 1  # the zero-size category is dropped
    assert "user-temp" in res.summary


def test_find_duplicates_handles_dict_return(tmp_path, monkeypatch):
    """Regression: find_duplicates returns dict[hash, list[Path]], not a list."""
    a, b, c = tmp_path / "a.bin", tmp_path / "b.bin", tmp_path / "c.bin"
    for f in (a, b, c):
        f.write_bytes(b"x" * 100)
    monkeypatch.setattr(disk_mod, "find_duplicates", lambda p, **k: {"h1": [a, b], "h2": [c]})
    res = tools.get("find_duplicates").handler({"path": str(tmp_path)})
    assert isinstance(res, ToolResult)
    assert res.has_table
    # Only the group with >1 copy is reported.
    assert len(res.rows) == 1
    assert res.rows[0][0] == "2"  # two copies


def test_find_duplicates_missing_path():
    res = tools.get("find_duplicates").handler({"path": "Z:/does/not/exist"})
    assert not res.has_table
    assert "does not exist" in res.summary


def test_toggle_startup_uses_set_enabled(monkeypatch):
    calls = {}
    monkeypatch.setattr(startup_mod, "set_enabled",
                        lambda name, enabled: calls.update(name=name, enabled=enabled) or True)
    res = tools.get("toggle_startup").handler({"name": "Spotify", "enable": False})
    assert calls == {"name": "Spotify", "enabled": False}
    assert "disabled" in res.summary


def test_apply_updates_reads_exit_code(monkeypatch):
    monkeypatch.setattr(updates_mod, "apply_upgrades", lambda i=None: 0)
    ok = tools.get("apply_updates").handler({"id": "Foo.Bar"})
    assert "successfully" in ok.summary

    monkeypatch.setattr(updates_mod, "apply_upgrades", lambda i=None: 1)
    bad = tools.get("apply_updates").handler({"id": "Foo.Bar"})
    assert "code 1" in bad.summary


def test_list_updates_table(monkeypatch):
    monkeypatch.setattr(updates_mod, "list_upgrades",
                        lambda: [Upgrade("Firefox", "Mozilla.Firefox", "120", "121")])
    res = tools.get("list_updates").handler({})
    assert res.has_table
    assert res.rows[0] == ["Firefox", "120", "121"]


def test_list_updates_none(monkeypatch):
    monkeypatch.setattr(updates_mod, "list_upgrades", lambda: [])
    res = tools.get("list_updates").handler({})
    assert not res.has_table
    assert "up to date" in res.summary


def test_list_apps_table(monkeypatch):
    apps = [
        InstalledApp("Big", "1.0", "Pub", 5_000_000, "", "HKCU"),
        InstalledApp("Small", "2.0", "Pub", 1000, "", "HKCU"),
    ]
    monkeypatch.setattr(apps_mod, "installed_apps", lambda: apps)
    res = tools.get("list_apps").handler({})
    assert res.has_table
    assert res.rows[0][0] == "Big"  # largest first


def test_uninstall_app_passes_through(monkeypatch):
    monkeypatch.setattr(apps_mod, "uninstall_app", lambda name: (True, "removed"))
    res = tools.get("uninstall_app").handler({"name": "Foo"})
    assert "succeeded" in res.summary


def test_analyze_disk_real_dir(tmp_path):
    (tmp_path / "big.bin").write_bytes(b"x" * 500)
    (tmp_path / "small.bin").write_bytes(b"x" * 10)
    res = tools.get("analyze_disk").handler({"path": str(tmp_path)})
    assert res.has_table
    assert res.rows[0][0] == "big.bin"
