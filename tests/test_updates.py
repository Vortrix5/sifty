"""Tests for the fragile winget upgrade-table parser."""

from __future__ import annotations

from sifty.core.updates import parse_upgrade_table

SAMPLE = """\
Name                     Id                        Version      Available    Source
-----------------------------------------------------------------------------------
Mozilla Firefox          Mozilla.Firefox           120.0        121.0        winget
Visual Studio Code       Microsoft.VisualStudioCode 1.85.0      1.86.0       winget
7-Zip                    7zip.7zip                 22.01        23.01        winget
"""


def test_parses_all_rows():
    rows = parse_upgrade_table(SAMPLE)
    assert len(rows) == 3


def test_parses_fields():
    rows = parse_upgrade_table(SAMPLE)
    firefox = rows[0]
    assert firefox.name == "Mozilla Firefox"
    assert firefox.id == "Mozilla.Firefox"
    assert firefox.current == "120.0"
    assert firefox.available == "121.0"


def test_empty_output_returns_empty():
    assert parse_upgrade_table("No installed package found.") == []


# The layout from issue #37: a single short-named app, so winget's trailing
# "1 upgrades available." line is wider than the Name column and used to be
# parsed as an extra row.
NARROW_WITH_SUMMARY = """\
Name               Id                                Version   Available Source
-------------------------------------------------------------------------------
PDF-XChange Editor TrackerSoftware.PDF-XChangeEditor 11.0.0.0  11.0.1.0  winget
1 upgrades available.
"""


def test_summary_line_is_not_counted_as_an_upgrade():
    rows = parse_upgrade_table(NARROW_WITH_SUMMARY)
    assert len(rows) == 1
    assert rows[0].id == "TrackerSoftware.PDF-XChangeEditor"


def test_trailing_summary_after_wide_table_is_ignored():
    rows = parse_upgrade_table(SAMPLE + "3 upgrades available.\n")
    assert len(rows) == 3


def test_second_table_section_prose_is_ignored():
    """winget appends a pinned/explicit-targeting section after the table."""
    extra = (
        "\n"
        "The following packages have an upgrade available, but require "
        "explicit targeting for upgrade:\n"
        "Name                     Id                        Version      "
        "Available    Source\n"
        "-----------------------------------------------------------------\n"
        "Some Pinned App          Vendor.Pinned             1.0          "
        "2.0          winget\n"
    )
    rows = parse_upgrade_table(SAMPLE + extra)
    assert [r.id for r in rows] == [
        "Mozilla.Firefox", "Microsoft.VisualStudioCode", "7zip.7zip",
        "Vendor.Pinned",
    ]
