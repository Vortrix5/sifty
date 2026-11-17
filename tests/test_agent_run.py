"""Tests for the proactive agent run - especially the unattended auto-fix rails."""

from __future__ import annotations

import pytest

from sifty.core import anomaly, checkup, disk, history, junk
from sifty.core.agent_run import _AUTOFIX_ALLOWLIST, eligible_autofix_keys, run_agent
from sifty.core.models import CategoryScan, CleanResult, JunkCategory
from sifty.infra.config import DEFAULTS, Config, _deep_merge

_GB = 1 << 30


@pytest.fixture
def temp_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def _cfg(**agent_overrides) -> Config:
    return Config(data=_deep_merge(DEFAULTS, {"agent": agent_overrides} if agent_overrides else {}))


def _cat(key: str, size: int, admin: bool = False) -> CategoryScan:
    return CategoryScan(JunkCategory(key, key, "", [], requires_admin=admin), size, 1, [])


def _finding(domain="junk", severity="attention", summary="1.0 GB reclaimable"):
    return checkup.Finding(domain, domain.title(), summary, severity, domain, "Fix")


@pytest.fixture
def stub_scans(monkeypatch):
    """Stub the read-only scans + anomaly writes so tests don't touch the machine."""
    monkeypatch.setattr(checkup, "run_checkup", lambda: [_finding()])
    monkeypatch.setattr(disk, "volumes", lambda: [])
    monkeypatch.setattr(anomaly, "detect", lambda **k: [])
    monkeypatch.setattr(anomaly, "record_snapshot", lambda **k: None)
    # Category list for the admin-exclusion check inside eligible_autofix_keys.
    monkeypatch.setattr(junk, "junk_categories", lambda config=None: [
        JunkCategory("user-temp", "", "", [], requires_admin=False),
        JunkCategory("windows-temp", "", "", [], requires_admin=True),
    ])


# --- the core safety invariant ---------------------------------------------

def test_allowlist_contains_no_admin_categories(monkeypatch, tmp_path):
    # Populate env so every category is actually constructed, then verify none
    # of the auto-fix allowlist keys are declared admin in junk.py.
    for var in ("TEMP", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(var, str(tmp_path))
    cats = {c.key: c for c in junk.junk_categories()}
    for key in _AUTOFIX_ALLOWLIST:
        if key in cats:
            assert cats[key].requires_admin is False, f"{key} is an admin category!"
    admin_keys = {k for k, c in cats.items() if c.requires_admin}
    assert _AUTOFIX_ALLOWLIST.isdisjoint(admin_keys)


def test_config_cannot_widen_allowlist(temp_appdata, stub_scans):
    # User tries to add admin/system categories; they must be dropped.
    cfg = _cfg(auto_fix_categories=["user-temp", "windows-temp", "windows-old"])
    assert eligible_autofix_keys(cfg) == {"user-temp"}


# --- run_agent behaviour ----------------------------------------------------

def test_notify_only_by_default_never_cleans(temp_appdata, stub_scans, monkeypatch):
    cleaned = []
    monkeypatch.setattr(junk, "scan", lambda config=None: [_cat("user-temp", 5 * _GB)])
    monkeypatch.setattr(junk, "clean", lambda *a, **k: cleaned.append(k) or CleanResult(0, 0, [], []))
    toasts = []
    result = run_agent(apply=False, config=_cfg(), notify_fn=lambda t, m: toasts.append(m) or True)

    assert cleaned == []                 # apply=False => never deletes
    assert result.auto_fixed is None
    assert result.notified is True and toasts  # it did toast the finding


def test_apply_cleans_only_allowlisted_keys(temp_appdata, stub_scans, monkeypatch):
    captured = {}

    def fake_clean(config=None, only=None, *, dry_run=True, extra_protected=None):
        captured["only"] = only
        captured["dry_run"] = dry_run
        return CleanResult(3 * _GB, 42, [], [])

    monkeypatch.setattr(junk, "scan", lambda config=None: [
        _cat("user-temp", 3 * _GB),          # allowlisted
        _cat("windows-temp", 9 * _GB, admin=True),  # admin -> excluded
    ])
    monkeypatch.setattr(junk, "clean", fake_clean)
    recorded = {}
    monkeypatch.setattr(history, "record_clean",
                        lambda *a, **k: recorded.update(action=a[0], detail=a[1]) or 7)

    result = run_agent(apply=True, config=_cfg(auto_fix_min_bytes=100),
                       notify_fn=lambda t, m: True)

    # Cleans the whole low-risk allowlist; the admin category is never passed.
    assert captured["only"] == set(_AUTOFIX_ALLOWLIST)
    assert "windows-temp" not in captured["only"]
    assert captured["dry_run"] is False
    assert result.auto_fixed is not None
    assert result.auto_fixed.run_id == 7          # recorded -> undoable
    assert recorded["action"] == "agent"


def test_below_min_bytes_is_notify_only(temp_appdata, stub_scans, monkeypatch):
    cleaned = []
    monkeypatch.setattr(junk, "scan", lambda config=None: [_cat("user-temp", 10 * 1024 * 1024)])  # 10 MB
    monkeypatch.setattr(junk, "clean", lambda *a, **k: cleaned.append(1) or CleanResult(0, 0, [], []))
    result = run_agent(apply=True, config=_cfg(auto_fix_min_bytes=500 * 1024 * 1024),
                       notify_fn=lambda t, m: True)
    assert cleaned == []
    assert result.auto_fixed is None


def test_elevated_skipped_reports_admin_junk(temp_appdata, stub_scans, monkeypatch):
    monkeypatch.setattr(junk, "scan", lambda config=None: [
        _cat("user-temp", 1 * _GB),
        _cat("windows-temp", 2 * _GB, admin=True),
    ])
    result = run_agent(apply=False, config=_cfg(), notify_fn=lambda t, m: True)
    assert result.elevated_skipped == ["windows-temp"]


def test_toast_failure_does_not_raise(temp_appdata, stub_scans, monkeypatch):
    monkeypatch.setattr(junk, "scan", lambda config=None: [_cat("user-temp", 1 * _GB)])
    result = run_agent(apply=False, config=_cfg(), notify_fn=lambda t, m: False)
    assert result.notified is False


def test_all_clear_sends_no_toast(temp_appdata, stub_scans, monkeypatch):
    monkeypatch.setattr(checkup, "run_checkup", lambda: [_finding(severity="ok", summary="nothing to clean")])
    monkeypatch.setattr(junk, "scan", lambda config=None: [])
    toasts = []
    result = run_agent(apply=False, config=_cfg(), notify_fn=lambda t, m: toasts.append(m) or True)
    assert result.headline == ""
    assert toasts == []            # don't nag when everything is fine
    assert result.notified is False
