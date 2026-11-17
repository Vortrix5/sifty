"""Safety guardrails - the only place the app is allowed to delete things.

The model has three tiers of roots plus per-call carve-outs:

* **Contents-protected roots** - critical OS directories (``C:\\Windows``, the
  ``Program Files`` trees, ``ProgramData``). Deleting one of these *or anything
  inside it* is refused, and deleting an *ancestor* of one (e.g. ``C:\\``) is
  refused too.
* **Self-protected roots** - the drive root (``C:\\``) and the user's profile
  root. Deleting the root *itself* (or an ancestor) is refused, but ordinary
  files inside them are fine - otherwise the whole disk would be off-limits.
* **Allowed subtrees** - explicit carve-outs a caller vouches for (e.g. the
  junk module's temp/cache locations). A path inside a contents-protected root
  is only permitted if it also sits inside one of these.

User-supplied ``extra_protected`` paths are treated as contents-protected.

Every deletion goes through :func:`trash`, which sends to the Recycle Bin via
Send2Trash - there is no permanent ``os.remove``/``rmtree`` anywhere in the app.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from ..infra.config import audit_log_path
from ..windows.recyclebin import send_to_trash


class ProtectedPathError(Exception):
    """Raised when a delete target is blocked by the safety denylist."""


def _system_drive() -> Path:
    return Path(os.environ.get("SystemDrive", "C:") + "\\")


def _dedup(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for p in paths:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def contents_protected_roots(extra: Iterable[str | Path] = ()) -> list[Path]:
    """Roots where deleting the root *or anything inside it* is refused."""
    candidates = [
        Path(os.environ.get("SystemRoot", r"C:\Windows")),  # C:\Windows
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("ProgramData", r"C:\ProgramData")),
    ]
    candidates.extend(Path(e) for e in extra)
    return _dedup(_norm(c) for c in candidates)


def self_protected_roots() -> list[Path]:
    """Roots where only the root itself (or an ancestor) is off-limits."""
    candidates = [
        _system_drive(),  # the drive root itself, e.g. C:\
        Path.home(),  # the user profile root, e.g. C:\Users\amine
    ]
    return _dedup(_norm(c) for c in candidates)


def _norm(path: Path) -> Path:
    """Normalise for comparison without requiring the path to exist."""
    try:
        return Path(os.path.normcase(os.path.abspath(str(path))))
    except (OSError, ValueError):
        return Path(os.path.normcase(str(path)))


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def is_protected(
    path: str | Path,
    allow_subtrees: Sequence[str | Path] = (),
    extra_protected: Iterable[str | Path] = (),
) -> bool:
    """Return ``True`` if deleting ``path`` should be refused."""
    target = _norm(Path(path))
    allowed = [_norm(Path(a)) for a in allow_subtrees]

    for root in contents_protected_roots(extra_protected):
        # Deleting the root itself, or an ancestor of one (which would take the
        # root with it), is always refused.
        if target == root or _is_relative_to(root, target):
            return True
        # Deleting something *inside* a contents-protected root is refused
        # unless the caller has explicitly vouched for that subtree.
        if _is_relative_to(target, root):
            if any(_is_relative_to(target, a) for a in allowed):
                return False
            return True

    for root in self_protected_roots():
        # Only the root itself (or an ancestor) is off-limits; contents are OK.
        if target == root or _is_relative_to(root, target):
            return True

    return False


def assert_safe(
    path: str | Path,
    allow_subtrees: Sequence[str | Path] = (),
    extra_protected: Iterable[str | Path] = (),
) -> None:
    if is_protected(path, allow_subtrees, extra_protected):
        raise ProtectedPathError(f"Refusing to delete protected path: {path}")


def trash(
    path: str | Path,
    allow_subtrees: Sequence[str | Path] = (),
    extra_protected: Iterable[str | Path] = (),
    *,
    dry_run: bool = True,
) -> bool:
    """Send ``path`` to the Recycle Bin after a safety check.

    Returns ``True`` if the item was (or, in dry-run, would be) trashed.
    Raises :class:`ProtectedPathError` if the path is protected.
    """
    assert_safe(path, allow_subtrees, extra_protected)
    if dry_run:
        return True
    send_to_trash(path)
    audit(f"TRASH {path}")
    return True


def audit(message: str) -> None:
    """Append a timestamped line to the audit log."""
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    line = f"{stamp} {message}\n"
    with audit_log_path().open("a", encoding="utf-8") as fh:
        fh.write(line)
