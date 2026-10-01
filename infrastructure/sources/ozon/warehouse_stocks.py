from __future__ import annotations

from typing import Any

import httpx

from domain.models import OzonWarehouseStockLine
from infrastructure.sources.ozon.client import OZON_BASE, ozon_headers
from shared.http_retry import request_with_retry

_PAGE_LIMIT = 1000


class OzonWarehouseStocksSource:
    """`POST /v2/analytics/stock_on_warehouses` — FBO stock per product and
    Ozon warehouse (free to sell / reserved / promised = on the way)."""

    def __init__(self, client_id: str, api_key: str) -> None:
        self._client_id = client_id
        self._api_key = api_key

    def fetch(self, account: str) -> list[OzonWarehouseStockLine]:
        headers = ozon_headers(self._client_id, self._api_key)
        rows: list[dict[str, Any]] = []
        offset = 0
        with httpx.Client(timeout=60.0) as client:
            while True:
                response = request_with_retry(
                    client, "POST", f"{OZON_BASE}/v2/analytics/stock_on_warehouses", headers=headers,
                    json={"limit": _PAGE_LIMIT, "offset": offset, "warehouse_type": "ALL"},
                )
                page = (response.json().get("result") or {}).get("rows") or []
                rows.extend(page)
                if len(page) < _PAGE_LIMIT:
                    break
                offset += _PAGE_LIMIT
        return [parse_warehouse_row(r) for r in rows if r.get("item_code") and r.get("warehouse_name")]


def parse_warehouse_row(raw: dict[str, Any]) -> OzonWarehouseStockLine:
    return OzonWarehouseStockLine(
        offer_id=raw["item_code"],
        sku=raw.get("sku"),
        product_name=raw.get("item_name"),
        warehouse_name=raw["warehouse_name"],
        free_to_sell=raw.get("free_to_sell_amount"),
        reserved=raw.get("reserved_amount"),
        promised=raw.get("promised_amount"),
    )
