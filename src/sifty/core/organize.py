"""File organization (engine): plan, apply, and undo moves by type or date."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from ..infra.config import app_data_dir
from .models import Move

__all__ = ["Move", "TYPE_FOLDERS", "plan_organization", "apply_moves", "undo_last"]

# Extension → destination subfolder.
TYPE_FOLDERS: dict[str, str] = {}
for folder, exts in {
    "Images": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".svg", ".tiff"],
    "Documents": [".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".md"],
    "Videos": [".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm"],
    "Audio": [".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"],
    "Archives": [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"],
    "Installers": [".exe", ".msi", ".msix", ".appx"],
    "Code": [".py", ".js", ".ts", ".java", ".c", ".cpp", ".cs", ".go", ".rs", ".rb", ".sh", ".json", ".xml", ".html", ".css"],
}.items():
    for _ext in exts:
        TYPE_FOLDERS[_ext] = folder


def _dest_folder_by_type(file: Path) -> str:
    return TYPE_FOLDERS.get(file.suffix.lower(), "Other")


def _dest_folder_by_date(file: Path) -> str:
    try:
        mtime = file.stat().st_mtime
    except OSError:
        return "Unknown-date"
    return datetime.fromtimestamp(mtime).strftime("%Y-%m")


def plan_organization(path: Path, scheme: str = "type") -> list[Move]:
    """Build the list of (src → dest) moves for top-level files in ``path``."""
    chooser = _dest_folder_by_date if scheme == "date" else _dest_folder_by_type
    moves: list[Move] = []
    for entry in path.iterdir():
        if not entry.is_file():
            continue  # only loose files at the top level are organized
        folder = chooser(entry)
        dest_dir = path / folder
        if entry.parent == dest_dir:
            continue  # already in place
        moves.append(Move(entry, dest_dir / entry.name))
    return moves


def _unique_dest(dest: Path) -> Path:
    """Avoid clobbering an existing file by suffixing ``(n)``."""
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    n = 1
    while True:
        candidate = dest.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def _session_file() -> Path:
    return app_data_dir() / "last_organize.json"


def apply_moves(moves: list[Move]) -> int:
    """Execute the planned moves, creating destination folders as needed.

    Records the executed (src, dest) pairs so :func:`undo_last` can move
    everything back. Returns the number of files moved.
    """
    executed: list[tuple[Path, Path]] = []
    for move in moves:
        move.dest.parent.mkdir(parents=True, exist_ok=True)
        final = _unique_dest(move.dest)
        shutil.move(str(move.src), str(final))
        executed.append((move.src, final))
    if executed:
        _session_file().write_text(
            json.dumps([{"src": str(s), "dest": str(d)} for s, d in executed]),
            encoding="utf-8",
        )
    return len(executed)


def last_session() -> list[tuple[Path, Path]]:
    """The (src, dest) pairs from the most recent ``apply_moves``; [] if none."""
    path = _session_file()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [(Path(e["src"]), Path(e["dest"])) for e in raw]
    except (ValueError, KeyError, OSError):
        return []


def undo_last() -> tuple[int, int]:
    """Move the files from the last organize session back where they were.

    Returns ``(restored, failed)``. A file is only moved back when it is still
    at its organized location and nothing has taken its old place. Emptied
    destination folders are removed best-effort.
    """
    pairs = last_session()
    restored = 0
    failed = 0
    touched_dirs: set[Path] = set()
    for src, dest in pairs:
        if not dest.exists() or src.exists():
            failed += 1
            continue
        try:
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest), str(src))
            restored += 1
            touched_dirs.add(dest.parent)
        except OSError:
            failed += 1
    for folder in touched_dirs:
        try:
            folder.rmdir()  # only succeeds when empty - exactly what we want
        except OSError:
            pass
    if pairs:
        try:
            _session_file().unlink()
        except OSError:
            pass
    return restored, failed
