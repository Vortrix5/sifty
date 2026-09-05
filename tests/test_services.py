"""Tests for the curated services manager (win32 layer mocked)."""

from __future__ import annotations

from sifty.core import services
from sifty.windows import services_api


def test_can_manage_allowlist_and_denylist():
    assert services.can_manage("DiagTrack") is True       # on the allowlist
    assert services.can_manage("RpcSs") is False           # critical denylist
    assert services.can_manage("SomeRandomSvc") is False   # not curated


def test_set_start_type_refuses_off_allowlist(monkeypatch):
    called = []
    monkeypatch.setattr(services_api, "set_start_type", lambda n, m: called.append((n, m)) or True)
    # Off-allowlist is refused without ever touching the OS layer.
    assert services.set_start_type("RpcSs", "disabled") is False
    assert called == []
    # Invalid mode refused too.
    assert services.set_start_type("DiagTrack", "bogus") is False
    assert called == []
    # Allowed service + valid mode goes through.
    assert services.set_start_type("DiagTrack", "disabled") is True
    assert called == [("DiagTrack", "disabled")]


def test_list_services_maps_state(monkeypatch):
    monkeypatch.setattr(
        services_api, "get_start_type",
        lambda name: "disabled" if name == "DiagTrack" else None,
    )
    items = {s.name: s for s in services.list_services()}
    assert items["DiagTrack"].start_type == "disabled" and items["DiagTrack"].present
    assert items["Fax"].start_type == "absent" and items["Fax"].present is False


def test_is_present_reflects_installed_state(monkeypatch):
    monkeypatch.setattr(services_api, "get_start_type",
                        lambda n: "manual" if n == "Fax" else None)
    assert services.is_present("Fax") is True
    assert services.is_present("NotInstalled") is False


def test_missing_service_is_logged_as_absent_not_admin_failure(monkeypatch, caplog):
    """Issue #37's log noise: error 1060 means absent, not 'admin required'."""
    class _Win32Error(Exception):
        winerror = services_api.ERROR_SERVICE_DOES_NOT_EXIST

    class _FakeWin32Service:
        SC_MANAGER_CONNECT = SERVICE_CHANGE_CONFIG = SERVICE_NO_CHANGE = 0

        @staticmethod
        def OpenSCManager(*a):
            raise _Win32Error("service does not exist")

    monkeypatch.setattr(services_api, "win32service", _FakeWin32Service)
    with caplog.at_level("DEBUG", logger="sifty.windows"):
        assert services_api.set_start_type("Fax", "disabled") is False
    assert "not installed" in caplog.text
    assert "admin required" not in caplog.text
