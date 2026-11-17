"""Tests for git worktree cleanup (git + trash mocked)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sifty.core import safety, vcs
from sifty.core.vcs import OrphanWorktree


def _orphans(tmp_path: Path, names: list[str]) -> list[OrphanWorktree]:
    out = []
    for name in names:
        wt = tmp_path / name
        wt.mkdir()
        (wt / "f.txt").write_bytes(b"x" * 10)
        out.append(OrphanWorktree(wt, "abc123", "prunable by git"))
    return out


def test_prune_worktrees_trashes_all_orphans(tmp_path, monkeypatch):
    orphans = _orphans(tmp_path, ["wt-a", "wt-b"])
    trashed = []
    monkeypatch.setattr(vcs, "find_orphan_worktrees", lambda root: orphans)
    monkeypatch.setattr(vcs, "_run_git", lambda args, cwd: (True, ""))
    monkeypatch.setattr(safety, "send_to_trash", lambda p: trashed.append(p))
    monkeypatch.setattr(safety, "audit", lambda msg: None)

    result = vcs.prune_worktrees(tmp_path, dry_run=False)
    assert result.items == 2
    assert sorted(p.name for p in trashed) == ["wt-a", "wt-b"]


def test_prune_worktrees_honors_only_selection(tmp_path, monkeypatch):
    """``only`` restricts pruning to the selected worktree paths."""
    orphans = _orphans(tmp_path, ["wt-a", "wt-b"])
    trashed = []
    monkeypatch.setattr(vcs, "find_orphan_worktrees", lambda root: orphans)
    monkeypatch.setattr(vcs, "_run_git", lambda args, cwd: (True, ""))
    monkeypatch.setattr(safety, "send_to_trash", lambda p: trashed.append(p))
    monkeypatch.setattr(safety, "audit", lambda msg: None)

    result = vcs.prune_worktrees(tmp_path, dry_run=False, only=[orphans[0].path])
    assert result.items == 1
    assert [p.name for p in trashed] == ["wt-a"]
    assert orphans[1].path.exists()  # deselected worktree untouched


def test_prune_worktrees_dry_run_touches_nothing(tmp_path, monkeypatch):
    orphans = _orphans(tmp_path, ["wt-a"])
    git_calls = []
    monkeypatch.setattr(vcs, "find_orphan_worktrees", lambda root: orphans)
    monkeypatch.setattr(vcs, "_run_git", lambda args, cwd: (git_calls.append(args), "")[0] or (True, ""))
    monkeypatch.setattr(safety, "send_to_trash", lambda p: (_ for _ in ()).throw(AssertionError("trashed in dry-run")))

    result = vcs.prune_worktrees(tmp_path, dry_run=True)
    assert result.items == 1 and result.trashed == []
    assert orphans[0].path.exists()
    assert git_calls == []  # no `git worktree prune` in dry-run


_FAKE_CONFIG = SimpleNamespace(section=lambda name: {})


# --- _run_git --------------------------------------------------------------


def test_run_git_success(monkeypatch, tmp_path):
    monkeypatch.setattr(
        vcs.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="out", stderr="")
    )
    ok, out = vcs._run_git(["status"], tmp_path)
    assert ok is True
    assert out == "out"


def test_run_git_failure_captures_output(monkeypatch, tmp_path):
    monkeypatch.setattr(
        vcs.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="fatal: not a repo"),
    )
    ok, out = vcs._run_git(["status"], tmp_path)
    assert ok is False
    assert "fatal" in out


def test_run_git_handles_missing_git(monkeypatch, tmp_path):
    monkeypatch.setattr(
        vcs.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no git"))
    )
    ok, out = vcs._run_git(["status"], tmp_path)
    assert ok is False


# --- _parse_worktrees ------------------------------------------------------


def test_parse_worktrees():
    raw = "\n".join(
        [
            "",  # leading blank lines (empty buffer) are ignored
            "",
            "worktree C:/repo",
            "HEAD abc123",
            "branch refs/heads/main",
            "",
            "worktree C:/repo/wt-a",
            "HEAD def456",
            "branch refs/heads/feature",
            "",
            "worktree C:/repo/bare-wt",
            "bare",
        ]
    )
    entries = vcs._parse_worktrees(raw)
    assert len(entries) == 3
    assert entries[0]["path"] == Path("C:/repo")
    assert entries[0]["head"] == "abc123"
    assert entries[1]["branch"] == "refs/heads/feature"
    assert entries[2].get("bare") is True


# --- find_orphan_worktrees -------------------------------------------------


def test_find_orphan_worktrees_classifies(monkeypatch, tmp_path):
    (tmp_path / "wt-prune").mkdir()
    (tmp_path / "wt-keep").mkdir()
    (tmp_path / "wt-detached").mkdir()
    raw = "\n".join(
        [
            f"worktree {tmp_path}",
            "HEAD aaa",
            "branch refs/heads/main",
            "",
            f"worktree {tmp_path / 'wt-gone'}",
            "HEAD bbb",
            "branch refs/heads/gone",
            "",
            f"worktree {tmp_path / 'wt-prune'}",
            "HEAD ccc",
            "branch refs/heads/dead",
            "",
            f"worktree {tmp_path / 'wt-keep'}",
            "HEAD ddd",
            "branch refs/heads/alive",
            "",
            f"worktree {tmp_path / 'wt-detached'}",
            "HEAD eee",
            "detached",
            "",
        ]
    )
    existing_branches = {"refs/heads/main", "refs/heads/alive"}

    def fake_git(args, cwd):
        if args[0] == "rev-parse":
            return (args[-1] in existing_branches), ""
        return True, raw  # both `worktree list` variants

    monkeypatch.setattr(vcs, "_run_git", fake_git)
    orphans = vcs.find_orphan_worktrees(tmp_path)
    assert {o.path.name: o.reason for o in orphans} == {
        "wt-gone": "missing directory",
        "wt-prune": "prunable by git",
    }


def test_find_orphan_worktrees_skips_entry_without_path(monkeypatch, tmp_path):
    raw = "\n".join(
        [
            f"worktree {tmp_path}",
            "HEAD aaa",
            "branch refs/heads/main",
            "",
            "HEAD orphanhead",  # malformed block: no `worktree` line → no path
            "",
        ]
    )
    monkeypatch.setattr(vcs, "_run_git", lambda args, cwd: (True, raw))
    assert vcs.find_orphan_worktrees(tmp_path) == []


def test_find_orphan_worktrees_git_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(vcs, "_run_git", lambda args, cwd: (False, "git missing"))
    assert vcs.find_orphan_worktrees(tmp_path) == []


def test_find_orphan_worktrees_no_entries(monkeypatch, tmp_path):
    monkeypatch.setattr(vcs, "_run_git", lambda args, cwd: (True, ""))
    assert vcs.find_orphan_worktrees(tmp_path) == []


def test_find_orphan_worktrees_respects_lock(monkeypatch, tmp_path):
    (tmp_path / "wt-locked").mkdir()
    lock = tmp_path / ".git" / "worktrees" / "wt-locked" / "locked"
    lock.parent.mkdir(parents=True)
    lock.write_text("")
    raw = "\n".join(
        [
            f"worktree {tmp_path}",
            "HEAD aaa",
            "branch refs/heads/main",
            "",
            f"worktree {tmp_path / 'wt-locked'}",
            "HEAD ccc",
            "branch refs/heads/dead",
            "",
        ]
    )
    # rev-parse fails for the dead branch, but the lock file keeps it.
    monkeypatch.setattr(
        vcs, "_run_git", lambda args, cwd: (False, "") if args[0] == "rev-parse" else (True, raw)
    )
    assert vcs.find_orphan_worktrees(tmp_path) == []


# --- prune_worktrees edge cases --------------------------------------------


def test_prune_worktrees_skips_protected(tmp_path, monkeypatch):
    from sifty.core.safety import ProtectedPathError

    orphans = _orphans(tmp_path, ["wt-a"])
    monkeypatch.setattr(vcs, "find_orphan_worktrees", lambda root: orphans)
    monkeypatch.setattr(vcs, "_run_git", lambda args, cwd: (True, ""))

    def _trash(p, **k):
        raise ProtectedPathError(f"protected: {p}")

    monkeypatch.setattr(vcs, "trash", _trash)
    result = vcs.prune_worktrees(tmp_path, dry_run=False, config=_FAKE_CONFIG)
    assert result.items == 0
    assert len(result.skipped) == 1
    assert "protected" in result.skipped[0]


def test_prune_worktrees_skips_on_os_error(tmp_path, monkeypatch):
    orphans = _orphans(tmp_path, ["wt-a"])
    monkeypatch.setattr(vcs, "find_orphan_worktrees", lambda root: orphans)
    monkeypatch.setattr(vcs, "_run_git", lambda args, cwd: (True, ""))

    def _trash(p, **k):
        raise OSError("file in use")

    monkeypatch.setattr(vcs, "trash", _trash)
    result = vcs.prune_worktrees(tmp_path, dry_run=False, config=_FAKE_CONFIG)
    assert result.items == 0
    assert len(result.skipped) == 1
    assert "file in use" in result.skipped[0]


def test_prune_worktrees_counts_already_gone(tmp_path, monkeypatch):
    gone = OrphanWorktree(tmp_path / "vanished", "abc", "missing directory")
    monkeypatch.setattr(vcs, "find_orphan_worktrees", lambda root: [gone])
    monkeypatch.setattr(vcs, "_run_git", lambda args, cwd: (True, ""))
    monkeypatch.setattr(
        vcs, "trash", lambda p, **k: (_ for _ in ()).throw(AssertionError("should not trash"))
    )
    result = vcs.prune_worktrees(tmp_path, dry_run=False, config=_FAKE_CONFIG)
    assert result.items == 1
    assert result.trashed == []


def test_prune_worktrees_no_orphans(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "find_orphan_worktrees", lambda root: [])
    result = vcs.prune_worktrees(tmp_path, dry_run=False, config=_FAKE_CONFIG)
    assert result.items == 0
    assert result.trashed == []
