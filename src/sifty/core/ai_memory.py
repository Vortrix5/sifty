"""Local AI memory: conversation persistence + learned preferences.

Backed by SQLite at ``%APPDATA%\\sifty\\ai_memory.db``, kept deliberately
separate from the run/undo ledger (``history.db``) so the safety-critical audit
trail stays clean. Two things live here:

- the recent chat transcript, so the agent has continuity across restarts;
- a log of tool *skips*, from which :func:`learned_preferences` derives a
  lightweight profile (which junk the user routinely cleans, which tools they
  tend to decline) that :mod:`sifty.ai.context` folds into the machine snapshot.

Metadata only - never file contents. Every reader degrades gracefully: a
missing or locked DB must never break the chat.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..infra.config import app_data_dir

# Junk-clean history rows use ``detail`` as a comma-joined list of category keys,
# but a few write sites use sentinels instead of real keys - ignore those.
_JUNK_DETAIL_SENTINELS = {"all", "checkup"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skip_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    tool TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT ''
);
"""


def db_path() -> Path:
    return app_data_dir() / "ai_memory.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.executescript(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Conversation transcript
# ---------------------------------------------------------------------------

def append_message(role: str, content: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO messages (ts, role, content) VALUES (?, ?, ?)",
            (_now(), role, content),
        )
        conn.commit()
    finally:
        conn.close()


def recent_messages(limit: int = 20) -> list[dict]:
    """The most recent ``limit`` messages, oldest-first (ready for the LLM)."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{"role": r[0], "content": r[1]} for r in reversed(rows)]
    finally:
        conn.close()


def clear_messages() -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM messages")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Skip events + learned preferences
# ---------------------------------------------------------------------------

def record_skip(tool: str, target: str = "") -> None:
    """Record that the user declined to run ``tool`` (optionally on ``target``)."""
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO skip_events (ts, tool, target) VALUES (?, ?, ?)",
            (_now(), tool, target),
        )
        conn.commit()
    finally:
        conn.close()


@dataclass
class Preferences:
    """A lightweight, derived profile of what the user does and doesn't do."""

    always_clean: list[str] = field(default_factory=list)
    often_skipped: list[str] = field(default_factory=list)
    avoided_tools: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.always_clean or self.often_skipped or self.avoided_tools)


def learned_preferences(min_count: int = 2) -> Preferences:
    """Derive preferences from cleanup history + recorded skips.

    - ``always_clean`` - junk category keys cleaned in >= ``min_count`` runs.
    - ``avoided_tools`` - tools declined >= ``min_count`` times.
    - ``often_skipped`` - ``"tool:target"`` pairs declined >= ``min_count`` times.

    Degrades to an empty :class:`Preferences` on any error.
    """
    try:
        return Preferences(
            always_clean=_always_clean(min_count),
            avoided_tools=_avoided_tools(min_count),
            often_skipped=_often_skipped(min_count),
        )
    except Exception:
        return Preferences()


def _always_clean(min_count: int) -> list[str]:
    # Derived from history.db (the junk-clean ledger), not this DB. If the
    # junk-clean ``detail`` format ever changes, see the record_clean() call
    # sites in cli/commands/junk.py and tui/views/junk.py.
    from . import history

    counter: Counter[str] = Counter()
    for run in history.recent_runs(100):
        if run.action != "junk":
            continue
        for key in run.detail.split(","):
            key = key.strip()
            if key and key not in _JUNK_DETAIL_SENTINELS:
                counter[key] += 1
    return sorted(k for k, n in counter.items() if n >= min_count)


def _avoided_tools(min_count: int) -> list[str]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT tool FROM skip_events GROUP BY tool HAVING COUNT(*) >= ?",
            (min_count,),
        ).fetchall()
        return sorted(r[0] for r in rows)
    finally:
        conn.close()


def _often_skipped(min_count: int) -> list[str]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT tool, target FROM skip_events WHERE target != '' "
            "GROUP BY tool, target HAVING COUNT(*) >= ?",
            (min_count,),
        ).fetchall()
        return sorted(f"{r[0]}:{r[1]}" for r in rows)
    finally:
        conn.close()
