"""Disk and volume analysis (engine): usage, biggest items, duplicates."""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil

from .models import VolumeUsage

__all__ = ["VolumeUsage", "volumes", "biggest", "find_duplicates"]

_MAX_HASH_WORKERS = min(8, os.cpu_count() or 1)


def volumes() -> list[VolumeUsage]:
    """Return usage for every fixed volume."""
    results: list[VolumeUsage] = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        results.append(
            VolumeUsage(part.device, part.mountpoint, part.fstype, usage.total, usage.used, usage.free)
        )
    return results


def _entry_size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat(follow_symlinks=False).st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            try:
                total += (Path(root) / name).stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return total


def biggest(path: Path, top: int = 15) -> list[tuple[Path, int]]:
    """Return the largest immediate children of ``path`` by total size."""
    items: list[tuple[Path, int]] = []
    try:
        for entry in path.iterdir():
            items.append((entry, _entry_size(entry)))
    except OSError:
        return []
    items.sort(key=lambda t: t[1], reverse=True)
    return items[:top]


def find_duplicates(
    path: Path,
    min_size: int = 1,
    count_hardlinks_once: bool = True,
) -> dict[str, list[Path]]:
    """Find duplicate files under ``path`` by size, then content hash.

    With ``count_hardlinks_once=True`` (default) NTFS hardlinks that share
    the same inode are represented by a single path so they are never
    reported as wasted space - they occupy the same disk block.
    Hashing of size-matched candidates is parallelised with a thread pool.
    """
    by_size: dict[int, list[Path]] = defaultdict(list)
    seen_inodes: set[tuple[int, int]] = set()

    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            fp = Path(root) / name
            try:
                st = fp.stat(follow_symlinks=False)
            except OSError:
                continue
            if st.st_size < min_size:
                continue
            if count_hardlinks_once:
                inode_key = (st.st_dev, st.st_ino)
                if inode_key in seen_inodes:
                    continue  # hardlink to an already-seen file - skip
                seen_inodes.add(inode_key)
            by_size[st.st_size].append(fp)

    # Collect all files from size groups that could be duplicates.
    candidates = [fp for paths in by_size.values() if len(paths) >= 2 for fp in paths]
    if not candidates:
        return {}

    # Hash candidates in parallel - I/O-bound, threads are the right tool.
    with ThreadPoolExecutor(max_workers=_MAX_HASH_WORKERS) as pool:
        hashes = list(pool.map(_hash_file, candidates))

    dupes: dict[str, list[Path]] = defaultdict(list)
    for fp, digest in zip(candidates, hashes, strict=True):
        if digest:
            dupes[digest].append(fp)
    return {d: ps for d, ps in dupes.items() if len(ps) > 1}


def _hash_file(path: Path, chunk: int = 1 << 20) -> str | None:
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            while block := fh.read(chunk):
                h.update(block)
    except OSError:
        return None
    return h.hexdigest()
