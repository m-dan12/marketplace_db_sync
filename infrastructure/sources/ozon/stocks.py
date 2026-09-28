from __future__ import annotations

from typing import Any

import httpx

from domain.models import OzonStockLine
from infrastructure.sources.ozon.client import OZON_BASE, ozon_headers
from shared.http_retry import request_with_retry


class OzonStocksSource:
    """`POST /v4/product/info/stocks` — cursor-paginated; each `offer_id`
    can carry several `stocks[]` rows (one per warehouse type, FBO/FBS)."""

    def __init__(self, client_id: str, api_key: str) -> None:
        self._client_id = client_id
        self._api_key = api_key

    def fetch(self, account: str) -> list[OzonStockLine]:
        headers = ozon_headers(self._client_id, self._api_key)
        items: list[dict[str, Any]] = []
        cursor = ""
        with httpx.Client(timeout=60.0) as client:
            while True:
                body: dict[str, Any] = {"filter": {"visibility": "ALL"}, "limit": 1000}
                if cursor:
                    body["cursor"] = cursor
                response = request_with_retry(
                    client, "POST", f"{OZON_BASE}/v4/product/info/stocks", headers=headers, json=body,
                )
                data = response.json()
                batch = data.get("items", [])
                items.extend(batch)
                cursor = data.get("cursor", "")
                if not cursor or not batch:
                    break
        return [line for item in items for line in _parse_stock_item(item)]


def _parse_stock_item(item: dict[str, Any]) -> list[OzonStockLine]:
    return [
        OzonStockLine(
            offer_id=item.get("offer_id"),
            product_id=item.get("product_id"),
            sku=stock.get("sku"),
            stock_type=stock.get("type"),
            present=stock.get("present"),
            reserved=stock.get("reserved"),
        )
        for stock in item.get("stocks", []) or []
    ]
