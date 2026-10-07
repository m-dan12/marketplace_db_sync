"""The weekly "План поставок" sheets: fabric handed to the workshops, task by task.

One sheet per week ('21.09-27.09 26'). A sheet is a list of task blocks:

    номер задания | 68 | ... | 'неделя 39 №39_00068(АМ) цех Солях Анна'     <- task row
    24.09.2026    | Солях | ... | supplier document | metres | amount | ... | defect and claim cells   <- invoice row
    (blank) | price | design | metres | amount | invoice | | fabric no | fabric | brand   <- fabric lines

The left block (columns A-K) is the same in the 42- and the 52-column layouts. Of the blocks on the
right, which move from week to week, only the claim cells T-Y of the invoice rows are read: defect
status, claim number, claimed metres and amount, claim status, compensation date. The columns after
them hold calculations that are blank in practice, and other tables of the sheet are laid out beside
the task blocks, so the right-hand cells of fabric lines are not read at all.
"""
from __future__ import annotations

import re
import time
from datetime import date
from typing import Any, Iterator, Optional, Sequence

from domain.models import FabricInvoiceLine, FabricReceiptLine
from domain.production import normalize_brand, normalize_workshop, parse_dates, parse_number
from infrastructure.sources.sheets.client import SheetReader
from infrastructure.sources.sheets.common import SheetFormatError, cell, text

_TITLE = re.compile(r"^(\d{1,2})\.(\d{1,2})\s*-\s*\d{1,2}\.\d{1,2}\s+(\d{2})$")
_DATE_ONLY = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{2,4}$")
_HEADER_ROW = 1  # the second row holds the column names
# column A is called "Дата вх,", "Дата документа" or nothing, depending on the week
_HEADER = {1: "цех", 4: "метраж", 8: "№ ткани", 9: "ткань", 10: "бренд"}
_CLAIM_HEADER = {19: "статус", 20: "претензия", 21: "метраж претензии", 22: "сумма претензии"}
_TASK_LABEL = "номер задания"
_TASK_TEXT_COLUMN = 13

_Invoice = tuple[date, Optional[str], Optional[str]]  # date, workshop, text of the invoice row


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


def _check_header(rows: Sequence[Sequence[Any]], title: str) -> None:
    header = rows[_HEADER_ROW] if len(rows) > _HEADER_ROW else []
    wrong = [pos for pos, name in _HEADER.items() if str(cell(header, pos) or "").strip().casefold() != name]
    if wrong:
        raise SheetFormatError(f"sheet {title!r}: unexpected header in columns {wrong}")


def _walk(rows: Sequence[Sequence[Any]]) -> Iterator[tuple[str, int, Sequence[Any], Optional[str], Optional[str], _Invoice]]:
    """('invoice' | 'line', 1-based row number, row, task number, task text, invoice) for every
    invoice row and every fabric line that belongs to one."""
    task_number: Optional[str] = None
    task_text: Optional[str] = None
    invoice: Optional[_Invoice] = None
    for number, row in enumerate(rows[_HEADER_ROW + 1 :], start=_HEADER_ROW + 2):
        first = text(row, 0)
        if first and first.casefold() == _TASK_LABEL:
            task_number, task_text, invoice = text(row, 1), text(row, _TASK_TEXT_COLUMN), None
            continue
        received = _invoice_date(cell(row, 0))
        if received is not None:
            invoice = (received, normalize_workshop(cell(row, 1)), text(row, 3))
            yield "invoice", number, row, task_number, task_text, invoice
            continue
        meters = parse_number(cell(row, 4))
        if invoice is not None and meters and meters > 0:
            yield "line", number, row, task_number, task_text, invoice


def parse_week(rows: Sequence[Sequence[Any]], title: str, week_start: date) -> list[FabricReceiptLine]:
    _check_header(rows, title)
    lines: list[FabricReceiptLine] = []
    for kind, number, row, task_number, task_text, invoice in _walk(rows):
        if kind != "line":
            continue
        lines.append(FabricReceiptLine(
            sheet=title,
            sheet_row=number,
            week_start=week_start,
            task_number=task_number,
            task_text=task_text,
            received_date=invoice[0],
            workshop=invoice[1],
            supplier_text=text(row, 0) or invoice[2],
            price=parse_number(cell(row, 2)),
            nomenclature=text(row, 3),
            meters=parse_number(cell(row, 4)),
            amount=parse_number(cell(row, 5)),
            document=text(row, 6),
            fabric_no=text(row, 8),
            fabric_name=text(row, 9),
            brand=normalize_brand(cell(row, 10)),
        ))
    return lines


