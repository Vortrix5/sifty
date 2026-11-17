"""Application updates via winget (engine).

winget has no stable machine-readable output, so we parse its fixed-column
table. The parser is isolated and unit-tested so the fragile bit is covered.
"""

from __future__ import annotations

from ..windows import winget
from .models import Upgrade

__all__ = ["Upgrade", "parse_upgrade_table", "list_upgrades", "apply_upgrades"]


def parse_upgrade_table(output: str) -> list[Upgrade]:
    """Parse the column layout of ``winget upgrade`` into structured rows.

    winget aligns columns by character offset under a ``Name  Id  Version
    Available  Source`` header, with a dashed separator line beneath it.
    """
    lines = output.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if "Name" in line and "Id" in line and "Available" in line:
            header_idx = i
            break
    if header_idx is None:
        return []

    header = lines[header_idx]
    cols = {
        "name": header.index("Name"),
        "id": header.index("Id"),
        "version": header.index("Version"),
        "available": header.index("Available"),
    }
    src = header.find("Source")
    avail_end = src if src != -1 else len(header) + 200

    def slice_at(line: str, start: int, end: int) -> str:
        return line[start:end].strip()

    def is_row(line: str) -> bool:
        """True if the line is aligned to the table's columns.

        winget prints trailing prose after the table ("37 upgrades
        available.", or a second "require explicit targeting" section).
        Those lines are not padded to the column offsets, so a real row has
        to reach the Version column, have padding before the Id column, and
        carry a package id (never spaces) under Id.
        """
        if len(line) <= cols["version"]:
            return False
        if not line[cols["id"] - 1].isspace():
            return False
        return " " not in line[cols["id"]:cols["version"]].strip()

    upgrades: list[Upgrade] = []
    for line in lines[header_idx + 1:]:
        if not line.strip() or set(line.strip()) <= {"-"}:
            continue
        if not is_row(line):
            continue
        name = slice_at(line, cols["name"], cols["id"])
        ident = slice_at(line, cols["id"], cols["version"])
        current = slice_at(line, cols["version"], cols["available"])
        available = slice_at(line, cols["available"], avail_end)
        if name == "Name" and ident == "Id":
            continue  # repeated header of a second table section
        if name and ident:
            upgrades.append(Upgrade(name, ident, current, available))
    return upgrades


def list_upgrades() -> list[Upgrade]:
    """Return the apps that have updates available (via winget)."""
    return parse_upgrade_table(winget.upgrade_list())


def apply_upgrades(upgrade_id: str | None = None) -> int:
    """Apply updates via winget (a single id, or all). Returns the exit code."""
    return winget.upgrade(upgrade_id)
