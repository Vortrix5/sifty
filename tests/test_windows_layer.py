"""Tests for the remaining windows/ OS primitives.

win32service, subprocess, the toast library, send2trash, winshell and
win32com are all faked, so nothing touches real services / disks / the
Recycle Bin.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from sifty.windows import hyperv, notify, recyclebin, services_api

# === services_api ==========================================================


class _FakeWin32Service:
    SC_MANAGER_CONNECT = 1
    SERVICE_QUERY_CONFIG = 2
    SERVICE_CHANGE_CONFIG = 3
    SERVICE_NO_CHANGE = -1

    def __init__(self, start_code=4, raise_on=None):
        self.start_code = start_code
        self.raise_on = raise_on  # "open" | "change"
        self.changed = None
        self.closed = []

    def OpenSCManager(self, machine, db, access):
        return "SCM"

    def OpenService(self, scm, name, access):
        if self.raise_on == "open":
            raise OSError("service not found")
        return f"SVC:{name}"

    def QueryServiceConfig(self, svc):
        return (None, self.start_code, None)

    def ChangeServiceConfig(self, svc, *args):
        if self.raise_on == "change":
            raise OSError("access denied")
        self.changed = args

    def CloseServiceHandle(self, handle):
        self.closed.append(handle)


def test_get_start_type_maps_code(monkeypatch):
    monkeypatch.setattr(services_api, "win32service", _FakeWin32Service(start_code=4))
    assert services_api.get_start_type("DiagTrack") == "disabled"


def test_get_start_type_auto(monkeypatch):
    monkeypatch.setattr(services_api, "win32service", _FakeWin32Service(start_code=2))
    assert services_api.get_start_type("Spooler") == "auto"


def test_get_start_type_unknown_code(monkeypatch):
    monkeypatch.setattr(services_api, "win32service", _FakeWin32Service(start_code=99))
    assert services_api.get_start_type("Weird") is None


def test_get_start_type_exception(monkeypatch):
    monkeypatch.setattr(services_api, "win32service", _FakeWin32Service(raise_on="open"))
    assert services_api.get_start_type("Missing") is None


def test_set_start_type_success(monkeypatch):
    fake = _FakeWin32Service()
    monkeypatch.setattr(services_api, "win32service", fake)
    assert services_api.set_start_type("DiagTrack", "disabled") is True
    # start-type code (4 for disabled) is the second positional arg
    assert fake.changed[1] == 4


def test_set_start_type_invalid_mode(monkeypatch):
    monkeypatch.setattr(services_api, "win32service", _FakeWin32Service())
    assert services_api.set_start_type("DiagTrack", "bogus") is False


def test_set_start_type_change_fails(monkeypatch):
    monkeypatch.setattr(services_api, "win32service", _FakeWin32Service(raise_on="change"))
    assert services_api.set_start_type("DiagTrack", "disabled") is False


# === hyperv ================================================================


def test_list_vhdx_files_filters_sorts_and_recurses(tmp_path):
    (tmp_path / "big.vhdx").write_bytes(b"x" * 200)
    (tmp_path / "small.vhd").write_bytes(b"y" * 100)
    (tmp_path / "notes.txt").write_bytes(b"z" * 999)
    sub = tmp_path / "vms"
    sub.mkdir()
    (sub / "nested.VHDX").write_bytes(b"w" * 150)
    missing = tmp_path / "ghost"  # non-existent root is skipped

    results = hyperv.list_vhdx_files([tmp_path, missing])
    assert [p.name for p, _ in results] == ["big.vhdx", "nested.VHDX", "small.vhd"]
    assert all(p.suffix.lower() in (".vhdx", ".vhd") for p, _ in results)


def test_list_vhdx_files_uses_default_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(hyperv, "_DEFAULT_SEARCH_ROOTS", [tmp_path / "nope"])
    assert hyperv.list_vhdx_files() == []


def test_list_vhdx_files_skips_unstattable(monkeypatch, tmp_path):
    # walk yields a .vhdx that doesn't exist on disk → stat raises → skipped.
    monkeypatch.setattr(hyperv.os, "walk", lambda root, onerror=None: [(str(tmp_path), [], ["ghost.vhdx"])])
    assert hyperv.list_vhdx_files([tmp_path]) == []


def test_compact_vhdx_success(monkeypatch):
    monkeypatch.setattr(
        hyperv.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="The operation completed successfully.", stderr=""),
    )
    ok, msg = hyperv.compact_vhdx(Path("a.vhdx"))
    assert ok is True
    assert "completed" in msg


def test_compact_vhdx_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        hyperv.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=5, stdout="", stderr="")
    )
    ok, msg = hyperv.compact_vhdx(Path("a.vhdx"))
    assert ok is False
    assert msg == "exit 5"


def test_compact_vhdx_dism_missing(monkeypatch):
    monkeypatch.setattr(
        hyperv.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError())
    )
    ok, msg = hyperv.compact_vhdx(Path("a.vhdx"))
    assert ok is False
    assert "DISM not found" in msg


def test_compact_vhdx_timeout(monkeypatch):
    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="DISM", timeout=300)

    monkeypatch.setattr(hyperv.subprocess, "run", _boom)
    ok, msg = hyperv.compact_vhdx(Path("a.vhdx"))
    assert ok is False
    assert "Timed out" in msg


def test_compact_vhdx_os_error(monkeypatch):
    def _boom(*a, **k):
        raise OSError("io error")

    monkeypatch.setattr(hyperv.subprocess, "run", _boom)
    ok, msg = hyperv.compact_vhdx(Path("a.vhdx"))
    assert ok is False
    assert "io error" in msg


# === notify ================================================================


def test_toast_success(monkeypatch):
    shown = {}

    class _Toast:
        def __init__(self):
            self.text_fields = None

    class _Toaster:
        def __init__(self, app):
            shown["app"] = app

        def show_toast(self, notification):
            shown["fields"] = notification.text_fields

    monkeypatch.setitem(
        sys.modules, "windows_toasts", SimpleNamespace(Toast=_Toast, WindowsToaster=_Toaster)
    )
    assert notify.toast("Title", "Body", "MyApp") is True
    assert shown["app"] == "MyApp"
    assert shown["fields"] == ["Title", "Body"]


def test_toast_failure_returns_false(monkeypatch):
    # None in sys.modules makes `from windows_toasts import ...` raise ImportError.
    monkeypatch.setitem(sys.modules, "windows_toasts", None)
    assert notify.toast("Title", "Body") is False


# === recyclebin ============================================================


def test_send_to_trash_delegates(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(recyclebin, "send2trash", lambda p: called.append(p))
    recyclebin.send_to_trash(tmp_path / "junk.tmp")
    assert called == [os.fspath(tmp_path / "junk.tmp")]


def test_restore_via_winshell(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "winshell", SimpleNamespace(undelete=lambda t: calls.append(t)))
    assert recyclebin.restore("C:\\x\\y.txt") is True
    assert calls == [os.fspath(Path("C:\\x\\y.txt"))]


def test_restore_falls_back_when_winshell_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "winshell", None)  # ImportError
    monkeypatch.setattr(recyclebin, "_restore_via_shell", lambda t: "FELLBACK")
    assert recyclebin.restore("x") == "FELLBACK"


def test_restore_falls_back_when_winshell_errors(monkeypatch):
    def _boom(_t):
        raise RuntimeError("undelete blew up")

    monkeypatch.setitem(sys.modules, "winshell", SimpleNamespace(undelete=_boom))
    monkeypatch.setattr(recyclebin, "_restore_via_shell", lambda t: False)
    assert recyclebin.restore("x") is False


class _Verb:
    def __init__(self, name, sink):
        self.Name = name
        self._sink = sink

    def DoIt(self):
        self._sink.append("did")


class _Item:
    def __init__(self, name, verbs):
        self.Name = name
        self._verbs = verbs

    def Verbs(self):
        return self._verbs


class _Recycle:
    def __init__(self, items, location):
        self._items = items
        self._location = location

    def Items(self):
        return self._items

    def GetDetailsOf(self, item, column):
        return self._location


def _install_shell(monkeypatch, recycle):
    shell = SimpleNamespace(Namespace=lambda n: recycle)
    fake_client = SimpleNamespace(Dispatch=lambda progid: shell)
    monkeypatch.setitem(sys.modules, "win32com", SimpleNamespace(client=fake_client))
    monkeypatch.setitem(sys.modules, "win32com.client", fake_client)


def test_restore_via_shell_restores_matching_item(monkeypatch):
    sink = []
    item = _Item("y.txt", [_Verb("&Restore", sink)])
    _install_shell(monkeypatch, _Recycle([item], "C:\\x"))
    assert recyclebin._restore_via_shell("C:\\x\\y.txt") is True
    assert sink == ["did"]


def test_restore_via_shell_no_matching_item(monkeypatch):
    sink = []
    item = _Item("other.txt", [_Verb("&Restore", sink)])
    _install_shell(monkeypatch, _Recycle([item], "D:\\z"))
    assert recyclebin._restore_via_shell("C:\\x\\y.txt") is False
    assert sink == []


def test_restore_via_shell_match_without_restore_verb(monkeypatch):
    sink = []
    item = _Item("y.txt", [_Verb("&Delete", sink)])
    _install_shell(monkeypatch, _Recycle([item], "C:\\x"))
    assert recyclebin._restore_via_shell("C:\\x\\y.txt") is False
    assert sink == []


def test_restore_via_shell_handles_exception(monkeypatch):
    fake_client = SimpleNamespace(Dispatch=lambda progid: (_ for _ in ()).throw(RuntimeError("COM error")))
    monkeypatch.setitem(sys.modules, "win32com", SimpleNamespace(client=fake_client))
    monkeypatch.setitem(sys.modules, "win32com.client", fake_client)
    assert recyclebin._restore_via_shell("C:\\x\\y.txt") is False
