"""Tests for the dev-artifact purge engine (sandboxed)."""

from __future__ import annotations

from pathlib import Path

from sifty.core import purge


def _make_tree(root: Path, structure: dict) -> None:
    for name, content in structure.items():
        child = root / name
        if isinstance(content, dict):
            child.mkdir(parents=True, exist_ok=True)
            _make_tree(child, content)
        else:
            child.parent.mkdir(parents=True, exist_ok=True)
            child.write_bytes(b"x" * content)


def test_scan_finds_artifact_dirs(tmp_path):
    _make_tree(tmp_path, {
        "myapp": {
            "src": {"main.py": 100},
            "node_modules": {"pkg": {"index.js": 500}},
            "dist": {"bundle.js": 1000},
        }
    })
    results = purge.scan_artifacts(tmp_path)
    patterns = {a.pattern for a in results}
    assert "node_modules" in patterns
    assert "dist" in patterns
    # src is not an artifact dir
    assert "src" not in patterns


def test_scan_does_not_descend_into_matched_dir(tmp_path):
    # nested node_modules inside node_modules must not be double-counted
    _make_tree(tmp_path, {
        "project": {
            "node_modules": {
                "pkg": {
                    "node_modules": {"sub": {"f.js": 200}},
                    "index.js": 100,
                }
            }
        }
    })
    results = purge.scan_artifacts(tmp_path)
    # Only the top-level node_modules should appear once
    assert len(results) == 1
    assert results[0].pattern == "node_modules"


def test_scan_skips_protected_paths(tmp_path, monkeypatch):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "file.js").write_bytes(b"x" * 100)
    # Patch is_protected in the purge module's own namespace (it was imported there)
    monkeypatch.setattr("sifty.core.purge.is_protected", lambda *a, **k: True)
    results = purge.scan_artifacts(tmp_path)
    assert results == []


def test_purge_artifacts_dry_run(tmp_path):
    art = tmp_path / "node_modules"
    art.mkdir()
    (art / "index.js").write_bytes(b"x" * 200)
    result = purge.purge_artifacts([art], dry_run=True)
    assert result.items == 1
    assert result.bytes_freed > 0
    assert art.exists()   # dry-run must NOT delete


def test_purge_artifacts_apply(tmp_path, monkeypatch):
    art = tmp_path / "__pycache__"
    art.mkdir()
    (art / "mod.pyc").write_bytes(b"x" * 300)
    trashed = []
    # Patch safety.trash directly - that's the single deletion call site
    monkeypatch.setattr("sifty.core.purge.trash", lambda p, **kw: trashed.append(p))
    result = purge.purge_artifacts([art], dry_run=False)
    assert result.items == 1
    assert len(trashed) == 1


def test_scan_respects_extra_patterns(tmp_path):
    (tmp_path / "my_custom_cache").mkdir()
    (tmp_path / "my_custom_cache" / "data.bin").write_bytes(b"x" * 100)
    # Not in ARTIFACT_DIRS, so should not appear without config
    results = purge.scan_artifacts(tmp_path)
    assert all(a.pattern != "my_custom_cache" for a in results)
    # With custom config
    results2 = purge.scan_artifacts(tmp_path, patterns=frozenset({"my_custom_cache"}))
    assert any(a.pattern == "my_custom_cache" for a in results2)


def test_dir_size_counts_nested_files(tmp_path):
    _make_tree(tmp_path, {"a.bin": 100, "sub": {"b.bin": 200, "deeper": {"c.bin": 300}}})
    assert purge._dir_size(tmp_path) == 600


def test_dir_size_missing_path_is_zero(tmp_path):
    assert purge._dir_size(tmp_path / "nope") == 0


def test_scan_skips_virtualenvs_and_site_packages(tmp_path):
    # Real packages named like artifacts (coverage, build) must never be offered.
    _make_tree(tmp_path, {
        "proj": {
            "dist": {"app.js": 100},
            ".venv": {
                "pyvenv.cfg": 10,
                "Lib": {"site-packages": {"coverage": {"__init__.py": 50}}},
            },
            "Lib": {"site-packages": {"build": {"__init__.py": 50}}},
        }
    })
    results = purge.scan_artifacts(tmp_path)
    assert [a.pattern for a in results] == ["dist"]


def test_scan_sizes_match_contents(tmp_path):
    _make_tree(tmp_path, {
        "a": {"node_modules": {"x.js": 700}},
        "b": {"dist": {"y.js": 300}},
    })
    results = purge.scan_artifacts(tmp_path)
    assert [(a.pattern, a.size_bytes) for a in results] == [("node_modules", 700), ("dist", 300)]


def test_purge_reuses_scan_sizes(tmp_path, monkeypatch):
    art = purge.ArtifactScan(tmp_path / "dist", 1234, "dist")
    monkeypatch.setattr("sifty.core.purge.trash", lambda p, **kw: True)
    monkeypatch.setattr(
        "sifty.core.purge._dir_size", lambda p: (_ for _ in ()).throw(AssertionError("re-walked"))
    )
    result = purge.purge_artifacts([art], dry_run=False)
    assert result.items == 1
    assert result.bytes_freed == 1234
    assert result.trashed == [tmp_path / "dist"]
