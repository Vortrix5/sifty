"""Curated Windows services manager (engine).

Only a vetted allowlist of well-known *optional* services can be toggled; a
critical-services denylist is an extra guard, and everything else stays
read-only. Changing a start type needs Administrator rights.
"""

from __future__ import annotations

from ..windows import services_api
from .models import ServiceInfo

__all__ = ["ALLOWLIST", "list_services", "can_manage", "is_present",
           "set_start_type"]

# Well-known optional services that are commonly safe to disable on a personal
# PC. (name, label, description - the description notes any trade-off.)
ALLOWLIST: list[dict[str, str]] = [
    {"name": "DiagTrack", "label": "Connected User Experiences and Telemetry",
     "description": "Windows diagnostic/telemetry data collection."},
    {"name": "dmwappushservice", "label": "Device Management WAP Push",
     "description": "WAP push routing (telemetry-related)."},
    {"name": "XblGameSave", "label": "Xbox Live Game Save",
     "description": "Xbox Live game-save sync - only needed for Xbox games."},
    {"name": "XboxNetApiSvc", "label": "Xbox Live Networking",
     "description": "Xbox Live networking - only needed for Xbox."},
    {"name": "XboxGipSvc", "label": "Xbox Accessory Management",
     "description": "Xbox controller/accessory management."},
    {"name": "MapsBroker", "label": "Downloaded Maps Manager",
     "description": "Offline maps - only needed if you use the Maps app."},
    {"name": "RetailDemo", "label": "Retail Demo Service",
     "description": "Store demo mode - unused on personal PCs."},
    {"name": "Fax", "label": "Fax",
     "description": "Fax service - rarely used."},
    {"name": "SysMain", "label": "SysMain (Superfetch)",
     "description": "Prefetch/Superfetch - can cause high disk on older HDDs."},
]

# Never touch these, even if somehow requested.
CRITICAL_DENYLIST = {
    "RpcSs", "DcomLaunch", "Power", "BrokerInfrastructure", "LSM", "Schedule",
    "EventLog", "ProfSvc", "Dhcp", "Dnscache", "nsi", "Winmgmt", "gpsvc",
    "BFE", "mpssvc", "CryptSvc", "Themes", "PlugPlay",
}

_ALLOWED_NAMES = {s["name"] for s in ALLOWLIST}


def list_services() -> list[ServiceInfo]:
    """Status of each curated service (start type + whether it exists)."""
    out: list[ServiceInfo] = []
    for spec in ALLOWLIST:
        start_type = services_api.get_start_type(spec["name"])
        out.append(ServiceInfo(
            name=spec["name"], label=spec["label"], description=spec["description"],
            start_type=start_type or "absent", present=start_type is not None,
        ))
    return out


def can_manage(name: str) -> bool:
    return name in _ALLOWED_NAMES and name not in CRITICAL_DENYLIST


def is_present(name: str) -> bool:
    """True if the service is actually installed on this machine."""
    return services_api.get_start_type(name) is not None


def set_start_type(name: str, mode: str) -> bool:
    """Set a curated service's start type. Refuses anything off the allowlist."""
    if not can_manage(name):
        return False
    if mode not in {"auto", "manual", "disabled"}:
        return False
    return services_api.set_start_type(name, mode)
