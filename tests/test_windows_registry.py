"""Tests for the Windows registry + winget primitives.

`winreg` and `subprocess` are faked so these run deterministically on any OS.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from sifty.windows import registry, winget

# --- fake winreg -----------------------------------------------------------

# A subkey/value name of "__RAISE__" makes the fake enumerator raise OSError at
# that index, exercising the `except OSError: continue` skip branches.
_RAISE = "__RAISE__"


class _FakeKey:
    def __init__(self, store, path):
        self._store = store
        self._path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeWinreg:
    HKEY_LOCAL_MACHINE = "HKLM"
    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = 1
    KEY_SET_VALUE = 2

    def __init__(self, data=None):
        # {(hive, subkey): {"subkeys": [...], "values": {name: value}}}
        self.data = data or {}

    def OpenKey(self, hive, subkey, *_args):
        path = (hive, subkey)
        if path not in self.data:
            raise OSError("key not found")
        return _FakeKey(self.data, path)

    def CreateKey(self, hive, subkey):
        path = (hive, subkey)
        self.data.setdefault(path, {"subkeys": [], "values": {}})
        return _FakeKey(self.data, path)

    def QueryInfoKey(self, key):
        entry = key._store[key._path]
        return (len(entry.get("subkeys", [])), len(entry.get("values", {})), 0)

    def EnumKey(self, key, i):
        name = key._store[key._path]["subkeys"][i]
        if name == _RAISE:
            raise OSError("enum failed")
        return name

    def EnumValue(self, key, i):
        values = key._store[key._path]["values"]
        name = list(values.keys())[i]
        if name == _RAISE:
            raise OSError("enum failed")
        return (name, values[name], self.REG_SZ)

    def SetValueEx(self, key, name, _reserved, _type, value):
        key._store[key._path]["values"][name] = value

    def DeleteValue(self, key, name):
        values = key._store[key._path]["values"]
        if name not in values:
            raise OSError("missing value")
        del values[name]


@pytest.fixture
def fake_winreg(monkeypatch):
    def _install(data=None):
        fake = _FakeWinreg(data or {})
        monkeypatch.setattr(registry, "winreg", fake)
        return fake

    return _install


# --- registry: list_subkeys ------------------------------------------------


def test_list_subkeys_returns_names(fake_winreg):
    fake_winreg({("HKLM", "Foo"): {"subkeys": ["A", "B", "C"], "values": {}}})
    assert registry.list_subkeys("HKLM", "Foo") == ["A", "B", "C"]


def test_list_subkeys_missing_key_returns_empty(fake_winreg):
    fake_winreg({})
    assert registry.list_subkeys("HKLM", "DoesNotExist") == []


def test_list_subkeys_skips_unreadable_entries(fake_winreg):
    fake_winreg({("HKCU", "Foo"): {"subkeys": ["A", _RAISE, "B"], "values": {}}})
    assert registry.list_subkeys("HKCU", "Foo") == ["A", "B"]


# --- registry: read_key_values ---------------------------------------------


def test_read_key_values_returns_mapping(fake_winreg):
    fake_winreg({("HKLM", "Foo"): {"subkeys": [], "values": {"x": "1", "y": 2}}})
    # non-string values are coerced to str
    assert registry.read_key_values("HKLM", "Foo") == {"x": "1", "y": "2"}


def test_read_key_values_missing_key_returns_empty(fake_winreg):
    fake_winreg({})
    assert registry.read_key_values("HKLM", "Nope") == {}


def test_read_key_values_skips_unreadable_entries(fake_winreg):
    fake_winreg({("HKCU", "Foo"): {"subkeys": [], "values": {"ok": "1", _RAISE: "x"}}})
    assert registry.read_key_values("HKCU", "Foo") == {"ok": "1"}


# --- registry: list_run_values ---------------------------------------------


def test_list_run_values_returns_pairs(fake_winreg):
    fake_winreg(
        {("HKCU", registry.RUN_SUBKEY): {"subkeys": [], "values": {"Spotify": "spotify.exe"}}}
    )
    assert registry.list_run_values("HKCU") == [("Spotify", "spotify.exe")]


def test_list_run_values_missing_key_returns_empty(fake_winreg):
    fake_winreg({})
    assert registry.list_run_values("HKLM") == []


def test_list_run_values_skips_unreadable_entries(fake_winreg):
    fake_winreg(
        {("HKLM", registry.RUN_SUBKEY): {"subkeys": [], "values": {"A": "a.exe", _RAISE: "x"}}}
    )
    assert registry.list_run_values("HKLM") == [("A", "a.exe")]


# --- registry: write/delete ------------------------------------------------


def test_write_run_value_creates_and_sets(fake_winreg):
    fake = fake_winreg({})
    registry.write_run_value("HKCU", registry.BACKUP_SUBKEY, "Foo", "foo.exe")
    assert fake.data[("HKCU", registry.BACKUP_SUBKEY)]["values"] == {"Foo": "foo.exe"}


def test_delete_run_value_removes_entry(fake_winreg):
    fake = fake_winreg(
        {("HKCU", registry.RUN_SUBKEY): {"subkeys": [], "values": {"Foo": "foo.exe"}}}
    )
    registry.delete_run_value("HKCU", registry.RUN_SUBKEY, "Foo")
    assert fake.data[("HKCU", registry.RUN_SUBKEY)]["values"] == {}


def test_delete_run_value_missing_raises(fake_winreg):
    fake_winreg({("HKCU", registry.RUN_SUBKEY): {"subkeys": [], "values": {}}})
    with pytest.raises(OSError):
        registry.delete_run_value("HKCU", registry.RUN_SUBKEY, "Ghost")


# --- winget ----------------------------------------------------------------


def _fake_run(monkeypatch, *, returncode=0, stdout="", stderr="", raise_exc=None):
    captured = {}

    def run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        if raise_exc is not None:
            raise raise_exc
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(winget.subprocess, "run", run)
    return captured


def test_winget_available_true(monkeypatch):
    _fake_run(monkeypatch, returncode=0)
    assert winget.available() is True


def test_winget_available_false_when_missing(monkeypatch):
    _fake_run(monkeypatch, raise_exc=FileNotFoundError())
    assert winget.available() is False


def test_winget_available_false_on_nonzero(monkeypatch):
    _fake_run(
        monkeypatch,
        raise_exc=subprocess.CalledProcessError(1, ["winget", "--version"]),
    )
    assert winget.available() is False


def test_winget_upgrade_list_returns_stdout(monkeypatch):
    captured = _fake_run(monkeypatch, stdout="Name  Id  Version\n")
    out = winget.upgrade_list()
    assert out == "Name  Id  Version\n"
    assert "--include-unknown" in captured["args"]


def test_winget_uninstall_returns_triple(monkeypatch):
    _fake_run(monkeypatch, returncode=0, stdout="done", stderr="")
    assert winget.uninstall("Some App") == (0, "done", "")


def test_winget_uninstall_coerces_none_output(monkeypatch):
    _fake_run(monkeypatch, returncode=1, stdout=None, stderr=None)
    assert winget.uninstall("Some App") == (1, "", "")


def test_winget_upgrade_single_id(monkeypatch):
    captured = _fake_run(monkeypatch, returncode=0)
    assert winget.upgrade("Mozilla.Firefox") == 0
    assert "--id" in captured["args"] and "Mozilla.Firefox" in captured["args"]
    assert "--all" not in captured["args"]


def test_winget_upgrade_all(monkeypatch):
    captured = _fake_run(monkeypatch, returncode=3)
    assert winget.upgrade() == 3
    assert "--all" in captured["args"]
