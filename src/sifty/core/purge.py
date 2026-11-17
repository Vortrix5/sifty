"""Dev-artifact purge engine.

Walks a project tree looking for well-known build/cache directories (node_modules,
dist, __pycache__, target, etc.) and sends them to the Recycle Bin via the safety
layer.  The scan stops descending into a matched directory so nested artefacts are
treated as one unit (e.g. nested node_modules inside node_modules).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from ..infra.config import load_config
from .models import CleanResult
from .safety import ProtectedPathError, is_protected, trash

__all__ = ["ARTIFACT_DIRS", "ArtifactScan", "scan_artifacts", "purge_artifacts"]

# Directories that are always safe to delete and rebuild.
ARTIFACT_DIRS: frozenset[str] = frozenset({
    # JavaScript / Node
    "node_modules", ".next", ".nuxt", ".svelte-kit", ".parcel-cache",
    # Generic build output
    "dist", "build", ".build", "out", ".output",
    # Java / Kotlin / Gradle / Maven
    "target", ".gradle",
    # Rust
    # (target already covered above)
    # Python
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".nox", "htmlcov", "coverage",
    # .NET
    "bin", "obj",
    # General caches
    ".cache",
    # Dart / Flutter
    ".dart_tool",
    # Go
    "vendor",
})


@dataclass
class ArtifactScan:
    path: Path
    size_bytes: int
    pattern: str   # which ARTIFACT_DIRS key matched


# Never descend into these: they hold installed packages, not build output, and
# names like ``coverage`` or ``build`` there are real modules.
_SKIP_DIRS: frozenset[str] = frozenset({"site-packages", "dist-packages"})

_SIZE_WORKERS = min(8, os.cpu_count() or 4)


def _is_link(entry: os.DirEntry) -> bool:
    """True for symlinks and NTFS junctions, which sizing must not follow."""
    if entry.is_symlink():
        return True
    is_junction = getattr(entry, "is_junction", None)  # Python 3.12+
    return bool(is_junction and is_junction())


def _dir_size(path: Path) -> int:
    """Total size of the files under ``path``.

    Uses ``os.scandir`` so the size comes from the directory listing itself
    (free on Windows) instead of one ``stat`` call per file.
    """
    total = 0
    stack = [os.fspath(path)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for entry in it:
                    try:
                        if _is_link(entry):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def _is_virtualenv(path: Path) -> bool:
    return (path / "pyvenv.cfg").is_file()


def scan_artifacts(
    root: Path,
    patterns: frozenset[str] | None = None,
    config=None,
) -> list[ArtifactScan]:
    """Walk ``root`` and return matched artifact directories, largest first.

    Matched directories are not descended into (so a ``node_modules`` inside
    another ``node_modules`` is not double-counted).  Protected system paths are
    silently skipped.
    """
    config = config or load_config()
    extra = config.section("purge").get("extra_patterns", [])
    active = (patterns or ARTIFACT_DIRS) | frozenset(extra)

    candidates: list[tuple[Path, str]] = []
    try:
        for dirpath, dirnames, _files in os.walk(root, onerror=lambda _e: None):
            current = Path(dirpath)
            keep: list[str] = []
            for name in dirnames:
                candidate = current / name
                if name in _SKIP_DIRS or _is_virtualenv(candidate):
                    continue
                if name in active:
                    if not is_protected(candidate):
                        candidates.append((candidate, name))
                    continue   # matched or protected: stop descending
                keep.append(name)
            dirnames[:] = keep
    except OSError:
        pass

    # Sizing is the slow part (every file under every match), and it is I/O
    # bound, so run it across a few threads.
    with ThreadPoolExecutor(max_workers=_SIZE_WORKERS) as pool:
        sizes = list(pool.map(_dir_size, (c for c, _ in candidates)))
    results = [ArtifactScan(path, size, name) for (path, name), size in zip(candidates, sizes, strict=True)]

    results.sort(key=lambda a: a.size_bytes, reverse=True)
    return results


def purge_artifacts(
    paths: Sequence[Path | ArtifactScan],
    *,
    dry_run: bool = True,
    config=None,
) -> CleanResult:
    """Send the given artifact directories to the Recycle Bin via the safety layer.

    Pass the ``ArtifactScan`` results from :func:`scan_artifacts` where you have
    them: their sizes are reused instead of walking every directory again.
    """
    config = config or load_config()
    extra_protected = config.section("safety").get("extra_protected_paths", [])

    bytes_freed = 0
    items = 0
    skipped: list[str] = []
    trashed: list[Path] = []

    for item in paths:
        if isinstance(item, ArtifactScan):
            p, known_size = item.path, item.size_bytes
        else:
            p, known_size = Path(item), None
        try:
            size = known_size if known_size is not None else _dir_size(p)
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
