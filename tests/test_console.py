"""Tests for shared console formatting helpers."""

import pytest

from sifty.console import human_size


@pytest.mark.parametrize(
    ("num_bytes", "expected"),
    [
        (0, "0 B"),
        (999, "999 B"),
        (1023, "1023 B"),
        (1024, "1.0 KB"),
        (5000, "4.9 KB"),
        (1024**2, "1.0 MB"),
        (1024**3, "1.0 GB"),
        (1023 * 1024**4, "1,023.0 TB"),
    ],
)
def test_human_size_unit_boundaries(num_bytes: int, expected: str) -> None:
    assert human_size(num_bytes) == expected
