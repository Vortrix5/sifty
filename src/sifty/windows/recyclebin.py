"""Recycle Bin primitives - trash (Send2Trash) and restore (winshell)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from send2trash import send2trash

logger = logging.getLogger("sifty.windows")


def send_to_trash(path: str | Path) -> None:
    """Send a path to the Recycle Bin."""
    send2trash(os.fspath(Path(path)))


def restore(original_path: str | Path) -> bool:
    """Restore a path from the Recycle Bin to its original location.

    Primary route is ``winshell.undelete`` (matches by original path, so it
    sidesteps verb localization). Falls back to the Shell.Application restore
    verb. Returns True on apparent success.
    """
    target = os.fspath(Path(original_path))
    try:
        import winshell

        winshell.undelete(target)
        return True
    except ImportError:
        logger.warning("winshell not installed; trying Shell.Application fallback")
    except Exception:
        logger.exception("winshell.undelete failed for %s", target)
    return _restore_via_shell(target)


def _restore_via_shell(target: str) -> bool:
    """Best-effort fallback: find the item in the bin and invoke its restore verb.

    Verb display names are localized, so we match a known set plus anything
    containing common restore stems.
    """
    try:
        import win32com.client  # type: ignore

        shell = win32com.client.Dispatch("Shell.Application")
        recycle = shell.Namespace(10)  # CSIDL_BITBUCKET
        want = os.path.normcase(os.path.abspath(target))
        wanted_name = os.path.basename(target)
        for item in list(recycle.Items()):
            # Column 0 is the display name; "Original Location" is column 1.
            name = item.Name
            location = recycle.GetDetailsOf(item, 1)
            full = os.path.normcase(os.path.abspath(os.path.join(location, name)))
            if full == want or name == wanted_name:
                for verb in item.Verbs():
                    label = verb.Name.lower().replace("&", "")
                    if any(stem in label for stem in ("restore", "wieder", "restaur", "ripristina", "restaurar")):
                        verb.DoIt()
                        return True
        return False
    except Exception:
        logger.exception("Shell.Application restore fallback failed for %s", target)
        return False
