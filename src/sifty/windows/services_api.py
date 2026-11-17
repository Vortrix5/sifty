"""Windows service start-type query/change via pywin32 (win32service).

Numeric start types are locale-independent (unlike parsing ``sc`` output).
Changing a start type requires Administrator rights; failures return False.
"""

from __future__ import annotations

import logging

try:  # Windows-only; tests mock these functions.
    import win32service
except ImportError:  # pragma: no cover - non-Windows
    win32service = None  # type: ignore[assignment]

logger = logging.getLogger("sifty.windows")

ERROR_SERVICE_DOES_NOT_EXIST = 1060

# Service start-type codes → friendly names.
_START_TYPES = {2: "auto", 3: "manual", 4: "disabled", 0: "boot", 1: "system"}
_MODE_CODES = {"auto": 2, "manual": 3, "disabled": 4}


def get_start_type(name: str) -> str | None:
    """Return 'auto'/'manual'/'disabled'/… or None if absent/unreadable."""
    if win32service is None:  # pragma: no cover - non-Windows
        return None
    try:
        scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        try:
            svc = win32service.OpenService(scm, name, win32service.SERVICE_QUERY_CONFIG)
            try:
                config = win32service.QueryServiceConfig(svc)
                return _START_TYPES.get(config[1])
            finally:
                win32service.CloseServiceHandle(svc)
        finally:
            win32service.CloseServiceHandle(scm)
    except Exception:
        return None


def set_start_type(name: str, mode: str) -> bool:
    """Set a service's start type. Returns False if not admin / not found."""
    if win32service is None:  # pragma: no cover - non-Windows
        return False
    code = _MODE_CODES.get(mode)
    if code is None:
        return False
    try:
        scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
        try:
            svc = win32service.OpenService(scm, name, win32service.SERVICE_CHANGE_CONFIG)
            try:
                win32service.ChangeServiceConfig(
                    svc,
                    win32service.SERVICE_NO_CHANGE,  # service type
                    code,                            # start type
                    win32service.SERVICE_NO_CHANGE,  # error control
                    None, None, 0, None, None, None, None,
                )
                return True
            finally:
                win32service.CloseServiceHandle(svc)
        finally:
            win32service.CloseServiceHandle(scm)
    except Exception as exc:
        if getattr(exc, "winerror", None) == ERROR_SERVICE_DOES_NOT_EXIST:
            logger.debug("Service %s is not installed on this system", name)
        else:
            logger.exception("Failed to set start type for %s (admin required?)", name)
        return False
