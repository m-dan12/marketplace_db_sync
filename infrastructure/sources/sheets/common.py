from __future__ import annotations

from typing import Any, Optional, Sequence


class SheetFormatError(Exception):
    """The sheet no longer looks the way the parser expects (a column was
    renamed or moved). Failing is better than loading shifted columns."""


def columns(header: Sequence[Any], required: Sequence[str], sheet: str) -> dict[str, int]:
    """Column index by header name (the first one when a name repeats). The exact spelling wins;
    only a name that is not found as typed is looked up regardless of case ('Ключ' became 'ключ'
    once), because some sheets hold two columns that differ by case alone ('количество' / 'Количество')."""
    index: dict[str, int] = {}
    folded: dict[str, int] = {}
    for position, name in enumerate(header):
        index.setdefault(str(name).strip(), position)
        folded.setdefault(str(name).strip().casefold(), position)
    for name in required:
        if name not in index and name.casefold() in folded:
            index[name] = folded[name.casefold()]
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
