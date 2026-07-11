"""Tests for anomaly detection (temp snapshot DB, no real OS scans)."""

from __future__ import annotations

import pytest

from sifty.core import anomaly
from sifty.core.models import VolumeUsage

_GB = 1 << 30


@pytest.fixture
def temp_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def _vol(mount: str, free: int) -> VolumeUsage:
    return VolumeUsage("dev", mount, "NTFS", 100 * _GB, 100 * _GB - free, free)


def test_first_run_has_no_anomalies(temp_appdata):
    found = anomaly.detect(volumes=[_vol("C:\\", 60 * _GB)], junk_total=0, startup_names=[])
    assert found == []


def test_disk_drop_detected(temp_appdata):
    anomaly.record_snapshot(volumes=[_vol("C:\\", 60 * _GB)], junk_total=0, startup_names=[])
    found = anomaly.detect(volumes=[_vol("C:\\", 15 * _GB)], junk_total=0, startup_names=[])
    drops = [a for a in found if a.kind == "disk_drop"]
    assert len(drops) == 1
    assert drops[0].severity == "attention"
    assert drops[0].detail["mountpoint"] == "C:\\"


def test_small_disk_drop_ignored(temp_appdata):
    anomaly.record_snapshot(volumes=[_vol("C:\\", 60 * _GB)], junk_total=0, startup_names=[])
    # 2 GB drop is below the 10 GB default threshold.
    found = anomaly.detect(volumes=[_vol("C:\\", 58 * _GB)], junk_total=0, startup_names=[])
    assert not any(a.kind == "disk_drop" for a in found)


def test_junk_growth_detected(temp_appdata):
    anomaly.record_snapshot(volumes=[], junk_total=1 * _GB, startup_names=[])
    found = anomaly.detect(volumes=[], junk_total=7 * _GB, startup_names=[])
    assert any(a.kind == "junk_growth" for a in found)


def test_new_startup_detected(temp_appdata):
    anomaly.record_snapshot(volumes=[], junk_total=0, startup_names=["Steam", "OneDrive"])
    found = anomaly.detect(volumes=[], junk_total=0, startup_names=["Steam", "OneDrive", "SketchyApp"])
    new = [a for a in found if a.kind == "new_startup"]
    assert len(new) == 1
    assert "SketchyApp" in new[0].detail["names"]


def test_unchanged_startup_not_flagged(temp_appdata):
    anomaly.record_snapshot(volumes=[], junk_total=0, startup_names=["Steam"])
    found = anomaly.detect(volumes=[], junk_total=0, startup_names=["Steam"])
    assert not any(a.kind == "new_startup" for a in found)


def test_snapshot_pruning_bounds_series(temp_appdata):
    for i in range(anomaly._KEEP_PER_SERIES + 15):
        anomaly.record_snapshot(volumes=[_vol("C:\\", i)], junk_total=0, startup_names=[])
    conn = anomaly._connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM snapshots WHERE kind='disk_free' AND key='C:\\'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n <= anomaly._KEEP_PER_SERIES
