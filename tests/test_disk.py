"""Tests for disk analysis: biggest items and duplicate detection."""

from __future__ import annotations

from types import SimpleNamespace

from sifty.core import disk


def test_biggest_orders_by_size(tmp_path):
    (tmp_path / "small.txt").write_text("a" * 10)
    (tmp_path / "big.txt").write_text("a" * 1000)
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "f.bin").write_text("a" * 500)

    result = disk.biggest(tmp_path, top=3)
    names = [p.name for p, _ in result]
    assert names[0] == "big.txt"  # 1000
    assert names[1] == "folder"  # 500
    assert names[2] == "small.txt"  # 10


def test_find_duplicates_groups_identical_content(tmp_path):
    (tmp_path / "x1.txt").write_text("identical content here")
    (tmp_path / "x2.txt").write_text("identical content here")
    (tmp_path / "unique.txt").write_text("totally different content")

    groups = disk.find_duplicates(tmp_path, min_size=1)
    assert len(groups) == 1
    (paths,) = groups.values()
    assert {p.name for p in paths} == {"x1.txt", "x2.txt"}


def test_find_duplicates_respects_min_size(tmp_path):
    (tmp_path / "a.txt").write_text("hi")
    (tmp_path / "b.txt").write_text("hi")
    assert disk.find_duplicates(tmp_path, min_size=100) == {}


def test_find_duplicates_hardlinks_not_counted_as_wasted(tmp_path):
    """NTFS hardlinks share the same inode - they are NOT duplicate space."""
    original = tmp_path / "original.bin"
    original.write_bytes(b"x" * 1000)
    link = tmp_path / "hardlink.bin"
    link.hardlink_to(original)

    groups = disk.find_duplicates(tmp_path, min_size=1, count_hardlinks_once=True)
    # The two paths share st_ino so only one is kept - no duplicate group formed.
    assert len(groups) == 0


def test_find_duplicates_hardlinks_opt_out(tmp_path):
    """count_hardlinks_once=False: hardlinks are treated as duplicate content."""
    original = tmp_path / "original.bin"
    original.write_bytes(b"x" * 1000)
    link = tmp_path / "hardlink.bin"
    link.hardlink_to(original)

    groups = disk.find_duplicates(tmp_path, min_size=1, count_hardlinks_once=False)
    # Both paths included → same hash → reported as duplicates
    assert len(groups) == 1


# --- volumes ---------------------------------------------------------------


def test_volumes_skips_unreadable_partition(monkeypatch):
    parts = [
        SimpleNamespace(device="C:", mountpoint="C:\\", fstype="NTFS"),
        SimpleNamespace(device="D:", mountpoint="D:\\", fstype="NTFS"),
    ]
    monkeypatch.setattr(disk.psutil, "disk_partitions", lambda all=False: parts)

    def usage(mountpoint):
        if mountpoint == "D:\\":
            raise PermissionError("access denied")
        return SimpleNamespace(total=100, used=60, free=40)

    monkeypatch.setattr(disk.psutil, "disk_usage", usage)
    vols = disk.volumes()
    assert [v.mountpoint for v in vols] == ["C:\\"]
    assert vols[0].free == 40


# --- _entry_size / biggest / hashing edge cases ----------------------------


def test_entry_size_file_stat_error(tmp_path):
    f = tmp_path / "f.bin"
    f.write_bytes(b"x" * 10)
    base = type(f)

    class _Bad(base):
        def is_file(self, *a, **k):
            return True

        def stat(self, *a, **k):
            raise OSError("stat failed")

    assert disk._entry_size(_Bad(str(f))) == 0


def test_entry_size_dir_skips_unstattable(monkeypatch, tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    monkeypatch.setattr(disk.os, "walk", lambda p, onerror=None: [(str(d), [], ["ghost.bin"])])
    assert disk._entry_size(d) == 0


def test_biggest_unreadable_path_returns_empty(tmp_path):
    assert disk.biggest(tmp_path / "ghost") == []


def test_find_duplicates_skips_unstattable(monkeypatch, tmp_path):
    monkeypatch.setattr(disk.os, "walk", lambda p, onerror=None: [(str(tmp_path), [], ["ghost.bin"])])
    assert disk.find_duplicates(tmp_path) == {}


def test_find_duplicates_skips_unhashable_candidate(monkeypatch, tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 100)
    (tmp_path / "b.bin").write_bytes(b"x" * 100)
    real_hash = disk._hash_file

    def fake_hash(p, chunk=1 << 20):
        return None if p.name == "a.bin" else real_hash(p)

    monkeypatch.setattr(disk, "_hash_file", fake_hash)
    # a.bin's hash is None → dropped; b.bin alone → no duplicate group.
    assert disk.find_duplicates(tmp_path, min_size=1) == {}


def test_hash_file_unreadable_returns_none(tmp_path):
    assert disk._hash_file(tmp_path / "ghost") is None
