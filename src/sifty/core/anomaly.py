"""Anomaly detection: flag notable changes on the machine over time.

Compares the current state against a small persisted baseline and reports things
worth a human's attention - a volume that lost a lot of free space, junk piling
up fast, or a new program that started launching at boot. Strictly read-only:
detection never deletes or changes anything, it only observes.

The baseline is a compact time series in its own SQLite file
(``%APPDATA%\\sifty\\snapshots.db``), kept separate from both the undo ledger
(``history.db``) and the AI store (``ai_memory.db``). Metadata only - free
bytes, junk totals, and startup entry *names*, never file contents.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..infra.config import app_data_dir, load_config
from .checkup import human_size

logger = logging.getLogger("sifty.core")

__all__ = ["Anomaly", "detect", "record_snapshot"]

_GB = 1 << 30
_KEEP_PER_SERIES = 60  # rows retained per (kind, key) time series

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,     -- "disk_free" | "junk_total"
    key TEXT NOT NULL,      -- volume mountpoint, or "total"
    value INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS startup_seen (
    name TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL
);
"""


@dataclass
class Anomaly:
    kind: str          # "disk_drop" | "junk_growth" | "new_startup"
    severity: str      # "info" | "attention"
    summary: str       # human one-liner
    action_key: str    # TUI nav key (mirrors checkup.Finding) so UIs can deep-link
    detail: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def db_path() -> Path:
    return app_data_dir() / "snapshots.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.executescript(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _latest_by_key(conn: sqlite3.Connection, kind: str) -> dict[str, int]:
    """The most recent stored value per key for a series."""
    rows = conn.execute(
        "SELECT key, value FROM snapshots WHERE kind = ? AND id IN "
        "(SELECT MAX(id) FROM snapshots WHERE kind = ? GROUP BY key)",
        (kind, kind),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------------------
# Current-state helpers (metadata only)
# ---------------------------------------------------------------------------

def _current_volumes(volumes=None):
    if volumes is not None:
        return volumes
    from . import disk
    return disk.volumes()


def _current_junk_total(junk_total=None) -> int:
    if junk_total is not None:
        return junk_total
    from . import junk
    return sum(c.size for c in junk.scan())


def _current_startup_names(startup_names=None) -> set[str]:
    if startup_names is not None:
        return set(startup_names)
    from . import startup
    return {e.name for e in startup.list_entries() if e.enabled}


# ---------------------------------------------------------------------------
# Detection (read-only)
# ---------------------------------------------------------------------------

def detect(*, config=None, volumes=None, junk_total=None, startup_names=None) -> list[Anomaly]:
    """Compare the current state to the stored baseline and return anomalies.

    Returns ``[]`` on the very first run (no baseline yet). Never raises - a
    failed detector is logged and skipped so a background run always completes.
    Optional current-state args let a caller avoid re-scanning; omit to compute.
    """
    config = config or load_config()
    section = config.section("anomaly")
    out: list[Anomaly] = []
    conn = _connect()
    try:
        for detector in (
            lambda: _detect_disk_drop(conn, section, volumes),
            lambda: _detect_junk_growth(conn, section, junk_total),
            lambda: _detect_new_startup(conn, section, startup_names),
        ):
            try:
                out.extend(detector())
            except Exception:
                logger.debug("anomaly detector failed", exc_info=True)
    finally:
        conn.close()
    return out


def _detect_disk_drop(conn, section, volumes) -> list[Anomaly]:
    threshold = int(section.get("disk_drop_gb", 10)) * _GB
    priors = _latest_by_key(conn, "disk_free")
    out: list[Anomaly] = []
    for v in _current_volumes(volumes):
        prior = priors.get(v.mountpoint)
        if prior is None:
            continue
        drop = prior - v.free
        if drop >= threshold:
            out.append(Anomaly(
                "disk_drop", "attention",
                f"{v.mountpoint} lost {human_size(drop)} of free space since the last "
                f"check ({human_size(v.free)} free now)",
                "disk", {"mountpoint": v.mountpoint, "drop": drop, "free": v.free},
            ))
    return out


def _detect_junk_growth(conn, section, junk_total) -> list[Anomaly]:
    threshold = int(section.get("junk_growth_gb", 5)) * _GB
    prior = _latest_by_key(conn, "junk_total").get("total")
    if prior is None:
        return []
    current = _current_junk_total(junk_total)
    growth = current - prior
    if growth >= threshold:
        return [Anomaly(
            "junk_growth", "info",
            f"Junk grew by {human_size(growth)} since the last check "
            f"({human_size(current)} reclaimable now)",
            "junk", {"growth": growth, "current": current},
        )]
    return []


def _detect_new_startup(conn, section, startup_names) -> list[Anomaly]:
    if not section.get("flag_new_startup", True):
        return []
    seen = {r[0] for r in conn.execute("SELECT name FROM startup_seen").fetchall()}
    if not seen:
        return []  # first run: nothing to compare; record_snapshot seeds the baseline
    new = sorted(_current_startup_names(startup_names) - seen)
    if not new:
        return []
    shown = ", ".join(new[:5]) + (" ..." if len(new) > 5 else "")
    return [Anomaly(
        "new_startup", "attention",
        f"{len(new)} new startup program(s) since the last check: {shown}",
        "startup", {"names": new},
    )]


# ---------------------------------------------------------------------------
# Baseline update
# ---------------------------------------------------------------------------

def record_snapshot(*, config=None, volumes=None, junk_total=None,
                    startup_names=None, now=None) -> None:
    """Append the current disk-free + junk-total + startup names to the baseline.

    Idempotent-safe to call every run; prunes each time series to the last
    :data:`_KEEP_PER_SERIES` rows so the file stays small.
    """
    config = config or load_config()
    ts = now or _now()
    conn = _connect()
    try:
        for v in _current_volumes(volumes):
            _append(conn, ts, "disk_free", v.mountpoint, int(v.free))
        _append(conn, ts, "junk_total", "total", int(_current_junk_total(junk_total)))
        for name in _current_startup_names(startup_names):
            conn.execute(
                "INSERT OR IGNORE INTO startup_seen (name, first_seen) VALUES (?, ?)",
                (name, ts),
            )
        conn.commit()
    finally:
        conn.close()


def _append(conn, ts: str, kind: str, key: str, value: int) -> None:
    conn.execute(
        "INSERT INTO snapshots (ts, kind, key, value) VALUES (?, ?, ?, ?)",
        (ts, kind, key, value),
    )
    conn.execute(
        "DELETE FROM snapshots WHERE kind = ? AND key = ? AND id NOT IN "
        "(SELECT id FROM snapshots WHERE kind = ? AND key = ? ORDER BY id DESC LIMIT ?)",
        (kind, key, kind, key, _KEEP_PER_SERIES),
    )
