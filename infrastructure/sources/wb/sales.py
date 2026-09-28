from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx

from domain.models import WbSaleLine
from infrastructure.config.settings import SYNC_WINDOW_DAYS
from infrastructure.sources.parsing import parse_date, parse_datetime
from infrastructure.sources.wb.client import STATISTICS_BASE, wb_headers
from shared.http_retry import request_with_retry


class WBSalesSource:
    """`GET /api/v1/supplier/sales` — confirmed buyouts, a separate entity
    from the order feed. Same `dateFrom`-only windowing as orders."""

    def __init__(self, api_key: str, window_days: int = SYNC_WINDOW_DAYS) -> None:
        self._api_key = api_key
        self._window_days = window_days

    def fetch(self, account: str) -> list[WbSaleLine]:
        date_from = date.today() - timedelta(days=self._window_days)
        headers = wb_headers(self._api_key)
        with httpx.Client(timeout=60.0) as client:
            response = request_with_retry(
                client, "GET", f"{STATISTICS_BASE}/api/v1/supplier/sales",
                headers=headers, params={"dateFrom": date_from.isoformat(), "flag": 0},
            )
        return [_parse_sale(raw) for raw in response.json()]


def _parse_sale(raw: dict[str, Any]) -> WbSaleLine:
    return WbSaleLine(
        sale_id=raw["saleID"],
        sale_date=parse_date(raw.get("date")),
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
        spp=raw.get("spp"),
        for_pay=raw.get("forPay"),
        finished_price=raw.get("finishedPrice"),
        price_with_disc=raw.get("priceWithDisc"),
        order_type=raw.get("orderType"),
        g_number=raw.get("gNumber"),
    )
