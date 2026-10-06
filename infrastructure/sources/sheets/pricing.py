"""The pricing workbook: the margin model (cost and limits per product model) and,
per Wildberries cabinet, a sheet with every card, its brand, category, cost and price range."""
from __future__ import annotations

from typing import Any, Sequence

from domain.models import CostModelLine, WbArticlePricingLine
from domain.production import normalize_brand, parse_int, parse_number
from infrastructure.sources.sheets.client import SheetReader
from infrastructure.sources.sheets.common import SheetFormatError, cell, columns, text

COST_MODEL_SHEET = "модель маржи"
# Price-range sheet of each Wildberries cabinet.
WB_PRICING_SHEETS = {
    "skazka": "диапазон профтекс",
    "milky_garden": "диапазон милки",
    "timeless": "диапазон таймлес",
}
_HEADER_ROW = 3  # 'ключ модели' row of the margin model; the data starts after the sub-header


def parse_cost_models(rows: Sequence[Sequence[Any]]) -> list[CostModelLine]:
    """Fixed positions (the header has blank cells): B key, C fabric price, D price type,
    E base price, F total cost, G Ozon limit discount, H WB max discount, I minimum price."""
    header = rows[_HEADER_ROW] if len(rows) > _HEADER_ROW else []
    if str(cell(header, 1)).strip() != "ключ модели" or "СЕБЕСТОИМОСТЬ" not in str(cell(header, 5)):
        raise SheetFormatError(f"sheet {COST_MODEL_SHEET!r}: expected 'ключ модели' in B and 'СЕБЕСТОИМОСТЬ ИТОГО' in F")
    result: dict[str, CostModelLine] = {}
    for row in rows[_HEADER_ROW + 1 :]:
        key = text(row, 1)
        if not key or not key.startswith("/"):
            continue
        result[key] = CostModelLine(
            model_key=key,
            fabric_price=parse_number(cell(row, 2)),
            price_type=text(row, 3),
            base_price=parse_number(cell(row, 4)),
            cost_total=parse_number(cell(row, 5)),
            ozon_limit_discount=parse_number(cell(row, 6)),
            wb_max_discount=parse_number(cell(row, 7)),
            min_price=parse_number(cell(row, 8)),
        )
    return list(result.values())


_WB_REQUIRED = (
    "Бренд", "Категория", "Артикул WB", "Артикул продавца", "БАЗОВАЯ ЦЕНА", "начало рабочего диапазона",
    "конец рабочего диапазона", "предельная цена", "предельная скидка", "старт новинки", "Себестоимость по модели",
)


def parse_wb_pricing(rows: Sequence[Sequence[Any]], sheet: str = "") -> list[WbArticlePricingLine]:
    """The model key joins the 'ключ' (size key) and 'тип цены' (fabric type) columns, as the sheet's own lookup does."""
    if not rows:
        raise SheetFormatError(f"sheet {sheet!r} is empty")
    col = columns(rows[0], _WB_REQUIRED, sheet)
    result: dict[str, WbArticlePricingLine] = {}
    for row in rows[1:]:
        article = text(row, col["Артикул продавца"])
        if not article:
            continue
        key_part, type_part = text(row, col["ключ"]) if "ключ" in col else None, text(row, col["тип цены"]) if "тип цены" in col else None
        result[article] = WbArticlePricingLine(
            nm_id=parse_int(cell(row, col["Артикул WB"])),
            article=article,
            brand=normalize_brand(cell(row, col["Бренд"])),
            category=text(row, col["Категория"]),
            model_key=(key_part + type_part) if key_part and type_part else None,
            cost=parse_number(cell(row, col["Себестоимость по модели"])),
            base_price=parse_number(cell(row, col["БАЗОВАЯ ЦЕНА"])),
            range_start=parse_number(cell(row, col["начало рабочего диапазона"])),
            range_end=parse_number(cell(row, col["конец рабочего диапазона"])),
            limit_price=parse_number(cell(row, col["предельная цена"])),
            limit_discount=parse_number(cell(row, col["предельная скидка"])),
            launch_discount=parse_number(cell(row, col["старт новинки"])),
        )
    return list(result.values())


class WbPricingSheetSource:
    """Plays the `MarketplaceSource` role: the sheet of the requested cabinet."""

    def __init__(self, reader: SheetReader, spreadsheet_id: str) -> None:
        self._reader = reader
        self._spreadsheet_id = spreadsheet_id

    def fetch(self, account: str) -> list[WbArticlePricingLine]:
        title = WB_PRICING_SHEETS[account]
        lines = parse_wb_pricing(self._reader.read(self._spreadsheet_id, title), title)
        if not lines:
            raise SheetFormatError(f"sheet {title!r} has no cards")
        return lines
