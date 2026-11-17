"""Git worktree cleanup: detect and prune orphaned worktrees.

Worktrees accumulate on dev machines: tools and scripts add them for isolated
checkouts and experiments, and they tend to outlive whatever created them,
at several GB each.

``find_orphan_worktrees`` identifies worktrees that git itself considers prunable
(deleted branch, missing lock, absent directory).  ``prune_worktrees`` runs
``git worktree prune`` to remove the stale registration and optionally trashes
the on-disk directory.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import CleanResult
from .safety import ProtectedPathError, trash

__all__ = ["OrphanWorktree", "find_orphan_worktrees", "prune_worktrees"]


@dataclass
class OrphanWorktree:
    path: Path
    head: str    # commit SHA or branch, or "detached" / "unknown"
    reason: str  # "prunable by git" | "missing directory"


def _run_git(args: list[str], cwd: Path) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return False, str(exc)


def _parse_worktrees(raw: str) -> list[dict]:
    """Parse `git worktree list --porcelain` output into dicts."""
    entries: list[dict] = []
    current: dict = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            if current:
                entries.append(current)
                current = {}
        elif line.startswith("worktree "):
            current["path"] = Path(line[9:])
        elif line.startswith("HEAD "):
            current["head"] = line[5:]
        elif line.startswith("branch "):
            current["branch"] = line[7:]
        elif line == "bare":
            current["bare"] = True
    if current:
        entries.append(current)
    return entries


def find_orphan_worktrees(root: Path) -> list[OrphanWorktree]:
    """Return worktrees in ``root`` that git considers prunable or whose directory is gone.

    The main worktree is always skipped.
    """
    ok, raw = _run_git(["worktree", "list", "--porcelain"], root)
    if not ok:
        return []

    entries = _parse_worktrees(raw)
    if not entries:
        return []

    orphans: list[OrphanWorktree] = []

    for entry in entries[1:]:   # skip main worktree
        wt_path = entry.get("path")
        if wt_path is None:
            continue
        head = entry.get("head", "unknown")[:12]  # short SHA

        if not wt_path.exists():
            orphans.append(OrphanWorktree(wt_path, head, "missing directory"))
            continue

        # Ask git whether this worktree would be pruned
        prunable_ok, prunable_out = _run_git(
            ["worktree", "list", "--porcelain", "--expired"],
            root,
        )
        # Simpler reliable check: locked file absent + branch gone
        lock_file = root / ".git" / "worktrees" / wt_path.name / "locked"
        branch = entry.get("branch", "")
        if branch:
            branch_ok, _ = _run_git(["rev-parse", "--verify", branch], root)
            if not branch_ok and not lock_file.exists():
                orphans.append(OrphanWorktree(wt_path, head, "prunable by git"))

    return orphans


def prune_worktrees(
    root: Path,
    *,
    dry_run: bool = True,
    config=None,
    only: list[Path] | None = None,
) -> CleanResult:
    """Prune orphaned worktree registrations and trash the on-disk directories.

    ``only`` restricts action to the given worktree paths (e.g. the user's
    selection in the TUI); ``None`` means every orphan. Trashes the on-disk
    directories first, then runs ``git worktree prune`` so the newly-missing
    directories are also deregistered (metadata-only).
    """
    orphans = find_orphan_worktrees(root)
    if only is not None:
        wanted = {str(p) for p in only}
        orphans = [o for o in orphans if str(o.path) in wanted]
    if not orphans:
        return CleanResult(0, 0, [], [])

    from ..infra.config import load_config
    from .junk import _dir_size as _ds

    config = config or load_config()
    extra_protected = config.section("safety").get("extra_protected_paths", [])

    bytes_freed = 0
    items = 0
    skipped: list[str] = []
    trashed: list[Path] = []

    for ow in orphans:
        if not ow.path.exists():
            items += 1   # already gone (prune cleaned it)
            continue
        try:
            size, _ = _ds(ow.path)
            trash(ow.path, extra_protected=extra_protected, dry_run=dry_run)
            bytes_freed += size
            items += 1
            if not dry_run:
                trashed.append(ow.path)
        except ProtectedPathError as exc:
            skipped.append(str(exc))
        except OSError as exc:
            skipped.append(f"{ow.path}: {exc}")

    if not dry_run:
        _run_git(["worktree", "prune"], root)

    return CleanResult(bytes_freed, items, skipped, trashed)
