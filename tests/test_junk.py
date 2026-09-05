"""Tests for junk scanning and cleaning against a sandbox temp dir."""

from __future__ import annotations

from pathlib import Path

import pytest

from sifty.core import junk, safety
from sifty.infra.config import Config


@pytest.fixture
def sandbox_temp(monkeypatch, tmp_path):
    """Point %TEMP% at a sandbox and populate it with junk."""
    temp = tmp_path / "temp"
    temp.mkdir()
    (temp / "a.tmp").write_text("x" * 100)
    (temp / "b.log").write_text("y" * 200)
    sub = temp / "cache"
    sub.mkdir()
    (sub / "c.dat").write_text("z" * 300)

    monkeypatch.setenv("TEMP", str(temp))
    monkeypatch.setenv("TMP", str(temp))
    # Keep other categories out of the way by pointing them at empty dirs.
    monkeypatch.setenv("SystemRoot", str(tmp_path / "win"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    return temp


def test_scan_reports_user_temp_size(sandbox_temp):
    results = junk.scan(only={"user-temp"})
    user_temp = next(r for r in results if r.category.key == "user-temp")
    assert user_temp.size == 600  # 100 + 200 + 300
    assert user_temp.file_count == 3


def test_clean_dry_run_does_not_delete(monkeypatch, sandbox_temp):
    monkeypatch.setattr(safety, "send_to_trash", lambda p: pytest.fail("must not delete in dry-run"))
    result = junk.clean(only={"user-temp"}, dry_run=True)
    assert result.bytes_freed == 600
    assert result.items == 3  # three top-level entries: a.tmp, b.log, cache/
    assert result.trashed == []  # dry-run trashes nothing
    assert sandbox_temp.exists()


def test_clean_apply_trashes_entries(monkeypatch, sandbox_temp):
    trashed = []
    monkeypatch.setattr(safety, "send_to_trash", lambda p: trashed.append(p))
    monkeypatch.setattr(safety, "audit", lambda msg: None)
    result = junk.clean(only={"user-temp"}, dry_run=False)
    assert result.items == 3
    assert len(trashed) == 3
    assert len(result.trashed) == 3  # surfaced for the undo manifest
    assert not result.skipped


def test_browser_cache_covers_all_profiles_and_firefox(monkeypatch, tmp_path):
    local = tmp_path / "local"
    chrome = local / "Google" / "Chrome" / "User Data"
    for profile in ("Default", "Profile 1"):
        (chrome / profile / "Cache").mkdir(parents=True)
        (chrome / profile / "Code Cache").mkdir(parents=True)
    (local / "Mozilla" / "Firefox" / "Profiles" / "abc.dev-edition" / "cache2").mkdir(parents=True)
    # A non-profile dir that must NOT be swept (would hit cookies/bookmarks).
    (chrome / "System Profile" / "Cache").mkdir(parents=True)

    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("SystemRoot", str(tmp_path / "win"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    cats = {c.key: c for c in junk.junk_categories(Config())}
    roots = {str(r) for r in cats["browser-cache"].roots}
    assert str(chrome / "Default" / "Cache") in roots
    assert str(chrome / "Profile 1" / "Cache") in roots
    assert str(chrome / "Default" / "Code Cache") in roots
    assert any("cache2" in r for r in roots)               # Firefox covered
    assert str(chrome / "System Profile" / "Cache") not in roots


def test_crash_dump_categories_present(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("SystemRoot", str(tmp_path / "win"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "progdata"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    cats = {c.key: c for c in junk.junk_categories(Config())}
    assert "crash-dumps" in cats and not cats["crash-dumps"].requires_admin
    assert "system-crash-reports" in cats and cats["system-crash-reports"].requires_admin
    crash_roots = {r.name for r in cats["crash-dumps"].roots}
    assert {"CrashDumps", "ReportQueue", "ReportArchive"} <= crash_roots
    sys_roots = {r.name for r in cats["system-crash-reports"].roots}
    assert "Minidump" in sys_roots


def test_downloads_installers_gated_by_config(monkeypatch, tmp_path):
    cfg_off = Config()
    keys_off = {c.key for c in junk.junk_categories(cfg_off)}
    assert "downloads-installers" not in keys_off

    cfg_on = Config(data={**Config().data})
    cfg_on.data["junk"] = {"include_downloads_installers": True}
    keys_on = {c.key for c in junk.junk_categories(cfg_on)}
    assert "downloads-installers" in keys_on


def test_discord_cache_category_covers_flavors_and_safeguards_session(monkeypatch, tmp_path):
    appdata = tmp_path / "appdata"

    for flavor in ("discord", "discordptb", "discordcanary"):
        flavor_dir = appdata / flavor
        (flavor_dir / "Cache").mkdir(parents=True)
        (flavor_dir / "Code Cache").mkdir(parents=True)
        (flavor_dir / "GPUCache").mkdir(parents=True)
        (flavor_dir / "Local Storage").mkdir(parents=True)

    original_env_path = junk._env_path
    monkeypatch.setattr(junk, "_env_path", lambda var: appdata if var == "APPDATA" else original_env_path(var))

    monkeypatch.setenv("TEMP", str(tmp_path / "temp"))
    monkeypatch.setenv("TMP", str(tmp_path / "temp"))
    monkeypatch.setenv("SystemRoot", str(tmp_path / "win"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    cats = {c.key: c for c in junk.junk_categories(Config())}

    # Verification 1: Category exists
    assert "discord-cache" in cats

    roots = {str(r) for r in cats["discord-cache"].roots}

    # Verification 2: Specific caches are included
    assert str(appdata / "discord" / "Cache") in roots
    assert str(appdata / "discordptb" / "Code Cache") in roots
    assert str(appdata / "discordcanary" / "GPUCache") in roots

    # Verification 3: Crucial session paths are ignored
    assert not any("Local Storage" in r for r in roots)



    
    def test_spotify_cache_category_covers_cache_dirs_and_safeguards_session(monkeypatch, tmp_path):
    local = tmp_path / "local"
    spotify = local / "Spotify"

    (spotify / "Storage").mkdir(parents=True)
    (spotify / "Data").mkdir(parents=True)
    (spotify / "Local Storage").mkdir(parents=True)

    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("SystemRoot", str(tmp_path / "win"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    cats = {c.key: c for c in junk.junk_categories(Config())}

    # Verification 1: Category exists
    assert "spotify-cache" in cats

    roots = {str(r) for r in cats["spotify-cache"].roots}

    # Verification 2: Spotify cache directories are included
    assert str(spotify / "Storage") in roots
    assert str(spotify / "Data") in roots

    # Verification 3: Login/session data is ignored
    assert str(spotify / "Local Storage") not in roots


def test_installer_app_hint_strips_suffixes():
    """Verify that installer filenames are properly cleaned into app hints."""
    assert junk._installer_app_hint(Path("Discord-Setup-1.2.3.exe")) == "discord setup"
    assert junk._installer_app_hint(Path("vscode_install_x64.msi")) == "vscode install"
    assert junk._installer_app_hint(Path("Python-3.11.exe")) == "python"
    assert junk._installer_app_hint(Path("BraveBrowser.exe")) == "bravebrowser"


def test_is_installed_app_installer_matches_substrings():
    """Verify substring matching in both directions with a frozenset of installed apps."""
    installed_apps = frozenset({"discord app", "vlc media player", "python"})

    assert junk._is_installed_app_installer(Path("Discord.exe"), installed_apps) is True

    assert junk._is_installed_app_installer(Path("Python-Setup.exe"), installed_apps) is True

    assert junk._is_installed_app_installer(Path("chrome_installer.exe"), installed_apps) is False


def test_is_installed_app_installer_under_3_chars_guard():
    """Verify that hints under 3 characters are rejected immediately."""
    installed_apps = frozenset({"go", "golang", "google chrome"})
    assert junk._is_installed_app_installer(Path("go.exe"), installed_apps) is False
