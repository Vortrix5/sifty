"""Tests for self-update version checks and the editable-install guard."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import httpx

from sifty.core import selfupdate


class _FakeDist:
    """Stand-in for importlib.metadata.Distribution with a canned direct_url.json."""

    def __init__(self, payload: str | None) -> None:
        self._payload = payload

    def read_text(self, name: str) -> str | None:
        assert name == "direct_url.json"
        return self._payload


def _patch_dist(monkeypatch, payload: str | None) -> None:
    monkeypatch.setattr(selfupdate, "distribution", lambda _pkg: _FakeDist(payload))


def test_editable_install_detected(monkeypatch):
    payload = json.dumps({"url": "file:///C:/Users/u/proj", "dir_info": {"editable": True}})
    _patch_dist(monkeypatch, payload)
    assert selfupdate.is_editable_install() is True
    assert selfupdate.editable_install_path() is not None


def test_normal_pypi_install_not_editable(monkeypatch):
    # A wheel install from an index records a https url and no editable marker.
    payload = json.dumps({"url": "https://files.pythonhosted.org/x/sifty.whl"})
    _patch_dist(monkeypatch, payload)
    assert selfupdate.is_editable_install() is False
    assert selfupdate.editable_install_path() is None


def test_no_direct_url_metadata(monkeypatch):
    # Most index installs have no direct_url.json at all.
    _patch_dist(monkeypatch, None)
    assert selfupdate.is_editable_install() is False


def test_editable_marker_false(monkeypatch):
    payload = json.dumps({"url": "file:///x", "dir_info": {"editable": False}})
    _patch_dist(monkeypatch, payload)
    assert selfupdate.is_editable_install() is False


def test_malformed_direct_url(monkeypatch):
    _patch_dist(monkeypatch, "{not valid json")
    assert selfupdate.is_editable_install() is False


def test_package_not_found(monkeypatch):
    def _raise(_pkg):
        raise selfupdate.PackageNotFoundError

    monkeypatch.setattr(selfupdate, "distribution", _raise)
    assert selfupdate.is_editable_install() is False


def test_apply_update_refuses_editable(monkeypatch):
    # The guard must short-circuit before any pipx subprocess runs.
    monkeypatch.setattr(selfupdate, "is_editable_install", lambda: True)
    called = False

    def _fail(*a, **k):
        nonlocal called
        called = True
        raise AssertionError("pipx must not be invoked on an editable install")

    monkeypatch.setattr(selfupdate.subprocess, "run", _fail)
    ok, msg = selfupdate.apply_update()
    assert ok is False
    assert "editable" in msg.lower()
    assert called is False


# --- _parse ----------------------------------------------------------------


def test_parse_versions():
    assert selfupdate._parse("1.2.3") == (1, 2, 3)
    assert selfupdate._parse("10.20.30") == (10, 20, 30)
    assert selfupdate._parse("1.2") == (1, 2)
    assert selfupdate._parse("") == (0,)
    assert selfupdate._parse("2.0.0b") == (2, 0, 0)  # non-digit suffix dropped
    assert selfupdate._parse("v3.1.4") == (3, 1, 4)  # leading 'v' ignored


# --- current_version -------------------------------------------------------


def test_current_version_success(monkeypatch):
    monkeypatch.setattr(selfupdate, "pkg_version", lambda pkg: "0.6.0")
    assert selfupdate.current_version() == "0.6.0"


def test_current_version_not_installed(monkeypatch):
    def _raise(pkg):
        raise selfupdate.PackageNotFoundError

    monkeypatch.setattr(selfupdate, "pkg_version", _raise)
    assert selfupdate.current_version() == "0.0.0"


# --- editable_install_path extra branches ----------------------------------


def test_editable_install_path_non_file_url(monkeypatch):
    payload = json.dumps({"url": "git+https://github.com/x", "dir_info": {"editable": True}})
    _patch_dist(monkeypatch, payload)
    assert selfupdate.editable_install_path() == "git+https://github.com/x"


def test_editable_install_path_url2pathname_error(monkeypatch):
    payload = json.dumps({"url": "file:///C:/x", "dir_info": {"editable": True}})
    _patch_dist(monkeypatch, payload)

    def _boom(_p):
        raise ValueError("bad path")

    monkeypatch.setattr(selfupdate, "url2pathname", _boom)
    assert selfupdate.editable_install_path() == "file:///C:/x"


# --- latest_version --------------------------------------------------------


def test_latest_version_success(monkeypatch):
    resp = SimpleNamespace(status_code=200, json=lambda: {"info": {"version": "9.9.9"}})
    monkeypatch.setattr(httpx, "get", lambda *a, **k: resp)
    assert selfupdate.latest_version() == "9.9.9"


def test_latest_version_non_200(monkeypatch):
    resp = SimpleNamespace(status_code=404, json=lambda: {})
    monkeypatch.setattr(httpx, "get", lambda *a, **k: resp)
    assert selfupdate.latest_version() is None


def test_latest_version_offline(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(httpx, "get", _boom)
    assert selfupdate.latest_version() is None


# --- check_update ----------------------------------------------------------


def test_check_update_newer_available(monkeypatch):
    monkeypatch.setattr(selfupdate, "current_version", lambda: "0.5.0")
    monkeypatch.setattr(selfupdate, "latest_version", lambda: "0.6.0")
    assert selfupdate.check_update() == ("0.5.0", "0.6.0")


def test_check_update_up_to_date(monkeypatch):
    monkeypatch.setattr(selfupdate, "current_version", lambda: "0.6.0")
    monkeypatch.setattr(selfupdate, "latest_version", lambda: "0.6.0")
    assert selfupdate.check_update() == ("0.6.0", None)


def test_check_update_check_failed(monkeypatch):
    monkeypatch.setattr(selfupdate, "current_version", lambda: "0.6.0")
    monkeypatch.setattr(selfupdate, "latest_version", lambda: None)
    assert selfupdate.check_update() == ("0.6.0", None)


# --- apply_update (non-editable paths) -------------------------------------


def test_apply_update_success(monkeypatch):
    monkeypatch.setattr(selfupdate, "is_editable_install", lambda: False)
    monkeypatch.setattr(
        selfupdate.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="upgrading sifty\nall done", stderr=""),
    )
    ok, msg = selfupdate.apply_update()
    assert ok is True
    assert msg == "all done"  # last line of output


def test_apply_update_failure(monkeypatch):
    monkeypatch.setattr(selfupdate, "is_editable_install", lambda: False)
    monkeypatch.setattr(
        selfupdate.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="error: boom"),
    )
    ok, msg = selfupdate.apply_update()
    assert ok is False
    assert msg == "error: boom"


def test_apply_update_empty_output(monkeypatch):
    monkeypatch.setattr(selfupdate, "is_editable_install", lambda: False)
    monkeypatch.setattr(
        selfupdate.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    ok, msg = selfupdate.apply_update()
    assert ok is True
    assert msg == "Upgraded successfully."


def test_apply_update_pipx_missing(monkeypatch):
    monkeypatch.setattr(selfupdate, "is_editable_install", lambda: False)

    def _boom(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(selfupdate.subprocess, "run", _boom)
    ok, msg = selfupdate.apply_update()
    assert ok is False
    assert "pipx not found" in msg


def test_apply_update_timeout(monkeypatch):
    monkeypatch.setattr(selfupdate, "is_editable_install", lambda: False)

    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="pipx", timeout=120)

    monkeypatch.setattr(selfupdate.subprocess, "run", _boom)
    ok, msg = selfupdate.apply_update()
    assert ok is False
    assert "timed out" in msg.lower()


def test_apply_update_os_error(monkeypatch):
    monkeypatch.setattr(selfupdate, "is_editable_install", lambda: False)

    def _boom(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr(selfupdate.subprocess, "run", _boom)
    ok, msg = selfupdate.apply_update()
    assert ok is False
    assert "permission denied" in msg
