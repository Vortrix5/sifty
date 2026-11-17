"""Smart cleanup (engine): duplicates, large files, and stale downloads.

Read functions find candidates; ``trash_paths`` removes a chosen set through the
safety layer (Recycle Bin, protected-path denylist) and returns a
:class:`CleanResult` so the caller can record history / enable undo.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from ..infra.config import load_config
from . import disk
from .models import CleanResult
from .safety import ProtectedPathError, trash

__all__ = [
    "find_large_files",
    "find_stale_downloads",
    "choose_duplicate_deletions",
    "trash_paths",
]

DEFAULT_LARGE_MIN = 100 * 1024 * 1024  # 100 MB
DEFAULT_STALE_DAYS = 180
DEFAULT_RECENT_DAYS = 7


def find_large_files(
    path: Path,
    min_size: int = DEFAULT_LARGE_MIN,
    top: int = 50,
    recent_days: int = DEFAULT_RECENT_DAYS,
) -> list[tuple[Path, int]]:
    """Return the largest files (>= ``min_size``) under ``path``, biggest first.

    Files modified within ``recent_days`` are excluded so actively-used files
    never appear pre-selected for deletion. Pass ``recent_days=0`` to disable.
    """
    cutoff = time.time() - recent_days * 86400 if recent_days > 0 else None
    results: list[tuple[Path, int]] = []
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            fp = Path(root) / name
            try:
                st = fp.stat(follow_symlinks=False)
            except OSError:
                continue
            if st.st_size < min_size:
                continue
            if cutoff is not None and st.st_mtime >= cutoff:
                continue  # recently modified - skip
            results.append((fp, st.st_size))
    results.sort(key=lambda t: t[1], reverse=True)
    return results[:top]


def find_stale_downloads(days: int = DEFAULT_STALE_DAYS, downloads: Path | None = None) -> list[tuple[Path, int, float]]:
    """Top-level items in Downloads not modified in ``days`` days."""
    downloads = downloads or (Path.home() / "Downloads")
    if not downloads.exists():
        return []
    cutoff = time.time() - days * 86400
    out: list[tuple[Path, int, float]] = []
    try:
        entries = list(downloads.iterdir())
    except OSError:
        return []
    for entry in entries:
        try:
            st = entry.stat()
        except OSError:
            continue
        if st.st_mtime < cutoff:
            size = st.st_size if entry.is_file() else disk._entry_size(entry)
            out.append((entry, size, st.st_mtime))
    out.sort(key=lambda t: t[1], reverse=True)
    return out


def choose_duplicate_deletions(
    groups: dict[str, list[Path]],
    recent_days: int = DEFAULT_RECENT_DAYS,
) -> list[Path]:
    """Keep one file per duplicate group, return the others (to delete).

    Keeps the shortest path (heuristic for the "original"); the rest are
    redundant copies. Files modified within ``recent_days`` are never suggested
    for deletion. Pass ``recent_days=0`` to disable.
    """
    cutoff = time.time() - recent_days * 86400 if recent_days > 0 else None
    to_delete: list[Path] = []
    for paths in groups.values():
        ordered = sorted(paths, key=lambda p: (len(str(p)), str(p)))
        for p in ordered[1:]:
            if cutoff is not None:
                try:
                    if p.stat(follow_symlinks=False).st_mtime >= cutoff:
                        continue  # recently modified - skip
                except OSError:
                    pass
            to_delete.append(p)
    return to_delete


def trash_paths(
    paths,
    *,
    dry_run: bool = True,
    config=None,
    extra_protected: list[str] | None = None,
) -> CleanResult:
    """Send a chosen set of paths to the Recycle Bin via the safety layer.

    ``extra_protected`` extends the built-in denylist for this call only.
    """
    config = config or load_config()
    cfg_protected = config.section("safety").get("extra_protected_paths", [])
    extra_protected = list(cfg_protected) + list(extra_protected or [])
    bytes_freed = 0
    items = 0
    skipped: list[str] = []
    trashed: list[Path] = []

    for raw in paths:
        p = Path(raw)
        try:
            size = p.stat(follow_symlinks=False).st_size if p.is_file() else disk._entry_size(p)
            trash(p, extra_protected=extra_protected, dry_run=dry_run)
            bytes_freed += size
            items += 1
            if not dry_run:
                trashed.append(p)
        except ProtectedPathError as exc:
            skipped.append(str(exc))
        except OSError as exc:
            skipped.append(f"{p}: {exc}")

    return CleanResult(bytes_freed, items, skipped, trashed)
