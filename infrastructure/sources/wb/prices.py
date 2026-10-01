from __future__ import annotations

from typing import Any

import httpx

from domain.models import WbPriceLine
from infrastructure.sources.wb.client import wb_headers
from shared.http_retry import request_with_retry

PRICES_BASE = "https://discounts-prices-api.wildberries.ru"
_PAGE_LIMIT = 1000


class WBPricesSource:
    """`GET /api/v2/list/goods/filter` — prices and discounts, offset-paginated.
    The discount is per product, the price per size."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def fetch(self, account: str) -> list[WbPriceLine]:
        headers = wb_headers(self._api_key)
        goods: list[dict[str, Any]] = []
        offset = 0
        with httpx.Client(timeout=60.0) as client:
            while True:
                response = request_with_retry(
                    client, "GET", f"{PRICES_BASE}/api/v2/list/goods/filter",
                    headers=headers, params={"limit": _PAGE_LIMIT, "offset": offset},
                )
                batch = (response.json().get("data") or {}).get("listGoods") or []
                goods.extend(batch)
                if len(batch) < _PAGE_LIMIT:
                    break
                offset += _PAGE_LIMIT
        return parse_goods(goods)


def parse_goods(goods: list[dict[str, Any]]) -> list[WbPriceLine]:
    return [
        WbPriceLine(
            nm_id=item.get("nmID"),
            vendor_code=item.get("vendorCode"),
            tech_size=size.get("techSizeName"),
            size_id=size.get("sizeID"),
            price=size.get("price"),
            discount=item.get("discount"),
            discounted_price=size.get("discountedPrice"),
        )
        for item in goods
        for size in item.get("sizes") or []
    ]
