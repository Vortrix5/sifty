"""winget primitives - shell out to Microsoft's package manager.

Centralised here so both ``core.apps`` and ``core.updates`` share one
implementation (and one place to handle UTF-8 decoding of winget's output).
"""

from __future__ import annotations

import subprocess


def available() -> bool:
    """True if winget is present on this system."""
    try:
        subprocess.run(["winget", "--version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def upgrade_list() -> str:
    """Return the stdout of ``winget upgrade`` (the available-updates table)."""
    result = subprocess.run(
        ["winget", "upgrade", "--include-unknown", "--accept-source-agreements"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return result.stdout


def uninstall(name: str) -> tuple[int, str, str]:
    """Uninstall by display name. Returns (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["winget", "uninstall", "--name", name, "--silent",
         "--accept-source-agreements"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return result.returncode, result.stdout or "", result.stderr or ""


def upgrade(upgrade_id: str | None = None) -> int:
    """Apply updates (a single id, or all). Returns the exit code."""
    cmd = ["winget", "upgrade", "--silent",
           "--accept-source-agreements", "--accept-package-agreements"]
    cmd += ["--id", upgrade_id] if upgrade_id else ["--all"]
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").returncode
