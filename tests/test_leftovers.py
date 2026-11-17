"""Tests for the post-uninstall leftover scanner (sandboxed roots)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sifty.core import leftovers, safety
from sifty.core.leftovers import Leftover, clean_leftovers, find_leftovers

_FAKE_CONFIG = SimpleNamespace(section=lambda name: {})


def _mkdir(root: Path, *parts: str) -> Path:
    path = root.joinpath(*parts)
    path.mkdir(parents=True)
    (path / "settings.json").write_bytes(b"x" * 100)
    return path


def test_finds_exact_and_squashed_name_matches(tmp_path):
    target = _mkdir(tmp_path, "SuperApp")
    squashed = _mkdir(tmp_path, "super-app")
    _mkdir(tmp_path, "OtherThing")
    found = find_leftovers("Super App", roots=[tmp_path], shortcut_roots=[])
    assert {f.path for f in found} == {target, squashed}
    assert all(f.size_bytes > 0 for f in found)


def test_strips_version_noise_from_app_name(tmp_path):
    target = _mkdir(tmp_path, "SuperApp")
    found = find_leftovers("Super App 2.4.1 (x64)", roots=[tmp_path], shortcut_roots=[])
    assert [f.path for f in found] == [target]


def test_publisher_two_level_layout(tmp_path):
    target = _mkdir(tmp_path, "AcmeSoft", "SuperApp")
    found = find_leftovers("Super App", publisher="AcmeSoft",
                           roots=[tmp_path], shortcut_roots=[])
    assert [f.path for f in found] == [target]


def test_never_matches_generic_vendor_names(tmp_path):
    _mkdir(tmp_path, "Microsoft")
    _mkdir(tmp_path, "Google")
    assert find_leftovers("Microsoft", roots=[tmp_path], shortcut_roots=[]) == []
    assert find_leftovers("Google", roots=[tmp_path], shortcut_roots=[]) == []


def test_short_names_never_match(tmp_path):
    _mkdir(tmp_path, "Git")
    assert find_leftovers("Git", roots=[tmp_path], shortcut_roots=[]) == []


def test_finds_start_menu_shortcuts(tmp_path):
    menu = tmp_path / "menu"
    menu.mkdir()
    lnk = menu / "Super App.lnk"
    lnk.write_bytes(b"shortcut")
    found = find_leftovers("Super App", roots=[], shortcut_roots=[menu])
    assert [f.path for f in found] == [lnk]
    assert found[0].kind == "shortcut"


def test_clean_leftovers_trashes_and_reports(tmp_path, monkeypatch):
    trashed = []
    monkeypatch.setattr(safety, "send_to_trash", lambda p: trashed.append(p))
    monkeypatch.setattr(safety, "audit", lambda msg: None)
    target = _mkdir(tmp_path, "SuperApp")
    items = [Leftover(target, 100, "data-dir")]

    dry = clean_leftovers(items, dry_run=True)
    assert dry.items == 1 and trashed == []

    applied = clean_leftovers(items, dry_run=False)
    assert applied.items == 1 and trashed == [target]


def test_clean_leftovers_refuses_system_trees(tmp_path, monkeypatch):
    monkeypatch.setattr(
        safety, "send_to_trash",
        lambda p: (_ for _ in ()).throw(AssertionError("must not trash system paths")),
    )
    sysroot = tmp_path / "Windows"
    (sysroot / "System32").mkdir(parents=True)
    monkeypatch.setenv("SystemRoot", str(sysroot))
    items = [Leftover(sysroot / "System32", 100, "data-dir")]
    result = clean_leftovers(items, dry_run=False)
    assert result.items == 0
    assert result.skipped and "refused" in result.skipped[0]


def test_normalize_handles_trademarks_and_separators():
    assert leftovers._normalize("Super-App™ v3.1 (x64)") == "super app"
    assert leftovers._normalize("EPSON_Scan 2") == "epson scan"


# --- default root discovery ------------------------------------------------


def test_default_roots_from_env(monkeypatch, tmp_path):
    local = tmp_path / "local"
    local.mkdir()
    (local / "Programs").mkdir()
    roaming = tmp_path / "roaming"
    roaming.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.delenv("PROGRAMDATA", raising=False)  # unset var is skipped

    roots = leftovers._default_roots()
    assert local in roots
    assert (local / "Programs") in roots
    assert roaming in roots


def test_shortcut_roots_from_env(monkeypatch, tmp_path):
    roaming = tmp_path / "roaming"
    menu = roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    menu.mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(roaming))
    # PROGRAMDATA is set but lacks the Start Menu structure → not added.
    progdata = tmp_path / "progdata"
    progdata.mkdir()
    monkeypatch.setenv("PROGRAMDATA", str(progdata))
    assert leftovers._shortcut_roots() == [menu]


def test_find_leftovers_uses_default_roots(monkeypatch, tmp_path):
    local = tmp_path / "local"
    _mkdir(local, "SuperApp")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("PROGRAMDATA", raising=False)
    found = find_leftovers("Super App")  # roots/shortcut_roots default
    assert any(f.path.name == "SuperApp" for f in found)


# --- error / skip branches -------------------------------------------------


class _BadRoot:
    def iterdir(self):
        raise OSError("permission denied")


def test_find_leftovers_skips_unreadable_data_root():
    assert find_leftovers("Super App", roots=[_BadRoot()], shortcut_roots=[]) == []


def test_find_leftovers_skips_unreadable_shortcut_root():
    assert find_leftovers("Super App", roots=[], shortcut_roots=[_BadRoot()]) == []


def test_find_leftovers_publisher_subdir_unreadable():
    class _Entry:
        name = "AcmeSoft"

        def is_dir(self):
            return True

        def iterdir(self):
            raise OSError("denied")

    class _Root:
        def iterdir(self):
            return iter([_Entry()])

    found = find_leftovers("Super App", publisher="AcmeSoft", roots=[_Root()], shortcut_roots=[])
    assert found == []


def test_publisher_layout_ignores_nonmatching_subdirs(tmp_path):
    _mkdir(tmp_path, "AcmeSoft", "SuperApp")
    _mkdir(tmp_path, "AcmeSoft", "OtherTool")
    found = find_leftovers("Super App", publisher="AcmeSoft", roots=[tmp_path], shortcut_roots=[])
    assert [f.path.name for f in found] == ["SuperApp"]


def test_shortcut_scan_ignores_nonmatching(tmp_path):
    menu = tmp_path / "menu"
    menu.mkdir()
    (menu / "Super App.lnk").write_bytes(b"x")
    (menu / "Unrelated.lnk").write_bytes(b"x")
    found = find_leftovers("Super App", roots=[], shortcut_roots=[menu])
    assert [f.path.name for f in found] == ["Super App.lnk"]


def test_find_leftovers_dedupes_repeated_roots(tmp_path):
    _mkdir(tmp_path, "SuperApp")
    found = find_leftovers("Super App", roots=[tmp_path, tmp_path], shortcut_roots=[])
    assert len([f for f in found if f.path.name == "SuperApp"]) == 1


def test_dir_size_skips_unreadable_files(monkeypatch, tmp_path):
    monkeypatch.setattr(leftovers.os, "walk", lambda p, onerror=None: [(str(tmp_path), [], ["ghost.bin"])])
    assert leftovers._dir_size(tmp_path) == 0


def test_file_size_missing_returns_zero(tmp_path):
    assert leftovers._file_size(tmp_path / "ghost") == 0


def test_clean_leftovers_skips_protected(monkeypatch, tmp_path):
    from sifty.core.safety import ProtectedPathError

    target = _mkdir(tmp_path, "SuperApp")
    monkeypatch.setattr(leftovers, "trash", lambda *a, **k: (_ for _ in ()).throw(ProtectedPathError("refused")))
    result = clean_leftovers([Leftover(target, 100, "data-dir")], dry_run=False, config=_FAKE_CONFIG)
    assert result.items == 0
    assert len(result.skipped) == 1


def test_clean_leftovers_skips_os_error(monkeypatch, tmp_path):
    target = _mkdir(tmp_path, "SuperApp")
    monkeypatch.setattr(leftovers, "trash", lambda *a, **k: (_ for _ in ()).throw(OSError("file in use")))
    result = clean_leftovers([Leftover(target, 100, "data-dir")], dry_run=False, config=_FAKE_CONFIG)
    assert result.items == 0
    assert "file in use" in result.skipped[0]
