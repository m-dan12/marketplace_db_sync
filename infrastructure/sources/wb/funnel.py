from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any, Optional

import httpx

from domain.models import WbFunnelLine
from infrastructure.config.settings import FUNNEL_WINDOW_DAYS
from infrastructure.sources.wb.client import wb_headers
from shared.http_retry import request_with_retry

FUNNEL_URL = "https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products"
_PAGE_LIMIT = 1000
_MIN_INTERVAL_SECONDS = 20.5  # the endpoint allows 3 requests a minute


class WBFunnelSource:
    """Sales funnel per product for one day at a time
    (`POST /api/analytics/v3/sales-funnel/products`, a one-day period,
    1000 products per page). `fetch` re-reads the last `window_days` days
    ending yesterday; `fetch_day` reads exactly one (used by the backfill)."""

    def __init__(
        self,
        api_key: str,
        window_days: int = FUNNEL_WINDOW_DAYS,
        today: Optional[date] = None,
        min_interval_seconds: float = _MIN_INTERVAL_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._window_days = window_days
        self._today = today
        self._min_interval = min_interval_seconds
        self._last_request = float("-inf")  # the first request never waits

    def fetch(self, account: str) -> list[WbFunnelLine]:
        today = self._today or date.today()
        days = [today - timedelta(days=n) for n in range(self._window_days, 0, -1)]
        lines: list[WbFunnelLine] = []
        with httpx.Client(timeout=60.0) as client:
            for day in days:
                lines.extend(self._day(client, day))
        return lines

    def fetch_day(self, day: date) -> list[WbFunnelLine]:
        with httpx.Client(timeout=60.0) as client:
            return self._day(client, day)

    def _day(self, client: httpx.Client, day: date) -> list[WbFunnelLine]:
        headers = wb_headers(self._api_key)
        lines: list[WbFunnelLine] = []
        offset = 0
        while True:
            self._throttle()
            response = request_with_retry(
                client, "POST", FUNNEL_URL, headers=headers,
                json={
                    "selectedPeriod": {"start": day.isoformat(), "end": day.isoformat()},
                    "limit": _PAGE_LIMIT, "offset": offset,
                },
            )
            products = ((response.json().get("data") or {}).get("products")) or []
            lines.extend(parse_funnel_product(item, day) for item in products)
            if len(products) < _PAGE_LIMIT:
                return lines
            offset += _PAGE_LIMIT

    def _throttle(self) -> None:
        wait = self._min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()


def parse_funnel_product(item: dict[str, Any], day: date) -> WbFunnelLine:
    product = item.get("product") or {}
    stat = ((item.get("statistic") or {}).get("selected")) or {}
    return WbFunnelLine(
        day=day,
        nm_id=product.get("nmId"),
        vendor_code=product.get("vendorCode"),
        subject_name=product.get("subjectName"),
        open_count=stat.get("openCount"),
        cart_count=stat.get("cartCount"),
        order_count=stat.get("orderCount"),
        order_sum=stat.get("orderSum"),
        buyout_count=stat.get("buyoutCount"),
        buyout_sum=stat.get("buyoutSum"),
        cancel_count=stat.get("cancelCount"),
        cancel_sum=stat.get("cancelSum"),
        add_to_wishlist=stat.get("addToWishlist"),
        product_rating=product.get("productRating"),
        feedback_rating=product.get("feedbackRating"),
    )
