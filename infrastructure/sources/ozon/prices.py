from __future__ import annotations

from typing import Any

import httpx

from domain.models import OzonPriceLine
from infrastructure.sources.ozon.client import OZON_BASE, ozon_headers
from shared.http_retry import request_with_retry


class OzonPricesSource:
    """`POST /v5/product/info/prices` — cursor-paginated."""

    def __init__(self, client_id: str, api_key: str) -> None:
        self._client_id = client_id
        self._api_key = api_key

    def fetch(self, account: str) -> list[OzonPriceLine]:
        headers = ozon_headers(self._client_id, self._api_key)
        items: list[dict[str, Any]] = []
        cursor = ""
        with httpx.Client(timeout=60.0) as client:
            while True:
                body: dict[str, Any] = {"filter": {"visibility": "ALL"}, "limit": 1000}
                if cursor:
                    body["cursor"] = cursor
                response = request_with_retry(
                    client, "POST", f"{OZON_BASE}/v5/product/info/prices", headers=headers, json=body,
                )
                data = response.json()
                batch = data.get("items", [])
                items.extend(batch)
                cursor = data.get("cursor", "")
                if not cursor or not batch:
                    break
        return [parse_price_item(item) for item in items]


def parse_price_item(item: dict[str, Any]) -> OzonPriceLine:
    price = item.get("price") or {}
    return OzonPriceLine(
        offer_id=item.get("offer_id"),
        product_id=item.get("product_id"),
        price=price.get("price"),
        old_price=price.get("old_price"),
        min_price=price.get("min_price"),
        marketing_seller_price=price.get("marketing_seller_price"),
    )
