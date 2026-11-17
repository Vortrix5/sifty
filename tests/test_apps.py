"""Tests for core.apps: installed-app + startup enumeration and uninstall.

`winreg` is faked with a small registry tree so the registry walks run on any OS.
"""

from __future__ import annotations

import pytest

from sifty.core import apps

_RAISE = "__RAISE__"


class _FakeKey:
    def __init__(self, node):
        self.node = node

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeWinreg:
    HKEY_LOCAL_MACHINE = "HKLM"
    HKEY_CURRENT_USER = "HKCU"

    def __init__(self, tree):
        # tree: {"HKLM": {subpath: node}, "HKCU": {...}}
        # node: {"subkeys": {name: node}, "values": {name: value}}
        self.tree = tree

    def OpenKey(self, base, name, *_args):
        if isinstance(base, _FakeKey):
            children = base.node.get("subkeys", {})
            if name not in children:
                raise OSError("missing subkey")
            return _FakeKey(children[name])
        hive = self.tree.get(base, {})
        if name not in hive:
            raise OSError("missing key")
        return _FakeKey(hive[name])

    def QueryInfoKey(self, key):
        return (
            len(key.node.get("subkeys", {})),
            len(key.node.get("values", {})),
            0,
        )

    def EnumKey(self, key, i):
        name = list(key.node.get("subkeys", {}).keys())[i]
        if name == _RAISE:
            raise OSError("enum failed")
        return name

    def EnumValue(self, key, i):
        values = key.node.get("values", {})
        name = list(values.keys())[i]
        if name == _RAISE:
            raise OSError("enum failed")
        return (name, values[name], 1)

    def QueryValueEx(self, key, name):
        values = key.node.get("values", {})
        if name not in values:
            raise OSError("missing value")
        return (values[name], 1)


def _app_node(**values):
    return {"subkeys": {}, "values": values}


@pytest.fixture
def fake_winreg(monkeypatch):
    def _install(tree):
        monkeypatch.setattr(apps, "winreg", _FakeWinreg(tree))

    return _install


# --- installed_apps --------------------------------------------------------


def test_installed_apps_reads_and_sorts(fake_winreg):
    uninstall = apps._UNINSTALL_KEYS[0][1]
    fake_winreg(
        {
            "HKLM": {
                uninstall: {
                    "subkeys": {
                        "Zeta": _app_node(
                            DisplayName="Zeta App",
                            DisplayVersion="2.0",
                            Publisher="Z Corp",
                            EstimatedSize=2048,
                            UninstallString="zeta.exe /u",
                        ),
                        "Alpha": _app_node(
                            DisplayName="Alpha App",
                            DisplayVersion="1.0",
                            Publisher="A Corp",
                            EstimatedSize=1024,
                        ),
                    },
                    "values": {},
                }
            }
        }
    )
    result = apps.installed_apps()
    assert [a.name for a in result] == ["Alpha App", "Zeta App"]
    zeta = result[1]
    assert zeta.size_bytes == 2048 * 1024
    assert zeta.publisher == "Z Corp"
    assert zeta.uninstall_string == "zeta.exe /u"
    assert zeta.source == "HKLM"


def test_installed_apps_filters_system_components_and_nameless(fake_winreg):
    uninstall = apps._UNINSTALL_KEYS[0][1]
    fake_winreg(
        {
            "HKLM": {
                uninstall: {
                    "subkeys": {
                        "Real": _app_node(DisplayName="Real App"),
                        "Sys": _app_node(DisplayName="Hidden", SystemComponent=1),
                        "NoName": _app_node(DisplayVersion="9.9"),
                    },
                    "values": {},
                }
            }
        }
    )
    result = apps.installed_apps()
    assert [a.name for a in result] == ["Real App"]
    assert result[0].size_bytes == 0  # no EstimatedSize → 0


def test_installed_apps_dedupes_across_hives(fake_winreg):
    hklm_path = apps._UNINSTALL_KEYS[0][1]
    hkcu_path = apps._UNINSTALL_KEYS[2][1]
    fake_winreg(
        {
            "HKLM": {
                hklm_path: {
                    "subkeys": {"App": _app_node(DisplayName="Shared App", Publisher="Old")},
                    "values": {},
                }
            },
            "HKCU": {
                hkcu_path: {
                    "subkeys": {"App": _app_node(DisplayName="Shared App", Publisher="New")},
                    "values": {},
                }
            },
        }
    )
    result = apps.installed_apps()
    assert len(result) == 1
    assert result[0].publisher == "New"  # HKCU processed last, wins
    assert result[0].source == "HKCU"


