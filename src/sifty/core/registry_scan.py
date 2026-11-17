"""Read-only scan for orphaned Windows uninstall registry entries.

Looks at the three standard Uninstall hive locations and reports entries
whose UninstallString references an executable that no longer exists on
disk.  MSI/RunDll/system-managed entries are skipped - only entries that
point at a missing standalone .exe are flagged.

This module is **strictly read-only**; no registry writes are performed.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from ..windows.registry import list_subkeys, read_key_values

__all__ = ["OrphanEntry", "find_orphan_uninstall_entries"]

# The three canonical Uninstall key locations on a 64-bit Windows system.
_UNINSTALL_HIVES: list[tuple[str, str]] = [
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKCU", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
]

# Uninstall strings that start with these tokens are system/MSI-managed.
# Their executables live in system32 or are virtual - never "missing".
_SKIP_PREFIXES = (
    "msiexec",
    "rundll32",
    "wusa.exe",
    "%systemroot%",
    "%windir%",
    "winget",          # winget-managed entries use "winget uninstall ..."
)


@dataclass
class OrphanEntry:
    hive: str
    key_path: str           # full subkey path (for reference only)
    display_name: str
    uninstall_string: str
    reason: str             # "missing executable" | "empty uninstall string"


def _extract_exe(uninstall_string: str) -> Path | None:
    """Parse an UninstallString and return the executable Path, or None."""
    s = uninstall_string.strip()
    if not s:
        return None
    try:
        # shlex handles both quoted ("C:\path\un.exe" /S) and unquoted forms.
        parts = shlex.split(s, posix=False)
        exe = parts[0].strip('"')
    except ValueError:
        exe = s.split()[0].strip('"')
    return Path(exe) if exe else None


def find_orphan_uninstall_entries() -> list[OrphanEntry]:
    """Scan all three Uninstall hive locations and return broken entries.

    An entry is considered an orphan if:
    - Its ``UninstallString`` is empty or absent, OR
    - The executable it references does not exist on disk.

    Results are deduplicated by display name (same app in 32-bit and
    64-bit hives is reported only once) and sorted alphabetically.
    """
    orphans: list[OrphanEntry] = []
    seen: set[str] = set()   # dedup by lowercased display name

    for hive, base_key in _UNINSTALL_HIVES:
        for subkey_name in list_subkeys(hive, base_key):
            full_key = f"{base_key}\\{subkey_name}"
            values = read_key_values(hive, full_key)

            display_name = values.get("DisplayName", "").strip()
            if not display_name:
                continue  # no display name → internal/component entry, skip

            # SystemComponent=1 means "hide from Add/Remove Programs" - internal
            # driver components (NVIDIA, etc.) use this; skip them.
            if values.get("SystemComponent", "0") == "1":
                continue

            # NoRemove=1 means the entry is intentionally not removable.
            if values.get("NoRemove", "0") == "1":
                continue

            dedup = display_name.lower()
            if dedup in seen:
                continue

            uninstall_str = values.get("UninstallString", "").strip()

            if not uninstall_str:
                seen.add(dedup)
                orphans.append(OrphanEntry(
                    hive=hive,
                    key_path=full_key,
                    display_name=display_name,
                    uninstall_string="",
                    reason="empty uninstall string",
                ))
                continue

            lower = uninstall_str.lower().lstrip('"')
            if any(lower.startswith(p) for p in _SKIP_PREFIXES):
                continue  # system-managed - can't be missing

            exe = _extract_exe(uninstall_str)
            if exe is None:
                continue

            if not exe.exists():
                seen.add(dedup)
                orphans.append(OrphanEntry(
                    hive=hive,
                    key_path=full_key,
                    display_name=display_name,
                    uninstall_string=uninstall_str,
                    reason="missing executable",
                ))

    return sorted(orphans, key=lambda e: e.display_name.lower())
