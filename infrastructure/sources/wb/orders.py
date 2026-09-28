from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx

from domain.models import WbOrderLine
from infrastructure.config.settings import SYNC_WINDOW_DAYS
from infrastructure.sources.parsing import parse_date, parse_datetime
from infrastructure.sources.wb.client import STATISTICS_BASE, wb_headers
from shared.http_retry import request_with_retry


class WBOrdersSource:
    """`GET /api/v1/supplier/orders` — the order feed (not confirmed sales).

    WB does not support `dateTo`: everything in `[dateFrom, now]` comes
    back, so the fixed sync window is implemented purely through `dateFrom`.
    """

    def __init__(self, api_key: str, window_days: int = SYNC_WINDOW_DAYS) -> None:
        self._api_key = api_key
        self._window_days = window_days

    def fetch(self, account: str) -> list[WbOrderLine]:
        date_from = date.today() - timedelta(days=self._window_days)
        headers = wb_headers(self._api_key)
        with httpx.Client(timeout=60.0) as client:
            response = request_with_retry(
                client, "GET", f"{STATISTICS_BASE}/api/v1/supplier/orders",
                headers=headers, params={"dateFrom": date_from.isoformat(), "flag": 0},
            )
        return [_parse_order(raw) for raw in response.json()]


def _parse_order(raw: dict[str, Any]) -> WbOrderLine:
    return WbOrderLine(
        srid=raw["srid"],
        order_date=parse_date(raw.get("date")),
        last_change_date=parse_datetime(raw.get("lastChangeDate")),
        warehouse_name=raw.get("warehouseName"),
        region_name=raw.get("regionName"),
        supplier_article=raw.get("supplierArticle"),
        nm_id=raw.get("nmId"),
        barcode=raw.get("barcode"),
        subject=raw.get("subject"),
        brand=raw.get("brand"),
        tech_size=raw.get("techSize"),
        total_price=raw.get("totalPrice"),
        discount_percent=raw.get("discountPercent"),
        finished_price=raw.get("finishedPrice"),
        price_with_disc=raw.get("priceWithDisc"),
        is_cancel=bool(raw.get("isCancel", False)),
        g_number=raw.get("gNumber"),
    )
