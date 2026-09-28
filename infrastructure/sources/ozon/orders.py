from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from domain.models import OzonOrderLine
from infrastructure.config.settings import SYNC_WINDOW_DAYS
from infrastructure.sources.ozon.client import OZON_BASE, ozon_headers
from infrastructure.sources.parsing import parse_datetime
from shared.http_retry import request_with_retry

# (endpoint, key holding the postings list in the response, our label for it)
_ENDPOINTS = (
    ("v3/posting/fbs/list", "postings", "fbs"),
    ("v2/posting/fbo/list", "result", "fbo"),
)
_PAGE_LIMIT = 1000


class OzonOrdersSource:
    """Combines FBS (`v3/posting/fbs/list`) and FBO (`v2/posting/fbo/list`)
    postings into one table; a posting can carry several `products[]`, each
    becoming its own row (natural key: posting_number + offer_id)."""

    def __init__(self, client_id: str, api_key: str, window_days: int = SYNC_WINDOW_DAYS) -> None:
        self._client_id = client_id
        self._api_key = api_key
        self._window_days = window_days

    def fetch(self, account: str) -> list[OzonOrderLine]:
        headers = ozon_headers(self._client_id, self._api_key)
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=self._window_days)
        rows: list[OzonOrderLine] = []
        with httpx.Client(timeout=60.0) as client:
            for endpoint, result_key, source_label in _ENDPOINTS:
                rows.extend(
                    self._fetch_endpoint(client, headers, endpoint, result_key, source_label, date_from, date_to)
                )
        return rows

    def _fetch_endpoint(
        self,
        client: httpx.Client,
        headers: dict[str, str],
        endpoint: str,
        result_key: str,
        source_label: str,
        date_from: datetime,
        date_to: datetime,
    ) -> list[OzonOrderLine]:
        rows: list[OzonOrderLine] = []
        offset = 0
        while True:
            body = {
                "dir": "ASC",
                "filter": {
                    "since": date_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "to": date_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                "limit": _PAGE_LIMIT, "offset": offset, "with": {"analytics_data": True},
            }
            response = request_with_retry(client, "POST", f"{OZON_BASE}/{endpoint}", headers=headers, json=body)
            data = response.json()
            result = data.get("result", data)
            batch = result.get(result_key, []) if isinstance(result, dict) else result
            if not batch:
                break
            for posting in batch:
                rows.extend(_parse_posting(posting, source_label))
            if len(batch) < _PAGE_LIMIT:
                break
            offset += _PAGE_LIMIT
        return rows


def _parse_posting(raw: dict[str, Any], source_label: str) -> list[OzonOrderLine]:
    analytics = raw.get("analytics_data") or {}
    common = dict(
        posting_number=raw.get("posting_number"),
        status=raw.get("status"),
        order_date=_parse_order_date(raw),
        source=source_label,
        warehouse_name=analytics.get("warehouse_name") or analytics.get("warehouse"),
        city=analytics.get("city"),
        region=analytics.get("region"),
    )
    products = raw.get("products") or []
    if not products:
        return [OzonOrderLine(offer_id="", sku=None, product_name=None, quantity=None, price=None, currency=None, **common)]
    return [
        OzonOrderLine(
            offer_id=product.get("offer_id"),
            sku=product.get("sku"),
            product_name=product.get("name"),
            quantity=product.get("quantity"),
            price=product.get("price"),
            currency=product.get("currency_code"),
            **common,
        )
        for product in products
    ]


def _parse_order_date(raw: dict[str, Any]):
    return parse_datetime(raw.get("order_date") or raw.get("created_at") or raw.get("in_process_at"))
