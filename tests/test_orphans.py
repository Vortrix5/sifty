"""Tests for the orphaned app-data finder (sandboxed: fake roots and known names)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from typer.testing import CliRunner

from sifty.ai import tools
from sifty.cli.app import app
from sifty.core import orphans

NOW = time.time()
OLD = NOW - 400 * 86400


def _folder(root: Path, name: str, size: int = 2 * 1024 * 1024, mtime: float = OLD) -> Path:
    d = root / name
    d.mkdir(parents=True)
    f = d / "data.bin"
    f.write_bytes(b"x" * size)
    os.utime(f, (mtime, mtime))
    os.utime(d, (mtime, mtime))
    return d


def _find(root: Path, known: set[str], **kw):
    return [o.path.name for o in orphans.find_orphaned_app_data(roots=[root], known=known, now=NOW, **kw)]


def test_flags_old_unowned_folder(tmp_path):
    _folder(tmp_path, "Link2EA")
    assert _find(tmp_path, set()) == ["Link2EA"]


def test_owned_by_installed_app_name(tmp_path):
    _folder(tmp_path, "Discord")
    _folder(tmp_path, "discordptb")
    known = orphans._tokens("Discord") | orphans._tokens("Discord Inc.")
    assert _find(tmp_path, known) == []


def test_owned_by_partial_name_match(tmp_path):
    # "obs-studio" in the registry owns an "obs-studio" folder and "Mozilla Firefox" owns "Mozilla".
    _folder(tmp_path, "obs-studio")
    _folder(tmp_path, "Mozilla")
    known = orphans._tokens("OBS Studio") | orphans._tokens("Mozilla Firefox")
    assert _find(tmp_path, known) == []


def test_recently_used_folder_is_kept(tmp_path):
    _folder(tmp_path, "SomeOldApp", mtime=NOW - 5 * 86400)
    assert _find(tmp_path, set()) == []


def test_system_and_dev_tool_folders_are_never_flagged(tmp_path):
    for name in ("Microsoft", "Packages", "Temp", "IsolatedStorage", "fnm", "NuGet", "pnpm"):
        _folder(tmp_path, name)
    assert _find(tmp_path, set()) == []


def test_tiny_folders_skipped_by_default(tmp_path):
    _folder(tmp_path, "EmptyLeftover", size=10)
    assert _find(tmp_path, set()) == []
    assert _find(tmp_path, set(), min_size=0) == ["EmptyLeftover"]


def test_largest_first(tmp_path):
    _folder(tmp_path, "SmallGone", size=2 * 1024 * 1024)
    _folder(tmp_path, "BigGone", size=5 * 1024 * 1024)
    assert _find(tmp_path, set()) == ["BigGone", "SmallGone"]


def test_generic_words_dont_claim_ownership():
    # "Software" alone mustn't make every "*Software" folder look owned.
    assert "software" not in orphans._tokens("Foxit Software")
    assert "foxit" in orphans._tokens("Foxit Software")


def test_missing_root_is_tolerated(tmp_path):
    assert _find(tmp_path / "nope", set()) == []


def test_cli_lists_orphans_as_json(monkeypatch, tmp_path):
    item = orphans.OrphanedData(tmp_path / "Link2EA", 3_000_000, OLD)
    monkeypatch.setattr(orphans, "find_orphaned_app_data", lambda days=90: [item])
    result = CliRunner().invoke(app, ["--json", "apps", "orphaned-data"])
    assert result.exit_code == 0
    assert "Link2EA" in result.stdout


def test_ai_tool_is_read_only_and_reports(monkeypatch, tmp_path):
    item = orphans.OrphanedData(tmp_path / "Link2EA", 3_000_000, OLD)
    monkeypatch.setattr(orphans, "find_orphaned_app_data", lambda days=90: [item])
    tool = tools.get("find_orphaned_app_data")
    assert tool.risk == "read"
    res = tool.handler({"days": 5})
    assert res.rows[0][0] == "Link2EA"
    assert "leftovers" in res.summary
