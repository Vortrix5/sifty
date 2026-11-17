"""Hyper-V / WSL2 virtual disk helpers (admin required).

VHDX files grow as data is written but never automatically shrink.
``compact_vhdx`` reclaims the unused space inside the VHD without
touching the contents - equivalent to running a defrag on a sparse file.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

__all__ = ["list_vhdx_files", "compact_vhdx"]

# Common roots where .vhdx files live.
_DEFAULT_SEARCH_ROOTS: list[Path] = [
    Path(os.environ.get("USERPROFILE", "C:\\Users\\Default")),
    Path("C:\\ProgramData\\Microsoft\\Windows\\Virtual Hard Disks"),
    Path("C:\\Users\\Public\\Documents\\Hyper-V"),
]


def list_vhdx_files(search_roots: list[Path] | None = None) -> list[tuple[Path, int]]:
    """Return (path, size_bytes) for every .vhdx / .vhd file found under the roots."""
    roots = search_roots or _DEFAULT_SEARCH_ROOTS
    results: list[tuple[Path, int]] = []
    for root in roots:
        if not root.exists():
            continue
        for dirpath, _dirs, files in os.walk(root, onerror=lambda _e: None):
            for name in files:
                if name.lower().endswith((".vhdx", ".vhd")):
                    fp = Path(dirpath) / name
                    try:
                        results.append((fp, fp.stat(follow_symlinks=False).st_size))
                    except OSError:
                        pass
    results.sort(key=lambda t: t[1], reverse=True)
    return results


def compact_vhdx(path: Path) -> tuple[bool, str]:
    """Compact a VHD/VHDX using DISM /Optimize-VHD.

    Non-destructive: reclaims white-space blocks inside the virtual disk
    without touching its filesystem contents.  Requires administrator rights.
    """
    try:
        result = subprocess.run(
            ["DISM", "/Online", f"/Optimize-VHD:{path}", "/Mode:Full"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        ok = result.returncode == 0
        lines = [line.strip() for line in (result.stdout + result.stderr).splitlines() if line.strip()]
        msg = lines[-1] if lines else ("ok" if ok else f"exit {result.returncode}")
        return ok, msg
    except FileNotFoundError:
        return False, "DISM not found"
    except subprocess.TimeoutExpired:
        return False, "Timed out after 300 s"
    except OSError as exc:
        return False, str(exc)
