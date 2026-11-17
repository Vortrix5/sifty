"""Tests for the startup manager (registry primitives + folder moves mocked)."""

from __future__ import annotations

from sifty.core import startup
from sifty.core.models import StartupEntry
from sifty.windows import registry


class FakeRegistry:
    """In-memory stand-in for the Run-key registry primitives."""

    def __init__(self):
        # {(hive, subkey): {name: value}}
        self.store: dict[tuple[str, str], dict[str, str]] = {}

    def list_run_values(self, hive, subkey=registry.RUN_SUBKEY):
        return list(self.store.get((hive, subkey), {}).items())

    def write_run_value(self, hive, subkey, name, value):
        self.store.setdefault((hive, subkey), {})[name] = value

    def delete_run_value(self, hive, subkey, name):
        self.store.get((hive, subkey), {}).pop(name, None)


def _install_fake(monkeypatch, tmp_path):
    fake = FakeRegistry()
    monkeypatch.setattr(registry, "list_run_values", fake.list_run_values)
    monkeypatch.setattr(registry, "write_run_value", fake.write_run_value)
    monkeypatch.setattr(registry, "delete_run_value", fake.delete_run_value)
    # Keep folder scanning out of the way (no real Startup folder in tests).
    monkeypatch.setattr(startup, "_startup_folder", lambda: None)
    monkeypatch.setenv("APPDATA", str(tmp_path))  # _disabled_folder under tmp
    return fake


def test_list_entries_marks_enabled(monkeypatch, tmp_path):
    fake = _install_fake(monkeypatch, tmp_path)
    fake.store[("HKCU", registry.RUN_SUBKEY)] = {"Spotify": "C:\\spotify.exe"}
    entries = [e for e in startup.list_entries() if e.kind == "hkcu-run"]
    assert len(entries) == 1
    assert entries[0].name == "Spotify" and entries[0].enabled is True


def test_disable_then_enable_round_trip(monkeypatch, tmp_path):
    fake = _install_fake(monkeypatch, tmp_path)
    fake.store[("HKCU", registry.RUN_SUBKEY)] = {"Spotify": "C:\\spotify.exe"}

    entry = next(e for e in startup.list_entries() if e.name == "Spotify")
    assert startup.disable(entry) is True
    # Now it's gone from Run and present (disabled) in the backup.
    names = {(e.name, e.enabled) for e in startup.list_entries()}
    assert ("Spotify", False) in names
    assert ("Spotify", True) not in names

    disabled = next(e for e in startup.list_entries() if e.name == "Spotify")
    assert startup.enable(disabled) is True
    names = {(e.name, e.enabled) for e in startup.list_entries()}
    assert ("Spotify", True) in names
    assert ("Spotify", False) not in names


def test_set_enabled_by_name(monkeypatch, tmp_path):
    fake = _install_fake(monkeypatch, tmp_path)
    fake.store[("HKCU", registry.RUN_SUBKEY)] = {"Teams": "C:\\teams.exe"}
    assert startup.set_enabled("Teams", False) is True
    assert startup.set_enabled("Teams", False) is False  # already disabled → no-op
    assert startup.set_enabled("Teams", True) is True


# --- _startup_folder -------------------------------------------------------


def test_startup_folder_none_without_appdata(monkeypatch):
    monkeypatch.delenv("APPDATA", raising=False)
    assert startup._startup_folder() is None


def test_startup_folder_path_from_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    expected = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    assert startup._startup_folder() == expected


# --- list_entries folder scanning ------------------------------------------


def test_list_entries_includes_startup_folders(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(registry, "list_run_values", lambda hive, subkey=registry.RUN_SUBKEY: [])
    sf = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    sf.mkdir(parents=True)
    (sf / "Live.lnk").write_text("x")
    (sf / "desktop.ini").write_text("x")  # ignored
    (startup._disabled_folder() / "Off.lnk").write_text("x")
    (startup._disabled_folder() / "subdir").mkdir()  # non-file → ignored

    by = {(e.name, e.enabled, e.kind) for e in startup.list_entries()}
    assert ("Live", True, "folder") in by
    assert ("Off", False, "folder") in by
    assert not any(e.name == "desktop" for e in startup.list_entries())


# --- disable/enable folder + error paths -----------------------------------


def test_disable_folder_entry(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    sf = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    sf.mkdir(parents=True)
    lnk = sf / "App.lnk"
    lnk.write_text("x")
    entry = StartupEntry("App", str(lnk), "Startup folder", enabled=True, kind="folder")
    assert startup.disable(entry) is True
    assert not lnk.exists()
    assert (startup._disabled_folder() / "App.lnk").exists()


def test_enable_folder_entry(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    sf = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    sf.mkdir(parents=True)
    src = startup._disabled_folder() / "App.lnk"
    src.write_text("x")
    entry = StartupEntry("App", str(src), "Startup folder (disabled)", enabled=False, kind="folder")
    assert startup.enable(entry) is True
    assert (sf / "App.lnk").exists()


def test_enable_folder_no_startup_folder(monkeypatch):
    monkeypatch.delenv("APPDATA", raising=False)
    entry = StartupEntry("App", "C:\\x\\App.lnk", "loc", enabled=False, kind="folder")
    assert startup.enable(entry) is False


def test_disable_folder_os_error(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(
        startup.shutil, "move", lambda s, d: (_ for _ in ()).throw(OSError("move failed"))
    )
    entry = StartupEntry("App", str(tmp_path / "ghost.lnk"), "loc", enabled=True, kind="folder")
    assert startup.disable(entry) is False


def test_disable_run_os_error(monkeypatch, tmp_path):
    _install_fake(monkeypatch, tmp_path)
    monkeypatch.setattr(
        registry, "write_run_value", lambda *a: (_ for _ in ()).throw(OSError("admin required"))
    )
    entry = StartupEntry("Spotify", "C:\\s.exe", "HKCU Run", enabled=True, kind="hkcu-run")
    assert startup.disable(entry) is False


def test_enable_run_os_error(monkeypatch, tmp_path):
    _install_fake(monkeypatch, tmp_path)
    monkeypatch.setattr(
        registry, "write_run_value", lambda *a: (_ for _ in ()).throw(OSError("denied"))
    )
    entry = StartupEntry("Spotify", "C:\\s.exe", "HKCU Run (disabled)", enabled=False, kind="hkcu-run")
    assert startup.enable(entry) is False


def test_disable_unknown_kind_returns_false():
    entry = StartupEntry("X", "cmd", "loc", enabled=True, kind="mystery")
    assert startup.disable(entry) is False


def test_enable_unknown_kind_returns_false():
    entry = StartupEntry("X", "cmd", "loc", enabled=False, kind="mystery")
    assert startup.enable(entry) is False
