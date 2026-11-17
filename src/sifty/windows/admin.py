"""Windows privilege detection and UAC self-elevation.

Windows can't elevate a *running* process, so "becoming admin" means relaunching
the same command through the ``runas`` verb, which triggers the UAC prompt and
starts a new, elevated process (in its own window). The caller then exits.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys

SW_SHOWNORMAL = 1


def is_admin() -> bool:
    """Return True if the current process has Administrator rights."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


def can_elevate() -> bool:
    """True if elevation is possible (Windows, and not already elevated)."""
    return sys.platform == "win32" and not is_admin()


def _relaunch_target() -> tuple[str, str]:
    """Return (executable, params) that re-run the current invocation directly.

    Normal installs relaunch via ``python -m sifty <args>`` (works for the venv
    and the pipx/editable global alike). A PyInstaller-frozen build relaunches
    its own exe with the original arguments.
    """
    args = subprocess.list2cmdline(sys.argv[1:])
    if getattr(sys, "frozen", False):
        return sys.executable, args
    return sys.executable, f"-m sifty {args}".strip()


def _windows_terminal() -> str | None:
    """Path to Windows Terminal (wt.exe) if available, else None."""
    found = shutil.which("wt")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        alias = os.path.join(local, "Microsoft", "WindowsApps", "wt.exe")
        if os.path.exists(alias):
            return alias
    return None


def _shell_execute(executable: str, params: str) -> int:
    try:
        return int(
            ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
                None, "runas", executable, params, None, SW_SHOWNORMAL
            )
        )
    except Exception:
        return 0


def relaunch_as_admin() -> bool:
    """Relaunch the current command elevated via UAC.

    Elevated processes get a fresh console, and Windows hands them the *legacy*
    console host - which would lose the Windows Terminal styling. So when
    Windows Terminal is present we relaunch *through* ``wt.exe`` to keep the same
    look; otherwise we relaunch the command directly.

    Returns True if an elevated process was launched - the caller should then
    exit. Returns False on non-Windows, when already admin, or when the user
    dismisses the UAC prompt.
    """
    if not can_elevate():
        return False

    executable, params = _relaunch_target()
    wt = _windows_terminal()

    # ShellExecuteW returns a value > 32 on success.
    if wt:
        # wt.exe runs the given command line in a new (elevated) terminal window.
        wt_params = f"{subprocess.list2cmdline([executable])} {params}".strip()
        if _shell_execute(wt, wt_params) > 32:
            return True
        # Fall through to a direct relaunch if launching via wt failed.

    return _shell_execute(executable, params) > 32
