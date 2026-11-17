"""Machine context snapshot for the AI advisor.

Builds a compact, *metadata-only* description of the current system state -
volumes, junk totals, recent history - that is injected into the AI system
prompt so it can give advice that is specific to *this* machine.

Rules that must never change:
- File *contents* are never included, only names, sizes, paths, and counts.
- Building the snapshot must degrade gracefully (any OS call can fail).
"""

from __future__ import annotations

from ..console import human_size


def build(*, include_junk: bool = True, include_volumes: bool = True,
          include_history: bool = True, include_preferences: bool = True,
          include_anomalies: bool = True) -> str:
    """Return a Markdown-formatted system context string for injection into prompts."""
    sections: list[str] = []

    if include_volumes:
        section = _volume_section()
        if section:
            sections.append(section)

    if include_junk:
        section = _junk_section()
        if section:
            sections.append(section)

    if include_anomalies:
        section = _anomaly_section()
        if section:
            sections.append(section)

    if include_history:
        section = _history_section()
        if section:
            sections.append(section)

    if include_preferences:
        section = _preferences_section()
        if section:
            sections.append(section)

    if not sections:
        return ""

    return "## Current machine context\n\n" + "\n\n".join(sections)


def _volume_section() -> str:
    try:
        from ..core import disk
        vols = disk.volumes()
    except Exception:
        return ""
    if not vols:
        return ""
    lines = ["**Disk volumes:**"]
    for v in vols:
        lines.append(
            f"- {v.mountpoint} ({v.fstype}): {human_size(v.free)} free / "
            f"{human_size(v.total)} total ({v.percent:.0f}% used)"
        )
    return "\n".join(lines)


def _junk_section() -> str:
    try:
        from ..core import junk
        cats = junk.scan()
    except Exception:
        return ""
    total_bytes = sum(c.size for c in cats)
    total_files = sum(c.file_count for c in cats)
    if not cats:
        return ""
    lines = [f"**Junk scan:** {total_files:,} files, {human_size(total_bytes)} reclaimable"]
    for c in cats:
        if c.size > 0:
            lines.append(f"- {c.category.label}: {human_size(c.size)} ({c.file_count:,} files)")
    return "\n".join(lines)


def _history_section() -> str:
    try:
        from ..core import history
        runs = history.recent_runs(5)
        summ = history.summary()
    except Exception:
        return ""
    if not runs:
        return ""
    lines = [
        f"**Cleanup history:** {summ['runs']} runs, "
        f"{human_size(summ['bytes_freed'])} reclaimed total"
    ]
    for r in runs[:3]:
        lines.append(f"- {r.ts[:10]}: {r.action} - {human_size(r.bytes_freed)} freed")
    return "\n".join(lines)


def _anomaly_section() -> str:
    """Recent notable changes (disk drops, junk growth, new startup entries)."""
    try:
        from ..core import anomaly
        found = anomaly.detect()
    except Exception:
        return ""
    if not found:
        return ""
    lines = ["**Recent changes noticed:**"]
    for a in found:
        lines.append(f"- {a.summary}")
    return "\n".join(lines)


def _preferences_section() -> str:
    """What this user routinely does / declines, so advice matches their habits."""
    try:
        from ..core.ai_memory import learned_preferences
        prefs = learned_preferences()
    except Exception:
        return ""
    if prefs.is_empty:
        return ""
    lines = ["**Learned preferences:**"]
    if prefs.always_clean:
        lines.append("- Routinely cleans: " + ", ".join(prefs.always_clean))
    skips = prefs.often_skipped or prefs.avoided_tools
    if skips:
        lines.append("- Tends to decline: " + ", ".join(skips))
    return "\n".join(lines)