def test_installed_apps_skips_unreadable_subkey(fake_winreg):
    uninstall = apps._UNINSTALL_KEYS[0][1]
    fake_winreg(
        {
            "HKLM": {
                uninstall: {
                    "subkeys": {
                        "Good": _app_node(DisplayName="Good App"),
                        _RAISE: _app_node(DisplayName="Boom"),
                    },
                    "values": {},
                }
            }
        }
    )
    result = apps.installed_apps()
    assert [a.name for a in result] == ["Good App"]


def test_installed_apps_empty_when_no_keys(fake_winreg):
    fake_winreg({})
    assert apps.installed_apps() == []


# --- startup_entries -------------------------------------------------------


def test_startup_entries_reads_run_keys(fake_winreg, monkeypatch, tmp_path):
    monkeypatch.delenv("APPDATA", raising=False)
    run_path = apps._RUN_KEYS[0][1]
    fake_winreg(
        {
            "HKCU": {
                run_path: {"subkeys": {}, "values": {"Spotify": "spotify.exe"}},
            }
        }
    )
    entries = apps.startup_entries()
    assert len(entries) == 1
    assert entries[0].name == "Spotify"
    assert entries[0].command == "spotify.exe"
    assert entries[0].location == "HKCU Run"


def test_startup_entries_skips_unreadable_run_value(fake_winreg, monkeypatch):
    monkeypatch.delenv("APPDATA", raising=False)
    run_path = apps._RUN_KEYS[0][1]
    fake_winreg(
        {
            "HKCU": {
                run_path: {"subkeys": {}, "values": {"Good": "good.exe", _RAISE: "x"}},
            }
        }
    )
    entries = apps.startup_entries()
    assert [e.name for e in entries] == ["Good"]


def test_startup_entries_includes_startup_folder(fake_winreg, monkeypatch, tmp_path):
    fake_winreg({})  # no run keys
    monkeypatch.setenv("APPDATA", str(tmp_path))
    folder = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    folder.mkdir(parents=True)
    (folder / "Launcher.lnk").write_text("shortcut")
    (folder / "subdir").mkdir()  # directories are ignored

    entries = apps.startup_entries()
    assert len(entries) == 1
    assert entries[0].name == "Launcher"
    assert entries[0].location == "Startup folder"


def test_startup_entries_missing_folder_is_empty(fake_winreg, monkeypatch, tmp_path):
    fake_winreg({})
    monkeypatch.setenv("APPDATA", str(tmp_path))  # folder never created
    assert apps.startup_entries() == []


def test_startup_entries_without_winreg_reads_folder_only(monkeypatch, tmp_path):
    monkeypatch.setattr(apps, "winreg", None)  # non-Windows / winreg unavailable
    monkeypatch.setenv("APPDATA", str(tmp_path))
    folder = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    folder.mkdir(parents=True)
    (folder / "OneDrive.lnk").write_text("shortcut")

    entries = apps.startup_entries()
    assert [e.name for e in entries] == ["OneDrive"]


# --- uninstall_app ---------------------------------------------------------


def test_uninstall_app_winget_unavailable(monkeypatch):
    monkeypatch.setattr(apps.winget, "available", lambda: False)
    ok, msg = apps.uninstall_app("Some App")
    assert ok is False
    assert "winget is not available" in msg


def test_uninstall_app_success(monkeypatch):
    monkeypatch.setattr(apps.winget, "available", lambda: True)
    monkeypatch.setattr(apps.winget, "uninstall", lambda name: (0, "done", ""))
    ok, msg = apps.uninstall_app("Some App")
    assert ok is True
    assert "Uninstalled 'Some App'." == msg


def test_uninstall_app_failure_uses_stderr(monkeypatch):
    monkeypatch.setattr(apps.winget, "available", lambda: True)
    monkeypatch.setattr(apps.winget, "uninstall", lambda name: (1, "", "no package found"))
    ok, msg = apps.uninstall_app("Some App")
    assert ok is False
    assert "exit 1" in msg and "no package found" in msg


def test_uninstall_app_failure_falls_back_to_stdout(monkeypatch):
    monkeypatch.setattr(apps.winget, "available", lambda: True)
    monkeypatch.setattr(apps.winget, "uninstall", lambda name: (2, "some stdout detail", ""))
    ok, msg = apps.uninstall_app("Some App")
    assert ok is False
    assert "some stdout detail" in msg
