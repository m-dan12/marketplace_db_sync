"""The weekly "План поставок" sheets: fabric handed to the workshops, task by task.

One sheet per week ('21.09-27.09 26'). A sheet is a list of task blocks:

    номер задания | 68 | ... | 'неделя 39 №39_00068(АМ) цех Солях Анна'     <- task row
    24.09.2026    | Солях | ... | supplier document | metres | amount             <- invoice row ("Дата вх,")
    (blank) | price | design | metres | amount | invoice | | fabric no | fabric | brand   <- fabric lines

Only the left block (columns A-K) is read: it is the same in the 42- and the 52-column layouts, while
the blocks on the right (claims, losses) move from week to week.
"""
from __future__ import annotations

import re
import time
from datetime import date
from typing import Any, Optional, Sequence

from domain.models import FabricReceiptLine
from domain.production import normalize_brand, normalize_workshop, parse_dates, parse_number
from infrastructure.sources.sheets.client import SheetReader
from infrastructure.sources.sheets.common import SheetFormatError, cell, text

_TITLE = re.compile(r"^(\d{1,2})\.(\d{1,2})\s*-\s*\d{1,2}\.\d{1,2}\s+(\d{2})$")
_DATE_ONLY = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{2,4}$")
_HEADER_ROW = 1  # the second row holds the column names
# column A is called "Дата вх,", "Дата документа" or nothing, depending on the week
_HEADER = {1: "цех", 4: "метраж", 8: "№ ткани", 9: "ткань", 10: "бренд"}
_TASK_LABEL = "номер задания"
_TASK_TEXT_COLUMN = 13


def week_start_of(title: str) -> Optional[date]:
    """'21.09-27.09 26' -> 2026-09-21; None for any other sheet."""
    match = _TITLE.match(title.strip())
    if not match:
        return None
    try:
        return date(2000 + int(match[3]), int(match[2]), int(match[1]))
    except ValueError:
        return None


def _invoice_date(value: Any) -> Optional[date]:
    """A cell that is only a date (typed text or a sheet date number); free text with a date inside is not."""
    if isinstance(value, str) and not _DATE_ONLY.match(value.strip()):
        return None
    if value is None or isinstance(value, bool):
        return None
    return parse_dates(value).last


def parse_week(rows: Sequence[Sequence[Any]], title: str, week_start: date) -> list[FabricReceiptLine]:
    header = rows[_HEADER_ROW] if len(rows) > _HEADER_ROW else []
    wrong = [pos for pos, name in _HEADER.items() if str(cell(header, pos) or "").strip().casefold() != name]
    if wrong:
        raise SheetFormatError(f"sheet {title!r}: unexpected header in columns {wrong}")

    lines: list[FabricReceiptLine] = []
    task_number: Optional[str] = None
    task_text: Optional[str] = None
    invoice: Optional[tuple[date, Optional[str], Optional[str]]] = None  # date, workshop, header text
    for number, row in enumerate(rows[_HEADER_ROW + 1 :], start=_HEADER_ROW + 2):
        first = text(row, 0)
        if first and first.casefold() == _TASK_LABEL:
            task_number, task_text, invoice = text(row, 1), text(row, _TASK_TEXT_COLUMN), None
            continue
        received = _invoice_date(cell(row, 0))
        if received is not None:
            invoice = (received, normalize_workshop(cell(row, 1)), text(row, 3))
            continue
        meters = parse_number(cell(row, 4))
        if invoice is None or not meters or meters <= 0:
            continue
        lines.append(FabricReceiptLine(
            sheet=title,
            sheet_row=number,
            week_start=week_start,
            task_number=task_number,
            task_text=task_text,
            received_date=invoice[0],
            workshop=invoice[1],
            supplier_text=first or invoice[2],
            price=parse_number(cell(row, 2)),
            nomenclature=text(row, 3),
            meters=meters,
            amount=parse_number(cell(row, 5)),
            document=text(row, 6),
            fabric_no=text(row, 8),
            fabric_name=text(row, 9),
            brand=normalize_brand(cell(row, 10)),
        ))
    return lines


class FabricReceiptsSheetSource:
    """Plays the `MarketplaceSource` role: every weekly sheet of the supply-plan workbook."""

    def __init__(self, reader: SheetReader, spreadsheet_id: str, pause_seconds: float = 1.0) -> None:
        self._reader = reader
        self._spreadsheet_id = spreadsheet_id
        self._pause_seconds = pause_seconds  # the Sheets API allows about 60 reads a minute

    def fetch(self, account: str) -> list[FabricReceiptLine]:
        weeks = [(t, w) for t in self._reader.titles(self._spreadsheet_id) if (w := week_start_of(t))]
        if not weeks:
            raise SheetFormatError("no weekly sheets found in the supply-plan workbook")
        lines: list[FabricReceiptLine] = []
        for i, (title, week_start) in enumerate(weeks):
            if i:
                time.sleep(self._pause_seconds)
            lines.extend(parse_week(self._reader.read(self._spreadsheet_id, title), title, week_start))
        return lines
