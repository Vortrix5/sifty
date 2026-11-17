"""Full-suite health checkup: run every read-only scan and report findings.

``run_checkup`` fans the individual domain scans (junk, updates, registry
orphans, stale downloads, low disk space, startup bloat) out to a thread pool
and folds the results into a list of :class:`Finding`. It is strictly
read-only - every finding carries an ``action_key`` (a TUI navigation key /
CLI hint) pointing at the screen or command that can act on it, but the
checkup itself never deletes or changes anything.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

__all__ = ["Finding", "CHECKS", "run_checkup"]

# Severity levels, in increasing order of urgency.
OK = "ok"
INFO = "info"
ATTENTION = "attention"

_GB = 1 << 30
_MB = 1 << 20


def human_size(n: float) -> str:
    """Format a byte count (kept local so core stays frontend-free)."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


@dataclass
class Finding:
    domain: str       # short key: "junk", "updates", …
    label: str        # human title for the domain
    summary: str      # one-line result
    severity: str     # "ok" | "info" | "attention"
    action_key: str   # TUI nav key (and CLI hint) that can act on it; "" if none
    action_label: str = ""  # e.g. "Clean junk" - empty when severity is "ok"


def _check_junk() -> Finding:
    from . import junk

    total = sum(c.size for c in junk.scan())
    if total >= _GB:
        sev = ATTENTION
    elif total >= 50 * _MB:
        sev = INFO
    else:
        sev = OK
    summary = f"{human_size(total)} reclaimable" if total else "nothing to clean"
    action = "Clean junk" if sev != OK else ""
    return Finding("junk", "Junk files", summary, sev, "junk", action)


def _check_updates() -> Finding:
    from ..windows import winget
    from . import updates

    if not winget.available():
        return Finding("updates", "App updates", "winget unavailable", OK, "", "")
    ups = updates.list_upgrades()
    if not ups:
        return Finding("updates", "App updates", "everything up to date", OK, "", "")
    sev = ATTENTION if len(ups) >= 5 else INFO
    return Finding(
        "updates", "App updates", f"{len(ups)} update(s) available",
        sev, "updates", "Review updates",
    )


def _check_orphans() -> Finding:
    from . import registry_scan

    orphans = registry_scan.find_orphan_uninstall_entries()
    if not orphans:
        return Finding("orphans", "Registry orphans", "no broken uninstall entries", OK, "", "")
    return Finding(
        "orphans", "Registry orphans",
        f"{len(orphans)} broken uninstall entr{'y' if len(orphans) == 1 else 'ies'}",
        INFO, "apps", "Review orphans",
    )


def _check_stale() -> Finding:
    from . import cleanup

    stale = cleanup.find_stale_downloads()
    if not stale:
        return Finding("stale", "Stale downloads", "no old items in Downloads", OK, "", "")
    total = sum(s for _p, s, _m in stale)
    sev = ATTENTION if total >= _GB else INFO
    return Finding(
        "stale", "Stale downloads",
        f"{len(stale)} old item(s) · {human_size(total)}",
        sev, "cleanup", "Review downloads",
    )


def _check_disk() -> Finding:
    from . import watch

    low = watch.low_space()
    if not low:
        return Finding("disk", "Disk space", "all volumes have headroom", OK, "", "")
    vols = ", ".join(f"{v.mountpoint} ({human_size(v.free)} free)" for v in low)
    return Finding("disk", "Disk space", f"low space on {vols}", ATTENTION, "clean", "Free up space")


def _check_startup() -> Finding:
    from . import startup

    entries = startup.list_entries()
    enabled = sum(1 for e in entries if e.enabled)
    if enabled <= 8:
        return Finding("startup", "Startup programs", f"{enabled} enabled", OK, "", "")
    return Finding(
        "startup", "Startup programs",
        f"{enabled} programs start with Windows",
        INFO, "startup", "Review startup",
    )


# (domain key, check function) - order defines the report.
CHECKS = [
    ("junk", _check_junk),
    ("updates", _check_updates),
    ("orphans", _check_orphans),
    ("stale", _check_stale),
    ("disk", _check_disk),
    ("startup", _check_startup),
]


def _run_one(domain: str, label: str, fn) -> Finding:
    try:
        return fn()
    except Exception as exc:  # one failed probe must not sink the checkup
        return Finding(domain, label, f"check failed: {exc}", OK, "", "")


def run_checkup(only: set[str] | None = None) -> list[Finding]:
    """Run all (or ``only`` the given) checks concurrently; read-only."""
    labels = {"junk": "Junk files", "updates": "App updates", "orphans": "Registry orphans",
              "stale": "Stale downloads", "disk": "Disk space", "startup": "Startup programs"}
    checks = [(d, fn) for d, fn in CHECKS if not only or d in only]
    if not checks:
        return []
    with ThreadPoolExecutor(max_workers=min(len(checks), 6)) as pool:
        futures = [pool.submit(_run_one, d, labels.get(d, d), fn) for d, fn in checks]
        return [f.result() for f in futures]
