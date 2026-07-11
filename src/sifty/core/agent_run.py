"""The proactive maintenance agent (background run).

`run_agent` is what a scheduled `sifty agent run` executes: a read-only checkup
plus anomaly detection, then EITHER a toast summarizing what it found (the
default) OR, only when explicitly opted in, an unattended auto-clean of a
narrow set of low-risk junk.

Safety model - this is the one place Sifty may delete with no human present, so
the rails are deliberately strict:

- The auto-fix set is a **hardcoded allowlist** of per-user cache/temp
  categories (:data:`_AUTOFIX_ALLOWLIST`). Config can only *narrow* it.
- Every admin-only / system junk category is excluded, always. The background
  task runs unelevated and must never silently touch system files.
- The LLM is never involved in choosing what to delete here; this path calls
  `junk.clean()` directly with the allowlist. The AI's role in the background is
  summarization only (that lives in the TUI, not here).
- Deletion still routes through `core.safety.trash()` (Recycle Bin, protected
  paths refused, audited) and is recorded in `history` so `sifty undo` works.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..windows import notify
from . import anomaly, checkup, disk, history, junk
from .checkup import ATTENTION, INFO, human_size

logger = logging.getLogger("sifty.agent")

__all__ = ["AgentRunResult", "AutoFixOutcome", "run_agent"]

_TITLE = "Sifty maintenance"

# The ONLY junk categories an unattended run may ever clean: per-user,
# self-rebuilding cache/temp/dump dirs, none of which require admin. This set is
# the source of truth; config can subset it but not extend it.
_AUTOFIX_ALLOWLIST = frozenset({
    "user-temp", "thumbnail-cache", "browser-cache",
    "winget-cache", "onedrive-logs", "discord-cache", "crash-dumps",
})

_DEFAULT_MIN_BYTES = 524288000  # 500 MB


@dataclass
class AutoFixOutcome:
    category_keys: list[str]
    items: int
    bytes_freed: int
    skipped: list[str]
    run_id: int | None  # history row id, so the clean is undoable


@dataclass
class AgentRunResult:
    findings: list[checkup.Finding]
    anomalies: list[anomaly.Anomaly]
    auto_fixed: AutoFixOutcome | None
    notified: bool
    headline: str
    elevated_skipped: list[str] = field(default_factory=list)


def eligible_autofix_keys(config) -> set[str]:
    """Junk keys the unattended run may clean: (config subset) of the hardcoded
    allowlist, minus any category that requires admin. Never wider than the
    allowlist. Independent of whether auto-fix is actually enabled."""
    configured = set(config.section("agent").get("auto_fix_categories", []))
    candidate = (configured & _AUTOFIX_ALLOWLIST) if configured else set(_AUTOFIX_ALLOWLIST)
    admin_keys = {c.key for c in junk.junk_categories(config) if c.requires_admin}
    return candidate - admin_keys


def run_agent(*, apply: bool = False, config=None, notify_fn=None, now=None) -> AgentRunResult:
    """Run a proactive checkup; auto-fix approved low-risk junk if ``apply``,
    else notify only. ``notify_fn`` is injected for testing."""
    from ..infra.config import load_config

    config = config or load_config()
    notify_fn = notify_fn or notify.toast

    findings = checkup.run_checkup()

    # Current state, computed once and shared with detection + baseline update.
    vols = disk.volumes()
    cats = junk.scan(config)
    junk_total = sum(c.size for c in cats)

    anomalies = anomaly.detect(config=config, volumes=vols, junk_total=junk_total)
    anomaly.record_snapshot(config=config, volumes=vols, junk_total=junk_total, now=now)

    # Admin-only junk we can see but won't touch unattended (informational).
    elevated_skipped = sorted(
        c.category.key for c in cats if c.category.requires_admin and c.size > 0
    )

    auto_fixed = None
    if apply:
        auto_fixed = _auto_fix(config, cats)

    headline = _headline(findings, anomalies, auto_fixed)
    notified = bool(notify_fn(_TITLE, headline)) if headline else False
    return AgentRunResult(findings, anomalies, auto_fixed, notified, headline, elevated_skipped)


def _auto_fix(config, cats) -> AutoFixOutcome | None:
    keys = eligible_autofix_keys(config)
    if not keys:
        return None
    min_bytes = int(config.section("agent").get("auto_fix_min_bytes", _DEFAULT_MIN_BYTES))
    eligible_total = sum(c.size for c in cats if c.category.key in keys)
    if eligible_total < min_bytes:
        return None
    result = junk.clean(config, only=set(keys), dry_run=False)
    if result.items == 0:
        return None
    run_id = history.record_clean(
        "agent", "auto-fix:" + ",".join(sorted(keys)),
        result.bytes_freed, result.items, result.trashed,
    )
    logger.info("agent auto-fix: %d items, %d bytes, run %s",
                result.items, result.bytes_freed, run_id)
    return AutoFixOutcome(sorted(keys), result.items, result.bytes_freed, result.skipped, run_id)


def _headline(findings, anomalies, auto_fixed) -> str:
    """Short toast text. Empty string means 'all clear' - don't nag."""
    if auto_fixed:
        return (f"Cleaned {human_size(auto_fixed.bytes_freed)} of junk "
                f"({auto_fixed.items} items) to the Recycle Bin.")

    urgent = [f"{f.label}: {f.summary}" for f in findings if f.severity == ATTENTION]
    urgent += [a.summary for a in anomalies if a.severity == "attention"]
    if urgent:
        return "Sifty found: " + "; ".join(urgent[:3]) + ". Open Sifty to review."

    minor = [f"{f.label}: {f.summary}" for f in findings if f.severity == INFO]
    minor += [a.summary for a in anomalies]
    if minor:
        return "Sifty noticed: " + "; ".join(minor[:2]) + "."
    return ""
