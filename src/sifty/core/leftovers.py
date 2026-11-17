"""Post-uninstall leftover scanner: find what an uninstaller left behind.

Windows uninstallers routinely leave settings, caches, and shortcuts behind in
``%APPDATA%``, ``%LOCALAPPDATA%``, ``%PROGRAMDATA%`` and the Start Menu. Given
an app's display name (and optionally its publisher), :func:`find_leftovers`
locates directories and shortcuts that match it, and :func:`clean_leftovers`
sends a confirmed selection to the Recycle Bin through the safety layer.

Matching is deliberately conservative: only exact (normalized) name matches,
never inside ``Program Files`` / ``Windows``, and never for generic vendor
names like "Microsoft" - a false positive here would trash live user data.
Registry traces are covered separately by ``sifty apps orphans`` (read-only by
policy).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..infra.config import load_config
from .models import CleanResult
from .safety import ProtectedPathError, trash

__all__ = ["Leftover", "find_leftovers", "clean_leftovers"]

# Names that are never leftovers no matter what app we're matching - shared
# vendor/system directories that hold many apps' data.
_NEVER_MATCH = {
    "microsoft", "windows", "common files", "commonfiles", "packages",
    "programs", "temp", "local", "locallow", "roaming", "intel", "nvidia",
    "amd", "realtek", "google", "mozilla", "apple", "adobe", "oracle",
    "python", "java", "node", "npm", "pip", "cache", "logs", "settings",
}

# Version-ish / noise tokens stripped from app display names ("App 1.2.3 (x64)").
_NOISE_TOKEN = re.compile(r"^(v?\d[\d.]*|x64|x86|64-bit|32-bit|\(.*\))$")


@dataclass
class Leftover:
    path: Path
    size_bytes: int
    kind: str  # "data-dir" | "shortcut"


def _normalize(name: str) -> str:
    """Lowercase, strip version/arch noise tokens and punctuation."""
    cleaned = re.sub(r"[®™©]", "", name.lower())
    tokens = [t for t in re.split(r"[\s_\-]+", cleaned) if t and not _NOISE_TOKEN.match(t)]
    return " ".join(tokens)


def _candidates(app_name: str) -> set[str]:
    """Normalized strings a leftover directory name may equal."""
    norm = _normalize(app_name)
    if not norm or norm in _NEVER_MATCH or len(norm) < 4:
        return set()
    return {norm, norm.replace(" ", ""), norm.replace(" ", "-"), norm.replace(" ", "_")}


def _matches(dir_name: str, candidates: set[str]) -> bool:
    norm = _normalize(dir_name)
    return bool(norm) and norm not in _NEVER_MATCH and (
        norm in candidates or norm.replace(" ", "") in candidates
    )


def _default_roots() -> list[Path]:
    roots: list[Path] = []
    for var, sub in [
        ("LOCALAPPDATA", ""), ("LOCALAPPDATA", "Programs"),
        ("APPDATA", ""), ("PROGRAMDATA", ""),
    ]:
        base = os.environ.get(var)
        if base:
            path = Path(base) / sub if sub else Path(base)
            if path.is_dir():
                roots.append(path)
    return roots


def _shortcut_roots() -> list[Path]:
    roots: list[Path] = []
    for var in ("APPDATA", "PROGRAMDATA"):
        base = os.environ.get(var)
        if base:
            path = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            if path.is_dir():
                roots.append(path)
    return roots


def _dir_size(path: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for fname in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, fname))
            except OSError:
                continue
    return total


def find_leftovers(
    app_name: str,
    publisher: str = "",
    *,
    roots: list[Path] | None = None,
    shortcut_roots: list[Path] | None = None,
) -> list[Leftover]:
    """Directories and Start-Menu shortcuts left behind by ``app_name``.

    Looks one level deep in each data root, plus ``<Publisher>/<App>`` two-level
    layouts when ``publisher`` is given. ``roots`` overrides the default
    AppData/ProgramData roots (used by tests).
    """
    candidates = _candidates(app_name)
    if not candidates:
        return []
    pub_candidates = _candidates(publisher) if publisher else set()

    found: list[Leftover] = []
    seen: set[str] = set()

    def _add(path: Path, kind: str) -> None:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            size = _dir_size(path) if path.is_dir() else _file_size(path)
            found.append(Leftover(path, size, kind))

    for root in (roots if roots is not None else _default_roots()):
        try:
            entries = [e for e in root.iterdir() if e.is_dir()]
        except OSError:
            continue
        for entry in entries:
            if _matches(entry.name, candidates):
                _add(entry, "data-dir")
            elif pub_candidates and _matches(entry.name, pub_candidates):
                # <Publisher>/<App> layout: only flag the app's subdirectory.
                try:
                    for sub in entry.iterdir():
                        if sub.is_dir() and _matches(sub.name, candidates):
                            _add(sub, "data-dir")
                except OSError:
                    continue

    for root in (shortcut_roots if shortcut_roots is not None else _shortcut_roots()):
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            stem = entry.stem if entry.is_file() else entry.name
            if _matches(stem, candidates):
                _add(entry, "shortcut")

    return found


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def clean_leftovers(
    items: list[Leftover],
    *,
    dry_run: bool = True,
    config=None,
) -> CleanResult:
    """Send leftover items to the Recycle Bin via the safety layer.

    Each item vouches only for itself (``allow_subtrees=[item]``) - ProgramData
    entries need that carve-out, but everything else stays protected.
    """
    config = config or load_config()
    extra_protected = config.section("safety").get("extra_protected_paths", [])

    # Defense-in-depth: the per-item carve-out below must never reach into the
    # OS or installed-program trees, even if a caller passes a bad path.
    forbidden = [
        Path(p) for p in (
            os.environ.get("SystemRoot"),
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
        ) if p
    ]

    bytes_freed = 0
    count = 0
    skipped: list[str] = []
    trashed: list[Path] = []

    for item in items:
        resolved = Path(os.path.normpath(item.path)).absolute()
        if any(resolved == f or f in resolved.parents for f in forbidden):
            skipped.append(f"{item.path}: refused (system tree)")
            continue
        try:
            trash(
                item.path,
                allow_subtrees=[item.path],
                extra_protected=extra_protected,
                dry_run=dry_run,
            )
            bytes_freed += item.size_bytes
            count += 1
            if not dry_run:
                trashed.append(item.path)
        except ProtectedPathError as exc:
            skipped.append(str(exc))
        except OSError as exc:
            skipped.append(f"{item.path}: {exc}")

    return CleanResult(bytes_freed, count, skipped, trashed)
