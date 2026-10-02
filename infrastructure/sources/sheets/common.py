from __future__ import annotations

from typing import Any, Optional, Sequence


class SheetFormatError(Exception):
    """The sheet no longer looks the way the parser expects (a column was
    renamed or moved). Failing is better than loading shifted columns."""


def columns(header: Sequence[Any], required: Sequence[str], sheet: str) -> dict[str, int]:
    """Column index by exact header name (the first one when a name repeats)."""
    index: dict[str, int] = {}
    for position, name in enumerate(header):
        index.setdefault(str(name).strip(), position)
    missing = [name for name in required if name not in index]
    if missing:
        raise SheetFormatError(f"sheet {sheet!r}: columns not found: {missing}")
    return index


def cell(row: Sequence[Any], position: int) -> Any:
    return row[position] if position < len(row) else None


def text(row: Sequence[Any], position: int) -> Optional[str]:
    value = cell(row, position)
    if value is None:
        return None
    result = str(value).strip()
    return result or None
