"""The production table ("в производстве", sheet "база данных"): one row per
article and distribution region inside a task."""
from __future__ import annotations

import hashlib
import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any, Sequence

from domain.models import ProductionLine
from domain.production import (
    normalize_brand,
    normalize_direction,
    normalize_region,
    normalize_status,
    normalize_workshop,
    parse_dates,
    parse_int,
    parse_number,
    status_group,
)
from infrastructure.sources.sheets.client import SheetReader
from infrastructure.sources.sheets.common import SheetFormatError, cell, columns, text

logger = logging.getLogger("marketplace_db_sync")

SHEET_TITLE = "база данных"

# Header names are case-sensitive: "количество" is the row quantity and
# "Количество" the task quantity.
_REQUIRED = (
    "Артикул", "количество", "регион", "заказ", "размер", "метраж", "бренд", "цех", "метраж2",
    "Номер недели", "Направление", "количество всего", "Ключ", "Количество", "Факт количество",
    "Факт дата отгрузки", "Факт дата приемки", "статус", "начало недели", "конец недели", "№ приемки",
)


@dataclass(frozen=True)
class ProductionParse:
    lines: list[ProductionLine]
    issues: dict[str, int]  # what was skipped or could not be recognised, by reason


def _as_text(value: Any) -> Any:
    """Cells that hold a number but are an identifier ('№ приемки' 41268617.0)."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _row_key(parts: Sequence[Any], occurrence: int) -> str:
    """Stable id of a row that does not depend on its position in the sheet
    nor on the fields people update later (fact, status, receipt number);
    identical rows are told apart by their order of appearance."""
    digest = hashlib.md5("|".join("" if p is None else str(p) for p in parts).encode("utf-8")).hexdigest()
    return f"{digest}:{occurrence}"


def parse_production_rows(rows: Sequence[Sequence[Any]]) -> ProductionParse:
    if not rows:
        raise SheetFormatError(f"sheet {SHEET_TITLE!r} is empty")
    col = columns(rows[0], _REQUIRED, SHEET_TITLE)
    issues: Counter[str] = Counter()
    seen: Counter[tuple] = Counter()
    lines: list[ProductionLine] = []

    for offset, row in enumerate(rows[1:]):
        sheet_row = offset + 2
        if not any(str(c).strip() for c in row if c is not None):
            continue
        article = text(row, col["Артикул"])
        if not article:
            issues["skipped: no article"] += 1
            continue

        week = parse_dates(cell(row, col["начало недели"]))
        week_start = week.last
        week_end = parse_dates(cell(row, col["конец недели"])).last
        if week_start is None:
            issues["week start not recognised"] += 1

        ship = parse_dates(cell(row, col["Факт дата отгрузки"]), week_start)
        accept = parse_dates(cell(row, col["Факт дата приемки"]), week_start)
        if ship.invalid:
            issues["ship date: typo / out of range"] += 1
        if accept.invalid:
            issues["accept date: typo / out of range"] += 1
        if len(ship.dates) > 1:
            issues["ship date: several dates (last one used)"] += 1
        if len(accept.dates) > 1:
            issues["accept date: several dates (last one used)"] += 1

        direction_raw = text(row, col["Направление"])
        direction = normalize_direction(direction_raw)
        if direction is None:
            issues["direction not recognised"] += 1
        quantity = parse_int(cell(row, col["количество"]))
        if quantity is None:
            issues["row quantity missing"] += 1
        meters2 = parse_number(cell(row, col["метраж2"]))  # also holds order file names: those are ignored

        status = normalize_status(cell(row, col["статус"]))
        group = status_group(status)
        if group == "other":
            issues["status not recognised"] += 1

        order_text = text(row, col["заказ"]) or ""
        task_key = text(row, col["Ключ"]) or ""
        region = normalize_region(cell(row, col["регион"]))
        brand = normalize_brand(cell(row, col["бренд"]))
        workshop = normalize_workshop(cell(row, col["цех"]))
        identity = (task_key, order_text, article, region, direction_raw, week_start, quantity, workshop, brand)
        seen[identity] += 1

        lines.append(
            ProductionLine(
                row_key=_row_key(identity, seen[identity]),
                sheet_row=sheet_row,
                article=article,
                quantity=quantity,
                region=region,
                order_text=order_text,
                size_text=text(row, col["размер"]),
                meters=parse_number(cell(row, col["метраж"])),
                meters2=meters2,
                week_number=parse_int(cell(row, col["Номер недели"])),
                direction=direction,
                task_total=parse_int(cell(row, col["количество всего"])),
                task_key=task_key,
                task_quantity=parse_int(cell(row, col["Количество"])),
                fact_quantity=parse_int(cell(row, col["Факт количество"])),
                fact_ship_date=ship.last,
                fact_ship_raw=_as_text(cell(row, col["Факт дата отгрузки"])),
                fact_accept_date=accept.last,
                fact_accept_raw=_as_text(cell(row, col["Факт дата приемки"])),
                status=status,
                status_group=group,
                week_start=week_start,
                week_end=week_end,
                receipt_no=_as_text(cell(row, col["№ приемки"])),
                brand=brand,
                workshop=workshop,
            )
        )
    return ProductionParse(lines, dict(issues))


class ProductionSheetSource:
    """Reads the whole production table. The account is irrelevant (the table
    covers every cabinet), the use case is run under the pseudo-account 'all'."""

    def __init__(self, reader: SheetReader, spreadsheet_id: str) -> None:
        self._reader = reader
        self._spreadsheet_id = spreadsheet_id

    def fetch(self, account: str) -> list[ProductionLine]:
        parsed = parse_production_rows(self._reader.read(self._spreadsheet_id, SHEET_TITLE))
        if not parsed.lines:
            raise SheetFormatError(f"sheet {SHEET_TITLE!r}: no data rows")
        logger.info("production sheet: %d rows read; problems: %s", len(parsed.lines), parsed.issues or "none")
        return parsed.lines
