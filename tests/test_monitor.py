"""Tests for core.monitor: rate formatting and the system snapshot.

`psutil` is faked so snapshots are instant and deterministic (the real
`cpu_percent(interval=1)` would block for a second).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sifty.core import monitor

_GB = 1_073_741_824


class _NoSuchProcess(Exception):
    pass


class _AccessDenied(Exception):
    pass


class _DeadProc:
    """A process whose .info access raises, like a vanished/locked process."""

    def __init__(self, exc):
        self._exc = exc

    @property
    def info(self):
        raise self._exc


class _FakePsutil:
    NoSuchProcess = _NoSuchProcess
    AccessDenied = _AccessDenied

    def __init__(self):
        self.cpu = 12.5
        self.mem = SimpleNamespace(used=8 * _GB, total=16 * _GB, percent=50.0)
        self.disk = SimpleNamespace(read_bytes=1000, write_bytes=2000)
        self.net = SimpleNamespace(bytes_sent=500, bytes_recv=700)
        self.procs: list = []
        self.disk_raises = False
        self.net_raises = False
        self.iter_raises = False

    def cpu_percent(self, interval=None):
        return self.cpu

    def virtual_memory(self):
        return self.mem

    def disk_io_counters(self):
        if self.disk_raises:
            raise RuntimeError("disk counters unavailable")
        return self.disk

    def net_io_counters(self):
        if self.net_raises:
            raise RuntimeError("net counters unavailable")
        return self.net

    def process_iter(self, attrs=None):
        if self.iter_raises:
            raise RuntimeError("process_iter failed")
        return list(self.procs)


@pytest.fixture
def fake_psutil(monkeypatch):
    fake = _FakePsutil()
    monkeypatch.setattr(monitor, "psutil", fake)
    monkeypatch.setattr(monitor, "_last_disk_io", None)
    monkeypatch.setattr(monitor, "_last_net_io", None)
    return fake


def _proc(pid, name, cpu, rss):
    mem = SimpleNamespace(rss=rss) if rss is not None else None
    return SimpleNamespace(
        info={"pid": pid, "name": name, "cpu_percent": cpu, "memory_info": mem}
    )


# --- fmt_rate --------------------------------------------------------------


def test_fmt_rate_units():
    assert monitor.fmt_rate(0, 2.0) == "0 B/s"
    assert monitor.fmt_rate(2000, 2.0) == "1000 B/s"
    assert monitor.fmt_rate(4096, 2.0) == "2.0 KB/s"
    assert monitor.fmt_rate(4_194_304, 2.0) == "2.0 MB/s"
    assert monitor.fmt_rate(4_294_967_296, 2.0) == "2.00 GB/s"


def test_fmt_rate_default_interval():
    # default interval is 2.0s → 1024 bytes / 2s = 512 B/s
    assert monitor.fmt_rate(1024) == "512 B/s"


def test_fmt_rate_zero_interval_does_not_divide_by_zero():
    assert monitor.fmt_rate(1000, 0.0).endswith("/s")


# --- snapshot --------------------------------------------------------------


def test_snapshot_basic_fields(fake_psutil):
    snap = monitor.snapshot()
    assert snap.cpu_percent == 12.5
    assert snap.memory_total_gb == 16.0
    assert snap.memory_used_gb == 8.0
    assert snap.memory_percent == 50.0


def test_snapshot_first_call_has_zero_io_deltas(fake_psutil):
    snap = monitor.snapshot()
    assert snap.disk_read_bytes == 0
    assert snap.disk_write_bytes == 0
    assert snap.net_sent_bytes == 0
    assert snap.net_recv_bytes == 0


def test_snapshot_computes_io_deltas(fake_psutil):
    monitor.snapshot()  # establishes baseline
    fake_psutil.disk = SimpleNamespace(read_bytes=1500, write_bytes=2200)
    fake_psutil.net = SimpleNamespace(bytes_sent=900, bytes_recv=1700)
    snap = monitor.snapshot()
    assert snap.disk_read_bytes == 500
    assert snap.disk_write_bytes == 200
    assert snap.net_sent_bytes == 400
    assert snap.net_recv_bytes == 1000


def test_snapshot_clamps_negative_deltas_on_counter_reset(fake_psutil):
    monitor.snapshot()
    fake_psutil.disk = SimpleNamespace(read_bytes=10, write_bytes=10)  # counters reset
    snap = monitor.snapshot()
    assert snap.disk_read_bytes == 0
    assert snap.disk_write_bytes == 0


def test_snapshot_handles_missing_io_counters(fake_psutil):
    fake_psutil.disk = None
    fake_psutil.net = None
    snap = monitor.snapshot()
    assert snap.disk_read_bytes == 0
    assert snap.net_recv_bytes == 0


def test_snapshot_tolerates_io_counter_errors(fake_psutil):
    fake_psutil.disk_raises = True
    fake_psutil.net_raises = True
    snap = monitor.snapshot()
    assert snap.disk_read_bytes == 0
    assert snap.net_sent_bytes == 0


def test_snapshot_ranks_and_limits_processes(fake_psutil):
    fake_psutil.procs = [
        _proc(1, "low", 5.0, 1_048_576),
        _proc(2, "high", 50.0, 2_097_152),
        _proc(3, "mid", 20.0, None),
    ]
    snap = monitor.snapshot(top_procs=2)
    assert [p.name for p in snap.processes] == ["high", "mid"]
    assert snap.processes[0].memory_mb == 2.0
    assert snap.processes[1].memory_mb == 0.0  # memory_info was None


def test_snapshot_coerces_none_process_fields(fake_psutil):
    fake_psutil.procs = [_proc(None, None, None, None)]
    snap = monitor.snapshot()
    p = snap.processes[0]
    assert p.pid == 0
    assert p.name == ""
    assert p.cpu_percent == 0.0


def test_snapshot_skips_vanished_processes(fake_psutil):
    fake_psutil.procs = [
        _DeadProc(_NoSuchProcess()),
        _DeadProc(_AccessDenied()),
        _proc(1, "alive", 1.0, None),
    ]
    snap = monitor.snapshot()
    assert [p.name for p in snap.processes] == ["alive"]


def test_snapshot_tolerates_process_iter_failure(fake_psutil):
    fake_psutil.iter_raises = True
    snap = monitor.snapshot()
    assert snap.processes == []
