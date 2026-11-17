"""Tests for the registry orphan scanner (winreg fully mocked)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sifty.core import registry_scan
from sifty.core.registry_scan import find_orphan_uninstall_entries


def _make_registry(apps: dict[str, dict[str, str]]):
    """Build mock return values for list_subkeys + read_key_values.

    ``apps`` maps subkey_name → {DisplayName, UninstallString, ...}.
    The same apps dict is used for all three hive/key combinations.
    """
    subkeys = list(apps.keys())

    def mock_list_subkeys(hive, key):
        # Only return values for the first hive to avoid triplicate results.
        if hive == "HKLM" and "WOW6432Node" not in key:
            return subkeys
        return []

    def mock_read_values(hive, key):
        # key ends with \{subkey_name}
        subkey_name = key.split("\\")[-1]
        return apps.get(subkey_name, {})

    return mock_list_subkeys, mock_read_values


def test_detects_missing_executable(tmp_path):
    missing_exe = str(tmp_path / "uninstall.exe")   # does not exist
    ls, rv = _make_registry({
        "App1": {"DisplayName": "My App", "UninstallString": f'"{missing_exe}" /S'},
    })
    with patch.object(registry_scan, "list_subkeys", ls), \
         patch.object(registry_scan, "read_key_values", rv):
        results = find_orphan_uninstall_entries()

    assert len(results) == 1
    assert results[0].display_name == "My App"
    assert results[0].reason == "missing executable"


def test_skips_present_executable(tmp_path):
    real_exe = tmp_path / "uninstall.exe"
    real_exe.write_bytes(b"")
    ls, rv = _make_registry({
        "App1": {"DisplayName": "Good App", "UninstallString": str(real_exe)},
    })
    with patch.object(registry_scan, "list_subkeys", ls), \
         patch.object(registry_scan, "read_key_values", rv):
        results = find_orphan_uninstall_entries()

    assert results == []


def test_detects_empty_uninstall_string():
    ls, rv = _make_registry({
        "App1": {"DisplayName": "Broken App", "UninstallString": ""},
    })
    with patch.object(registry_scan, "list_subkeys", ls), \
         patch.object(registry_scan, "read_key_values", rv):
        results = find_orphan_uninstall_entries()

    assert len(results) == 1
    assert results[0].reason == "empty uninstall string"


def test_skips_msi_entries():
    ls, rv = _make_registry({
        "App1": {
            "DisplayName": "MSI App",
            "UninstallString": "MsiExec.exe /X{12345678-DEAD-BEEF-0000-123456789ABC}",
        },
    })
    with patch.object(registry_scan, "list_subkeys", ls), \
         patch.object(registry_scan, "read_key_values", rv):
        results = find_orphan_uninstall_entries()

    assert results == []


def test_skips_entries_without_display_name():
    ls, rv = _make_registry({
        "App1": {"UninstallString": "C:\\missing.exe"},  # no DisplayName
    })
    with patch.object(registry_scan, "list_subkeys", ls), \
         patch.object(registry_scan, "read_key_values", rv):
        results = find_orphan_uninstall_entries()

    assert results == []


def test_deduplicates_across_hives(tmp_path):
    missing_exe = str(tmp_path / "un.exe")

    def ls(hive, key):  # return same app in both HKLM and HKCU
        if "WOW6432Node" not in key:
            return ["App1"]
        return []

    def rv(hive, key):
        return {"DisplayName": "Shared App", "UninstallString": missing_exe}

    with patch.object(registry_scan, "list_subkeys", ls), \
         patch.object(registry_scan, "read_key_values", rv):
        results = find_orphan_uninstall_entries()

    # Same display name in multiple hives → only one entry
    assert len(results) == 1


def test_results_sorted_alphabetically(tmp_path):
    ls, rv = _make_registry({
        "Z": {"DisplayName": "Zebra App", "UninstallString": str(tmp_path / "z.exe")},
        "A": {"DisplayName": "Alpha App", "UninstallString": str(tmp_path / "a.exe")},
    })
    with patch.object(registry_scan, "list_subkeys", ls), \
         patch.object(registry_scan, "read_key_values", rv):
        results = find_orphan_uninstall_entries()

    names = [r.display_name for r in results]
    assert names == sorted(names, key=str.lower)


# --- _extract_exe ----------------------------------------------------------


def test_extract_exe_empty_returns_none():
    assert registry_scan._extract_exe("   ") is None


def test_extract_exe_unterminated_quote_falls_back():
    # An unbalanced quote makes shlex raise ValueError → split() fallback.
    assert registry_scan._extract_exe('"C:\\un.exe /S') == Path("C:\\un.exe")


def test_extract_exe_quotes_only_is_none():
    assert registry_scan._extract_exe('""') is None


# --- skip branches ---------------------------------------------------------


def test_skips_system_components():
    ls, rv = _make_registry({
        "App1": {
            "DisplayName": "GPU Driver",
            "UninstallString": "C:\\missing.exe",
            "SystemComponent": "1",
        },
    })
    with patch.object(registry_scan, "list_subkeys", ls), \
         patch.object(registry_scan, "read_key_values", rv):
        assert find_orphan_uninstall_entries() == []


def test_skips_no_remove_entries():
    ls, rv = _make_registry({
        "App1": {
            "DisplayName": "Permanent App",
            "UninstallString": "C:\\missing.exe",
            "NoRemove": "1",
        },
    })
    with patch.object(registry_scan, "list_subkeys", ls), \
         patch.object(registry_scan, "read_key_values", rv):
        assert find_orphan_uninstall_entries() == []


def test_skips_entry_with_empty_exe_token():
    # UninstallString is non-empty but resolves to no executable.
    ls, rv = _make_registry({
        "App1": {"DisplayName": "Weird App", "UninstallString": '""'},
    })
    with patch.object(registry_scan, "list_subkeys", ls), \
         patch.object(registry_scan, "read_key_values", rv):
        assert find_orphan_uninstall_entries() == []
