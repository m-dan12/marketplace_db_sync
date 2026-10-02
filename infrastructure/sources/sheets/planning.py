"""Reference sheets of the analyst's planning workbook: packing multiples,
fabric consumption per article, the fabric dictionary and the fabric stock."""
from __future__ import annotations

import logging
from typing import Any, Callable, Generic, Sequence, TypeVar

from domain.models import ArticleSpecLine, FabricLine, FabricStockLine, QuantMultipleLine
from domain.production import normalize_brand, parse_int, parse_number
from infrastructure.sources.sheets.client import SheetReader
from infrastructure.sources.sheets.common import SheetFormatError, cell, columns, text

logger = logging.getLogger("marketplace_db_sync")

T = TypeVar("T")

QUANT_SHEET = "кратность кванта"
SPECS_SHEET = "артикулы"
FABRICS_SHEET = "ткани"
FABRIC_STOCK_SHEET = "наличие ткани"


def _id_text(value: Any) -> Any:
    """Fabric numbers come as 110, 110.0 or '110'."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    result = str(value).strip()
    return result or None


def parse_quant_multiples(rows: Sequence[Sequence[Any]]) -> list[QuantMultipleLine]:
    """The key sits in column B and the multiple the formula uses is column K,
    'Финальное V5'. The header has blank cells, so positions are fixed and the
    two anchor headers are checked."""
    if not rows or str(cell(rows[0], 1)).strip() != "ключ" or "V5" not in str(cell(rows[0], 10)):
        raise SheetFormatError(f"sheet {QUANT_SHEET!r}: expected 'ключ' in B and 'Финальное V5' in K")
    result: dict[str, QuantMultipleLine] = {}
    for row in rows[1:]:
        key = text(row, 1)
        if not key or not key.startswith("/"):
            continue
        line = QuantMultipleLine(
            size_key=key,
            name=text(row, 2),
            volume_liters=parse_number(cell(row, 4)),
            fits_in_box=parse_int(cell(row, 5)),
            calculated_in_box=parse_int(cell(row, 6)),
            desired_count=parse_int(cell(row, 7)),
            final_count=parse_int(cell(row, 8)),
            final_v4=parse_int(cell(row, 9)),
            quant=parse_int(cell(row, 10)),
        )
        if key not in result or (result[key].quant is None and line.quant is not None):
            result[key] = line
    return list(result.values())


def parse_article_specs(rows: Sequence[Sequence[Any]]) -> list[ArticleSpecLine]:
    """The sheet has a row per article and marketplace; the consumption of an
    article is the same on each, so the first non-empty value is taken."""
    if not rows:
        raise SheetFormatError(f"sheet {SPECS_SHEET!r} is empty")
    col = columns(rows[0], ("бренд", "ткань1", "ткань2", "артикул", "размер", "м/изд1", "м/изд2", "назначение"), SPECS_SHEET)
    specs: dict[str, dict[str, Any]] = {}
    for row in rows[1:]:
        article = text(row, col["артикул"])
        if not article:
            continue
        spec = specs.setdefault(article, {})
        values = {
            "brand_name": normalize_brand(cell(row, col["бренд"])),
            "fabric_no_1": _id_text(cell(row, col["ткань1"])),
            "fabric_no_2": _id_text(cell(row, col["ткань2"])),
            "meters_per_item_1": parse_number(cell(row, col["м/изд1"])),
            "meters_per_item_2": parse_number(cell(row, col["м/изд2"])),
            "purpose": text(row, col["назначение"]),
            "size_text": text(row, col["размер"]),
        }
        for name, value in values.items():
            if spec.get(name) is None and value is not None:
                spec[name] = value
    empty = dict.fromkeys(
        ("brand_name", "fabric_no_1", "fabric_no_2", "meters_per_item_1", "meters_per_item_2", "purpose", "size_text")
    )
    return [ArticleSpecLine(article=a, **{**empty, **v}) for a, v in specs.items()]


def parse_fabrics(rows: Sequence[Sequence[Any]]) -> list[FabricLine]:
    """Key is the second 'ткань' column (M), the one the planning formulas look up."""
    if not rows or str(cell(rows[0], 12)).strip() != "ткань":
        raise SheetFormatError(f"sheet {FABRICS_SHEET!r}: expected 'ткань' in column M")
    col = columns(
        rows[0],
        ("материал", "ценовая категория", "Длина рулона,м", "Ширина рулона, см", "детям / взрослый", "Цвет",
         "Рисунок для WB", "Тип плетения ткани", "краткое название", "артикул поставщика", "Код поставщика2",
         "Поставщик", "Изделия"),
        FABRICS_SHEET,
    )
    result: dict[str, FabricLine] = {}
    for row in rows[1:]:
        fabric_no = _id_text(cell(row, 12)) or _id_text(cell(row, 0))
        if not fabric_no or fabric_no in result:
            continue
        result[fabric_no] = FabricLine(
            fabric_no=fabric_no,
            material=text(row, col["материал"]),
            price_category=text(row, col["ценовая категория"]),
            roll_length_m=parse_number(cell(row, col["Длина рулона,м"])),
            roll_width_cm=parse_number(cell(row, col["Ширина рулона, см"])),
            audience=text(row, col["детям / взрослый"]),
            color=text(row, col["Цвет"]),
            pattern=text(row, col["Рисунок для WB"]),
            weave=text(row, col["Тип плетения ткани"]),
            short_name=text(row, col["краткое название"]),
            supplier_article=text(row, col["артикул поставщика"]),
            supplier_code=text(row, col["Код поставщика2"]),
            supplier=text(row, col["Поставщик"]),
            products=text(row, col["Изделия"]),
        )
    return list(result.values())


def parse_fabric_stock(rows: Sequence[Sequence[Any]]) -> list[FabricStockLine]:
    if not rows:
        raise SheetFormatError(f"sheet {FABRIC_STOCK_SHEET!r} is empty")
    col = columns(rows[0], ("номер ткани", "количество", "поставщик", "название", "Есть в вывозе"), FABRIC_STOCK_SHEET)
    result = []
    for row in rows[1:]:
        fabric_no = _id_text(cell(row, col["номер ткани"]))
        if not fabric_no:
            continue
        result.append(
            FabricStockLine(
                fabric_no=fabric_no,
                quantity_m=parse_number(cell(row, col["количество"])),
                supplier=text(row, col["поставщик"]),
                name=text(row, col["название"]),
                brand=normalize_brand(cell(row, col["Есть в вывозе"])),
            )
        )
    return result


class SheetTableSource(Generic[T]):
    """One sheet -> a list of lines. An empty result is an error: a sheet that
    parses to nothing has almost certainly changed shape."""

    def __init__(
        self,
        reader: SheetReader,
        spreadsheet_id: str,
        sheet_title: str,
        parser: Callable[[Sequence[Sequence[Any]]], list[T]],
    ) -> None:
        self._reader = reader
        self._spreadsheet_id = spreadsheet_id
        self._sheet_title = sheet_title
        self._parser = parser

    def fetch(self, account: str) -> list[T]:
        items = self._parser(self._reader.read(self._spreadsheet_id, self._sheet_title))
        if not items:
            raise SheetFormatError(f"sheet {self._sheet_title!r}: nothing recognised")
        logger.info("sheet %r: %d rows read", self._sheet_title, len(items))
        return items
