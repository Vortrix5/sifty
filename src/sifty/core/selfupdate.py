"""Self-update: compare the running version against PyPI and upgrade via pipx."""

from __future__ import annotations

import json
import logging
import subprocess
from importlib.metadata import PackageNotFoundError, distribution
from importlib.metadata import version as pkg_version
from urllib.parse import urlparse
from urllib.request import url2pathname

logger = logging.getLogger("sifty.core")

__all__ = [
    "current_version",
    "latest_version",
    "check_update",
    "apply_update",
    "is_editable_install",
    "editable_install_path",
]

_PACKAGE = "sifty"
_PYPI_URL = "https://pypi.org/pypi/sifty/json"


def _parse(v: str) -> tuple[int, ...]:
    """Parse a semver-ish string into a comparable tuple, ignoring pre-release tags."""
    parts = []
    for segment in v.split(".")[:3]:
        digits = "".join(c for c in segment if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def current_version() -> str:
    try:
        return pkg_version(_PACKAGE)
    except PackageNotFoundError:
        return "0.0.0"


def editable_install_path() -> str | None:
    """Local source path if Sifty is an editable/dev install (pip/pipx -e), else None.

    Editable installs record PEP 610 ``dir_info.editable = true`` in their
    ``direct_url.json`` metadata; a normal PyPI install has no such marker. On a
    dev checkout the recorded metadata version is frozen at install time, so the
    PyPI-vs-installed comparison is meaningless and ``pipx upgrade`` only re-runs
    the editable install (``pip install -e <path>``) instead of fetching a
    release - which is exactly the failure self-update would otherwise hit.
    """
    try:
        raw = distribution(_PACKAGE).read_text("direct_url.json")
    except PackageNotFoundError:
        return None
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except ValueError:
        return None
    if not info.get("dir_info", {}).get("editable", False):
        return None
    url = info.get("url", "")
    parsed = urlparse(url)
    if parsed.scheme == "file":
        try:
            return url2pathname(parsed.path)
        except Exception:
            return url or None
    return url or "<source>"


def is_editable_install() -> bool:
    """True if Sifty is running from an editable/dev install. See editable_install_path()."""
    return editable_install_path() is not None


def latest_version() -> str | None:
    """Fetch the latest published version from PyPI. Returns None on any error."""
    try:
        import httpx
        resp = httpx.get(_PYPI_URL, timeout=5.0, follow_redirects=True)
        if resp.status_code == 200:
            return resp.json().get("info", {}).get("version")
        logger.debug("selfupdate: PyPI returned HTTP %s", resp.status_code)
    except Exception as exc:
        # Offline / PyPI down is normal - log for `sifty logs`, don't crash.
        logger.debug("selfupdate: version check failed: %s", exc)
    return None


def check_update() -> tuple[str, str | None]:
    """Return (current, latest_if_newer). latest is None if already up-to-date or check failed."""
    current = current_version()
    latest = latest_version()
    if latest and _parse(latest) > _parse(current):
        return current, latest
    return current, None


def apply_update() -> tuple[bool, str]:
    """Run `pipx upgrade sifty`. Returns (success, message)."""
    if is_editable_install():
        return False, "Sifty is an editable dev install - update it with git, not pipx."
    try:
        result = subprocess.run(
            ["pipx", "upgrade", _PACKAGE],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        ok = result.returncode == 0
        msg = (result.stdout or result.stderr or "").strip().splitlines()
        summary = msg[-1] if msg else ("Upgraded successfully." if ok else "Upgrade failed.")
        return ok, summary
    except FileNotFoundError:
        return False, "pipx not found - is Sifty installed via pipx?"
    except subprocess.TimeoutExpired:
        return False, "Upgrade timed out after 120 s"
    except OSError as exc:
        return False, str(exc)