def _unless_zero(value: Optional[str]) -> Optional[str]:
    return None if value in (None, "0") else value


def parse_invoices(rows: Sequence[Sequence[Any]], title: str, week_start: date) -> list[FabricInvoiceLine]:
    """Invoice rows with their defect / claim cells. A week whose column names do not show the claim
    block in T-W (a few sheets lack them) gives invoices without claim data."""
    _check_header(rows, title)
    header = rows[_HEADER_ROW]
    has_claims = all(str(cell(header, pos) or "").strip().casefold() == name for pos, name in _CLAIM_HEADER.items())
    invoices: list[FabricInvoiceLine] = []
    for kind, number, row, task_number, _, invoice in _walk(rows):
        if kind != "invoice":
            continue
        compensation_cell = cell(row, 24) if has_claims else None
        # A claim is a numbered one. Amounts without a number belong to other tables of the sheet
        # (four rows of 40 million in one week), so they are not taken.
        claim_no = _unless_zero(text(row, 20)) if has_claims else None
        invoices.append(FabricInvoiceLine(
            sheet=title,
            sheet_row=number,
            week_start=week_start,
            task_number=task_number,
            received_date=invoice[0],
            workshop=invoice[1],
            supplier_text=invoice[2],
            meters=parse_number(cell(row, 4)),
            amount=parse_number(cell(row, 5)),
            defect_status=_unless_zero(text(row, 19)) if has_claims else None,
            claim_no=claim_no,
            claim_meters=parse_number(cell(row, 21)) if claim_no else None,
            claim_amount=parse_number(cell(row, 22)) if claim_no else None,
            claim_status=_unless_zero(text(row, 23)) if has_claims else None,
            compensation_date=parse_dates(compensation_cell, reference=week_start).last if compensation_cell else None,
        ))
    return invoices


class _WeeklySheets:
    """Reads the weekly sheets once and hands the parsed rows to both sources."""

    def __init__(self, reader: SheetReader, spreadsheet_id: str, pause_seconds: float) -> None:
        self._reader = reader
        self._spreadsheet_id = spreadsheet_id
        self._pause_seconds = pause_seconds  # the Sheets API allows about 60 reads a minute
        self._parsed: Optional[tuple[list[FabricReceiptLine], list[FabricInvoiceLine]]] = None

    def parsed(self) -> tuple[list[FabricReceiptLine], list[FabricInvoiceLine]]:
        if self._parsed is None:
            weeks = [(t, w) for t in self._reader.titles(self._spreadsheet_id) if (w := week_start_of(t))]
            if not weeks:
                raise SheetFormatError("no weekly sheets found in the supply-plan workbook")
            lines: list[FabricReceiptLine] = []
            invoices: list[FabricInvoiceLine] = []
            for i, (title, week_start) in enumerate(weeks):
                if i:
                    time.sleep(self._pause_seconds)
                rows = self._reader.read(self._spreadsheet_id, title)
                lines.extend(parse_week(rows, title, week_start))
                invoices.extend(parse_invoices(rows, title, week_start))
            self._parsed = (lines, invoices)
        return self._parsed


class FabricReceiptsSheetSource:
    """Plays the `MarketplaceSource` role: every weekly sheet of the supply-plan workbook."""

    def __init__(self, reader: SheetReader, spreadsheet_id: str, pause_seconds: float = 1.0) -> None:
        self._sheets = _WeeklySheets(reader, spreadsheet_id, pause_seconds)

    def fetch(self, account: str) -> list[FabricReceiptLine]:
        return self._sheets.parsed()[0]

    def invoices(self) -> "FabricInvoicesSheetSource":
        """The invoice rows of the same sheets; the sheets are read once for both sources."""
        return FabricInvoicesSheetSource(self._sheets)


class FabricInvoicesSheetSource:
    def __init__(self, sheets: _WeeklySheets) -> None:
        self._sheets = sheets

    def fetch(self, account: str) -> list[FabricInvoiceLine]:
        return self._sheets.parsed()[1]
