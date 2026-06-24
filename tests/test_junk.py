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


# ── installer-name matching helpers ────────────────────────────────────────


class TestInstallerAppHint:
    """Tests for ``_installer_app_hint()`` suffix stripping and normalisation."""

    def test_strips_common_setup_suffixes(self):
        # _INSTALLER_SUFFIXES strips the trailing version first (1.2.3),
        # leaving "Discord-Setup".  The "Setup" suffix is also in the regex
        # but the regex applies once from the end, so it strips "-1.2.3"
        # and leaves "discord setup" after normalisation.
        hint = junk._installer_app_hint(Path("Discord-Setup-1.2.3.exe"))
        assert hint == "discord setup"

    def test_strips_installer_suffix(self):
        hint = junk._installer_app_hint(Path("VSCode-installer.exe"))
        assert hint == "vscode"

    def test_strips_install_suffix(self):
        hint = junk._installer_app_hint(Path("Firefox Installer.exe"))
        assert hint == "firefox"
        hint = junk._installer_app_hint(Path("Firefox-Install.exe"))
        assert hint == "firefox"

    def test_strips_arch_suffixes(self):
        hint = junk._installer_app_hint(Path("MyApp_x64.exe"))
        assert hint == "myapp"
        hint = junk._installer_app_hint(Path("MyApp_amd64.exe"))
        assert hint == "myapp"

    def test_strips_version_suffix(self):
        hint = junk._installer_app_hint(Path("App-2.1.0.exe"))
        assert hint == "app"

    def test_lowercases_output(self):
        hint = junk._installer_app_hint(Path("DIScord.EXE"))
        assert hint == "discord"

    def test_replaces_hyphens_and_underscores_with_space(self):
        # "_windows" is a recognised suffix and gets stripped first.
        # After stripping and normalisation: "visual studio code"
        hint = junk._installer_app_hint(Path("Visual-Studio-Code_windows.exe"))
        assert hint == "visual studio code"


class TestIsInstalledAppInstaller:
    """Tests for ``_is_installed_app_installer()`` matching logic."""

    @pytest.mark.parametrize("filename,installed,expected", [
        # happy path — exact and substring matches
        ("Discord-Setup-1.2.3.exe", frozenset({"discord"}), True),
        # substring match
        ("vscode-installer.exe", frozenset({"vscode", "discord"}), True),
        ("Firefox Setup 120.0.exe", frozenset({"firefox"}), True),
        # short hint guard (< 3 chars)
        ("ab-setup.exe", frozenset({"ab"}), False),
        ("x_64.exe", frozenset({"x"}), False),
        # no match
        ("unknown-app-setup.exe", frozenset({"discord", "vscode"}), False),
        ("random.exe", frozenset({"discord"}), False),
    ])
    def test_is_installed_app_installer(self, filename, installed, expected):
        path = Path(filename)
        assert junk._is_installed_app_installer(path, installed) == expected

    def test_bidirectional_substring_match(self):
        """Both 'hint in name' and 'name in hint' directions are checked."""
        # hint "discord" is found inside "discordptb" → match
        assert junk._is_installed_app_installer(
            Path("Discord-Setup.exe"), frozenset({"discordptb"})
        ) == True
        # "discordptb" contains "discord" → match (other direction)
        assert junk._is_installed_app_installer(
            Path("Discord-PTB-Setup.exe"), frozenset({"discord"})
        ) == True

    def test_short_hint_after_suffix_stripping_returns_false(self):
        """After stripping everything, a too-short hint is rejected."""
        # "a" after stripping version → length 1, rejected
        hint = junk._installer_app_hint(Path("a-1.0.exe"))
        assert len(hint) < 3
        assert junk._is_installed_app_installer(
            Path("a-1.0.exe"), frozenset({"a"})
        ) == False

    def test_empty_installed_names_returns_false(self):
        assert junk._is_installed_app_installer(
            Path("Firefox-Setup.exe"), frozenset()
        ) == False
