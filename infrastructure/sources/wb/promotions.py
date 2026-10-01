from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from domain.models import PromotionBundle, PromotionItemLine, PromotionLine
from infrastructure.config.settings import (
    PROMOTIONS_CALENDAR_AHEAD_DAYS,
    PROMOTIONS_CALENDAR_START,
    PROMOTIONS_ITEMS_LOOKBACK_DAYS,
)
from infrastructure.sources.parsing import parse_datetime
from infrastructure.sources.wb.client import wb_headers
from shared.http_retry import request_with_retry

logger = logging.getLogger(__name__)

CALENDAR_URL = "https://dp-calendar-api.wildberries.ru/api/v1/calendar/promotions"
_CALENDAR_PAGE = 100
_DETAILS_BATCH = 100
_ITEMS_PAGE = 1000
_PAUSE_SECONDS = 0.7  # 10 requests per 6 seconds are allowed
_DESCRIPTION_LIMIT = 1000


class WBPromotionsSource:
    """WB promotions calendar. The list covers `calendar_start` .. today+ahead;
    the details come in batches; the participating products are read only for
    *regular* promotions that have not ended earlier than `items_lookback_days`
    ago (auto promotions have no product list: the API answers 422)."""

    def __init__(
        self,
        api_key: str,
        calendar_start: str = PROMOTIONS_CALENDAR_START,
        ahead_days: int = PROMOTIONS_CALENDAR_AHEAD_DAYS,
        items_lookback_days: int = PROMOTIONS_ITEMS_LOOKBACK_DAYS,
        items_from: Optional[date] = None,
        today: Optional[date] = None,
        pause_seconds: float = _PAUSE_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._calendar_start = calendar_start
        self._ahead_days = ahead_days
        self._items_lookback_days = items_lookback_days
        self._items_from = items_from
        self._today = today
        self._pause = pause_seconds

    def fetch(self, account: str) -> list[PromotionBundle]:
        headers = wb_headers(self._api_key)
        today = self._today or date.today()
        items_cutoff = self._items_from or (today - timedelta(days=self._items_lookback_days))
        with httpx.Client(timeout=60.0) as client:
            listed = self._calendar(client, headers, today)
            details = self._details(client, headers, [p["id"] for p in listed])
            bundles: list[PromotionBundle] = []
            for promo in listed:
                items: list[PromotionItemLine] = []
                ends = parse_datetime(promo.get("endDateTime"))
                if promo.get("type") == "regular" and ends is not None and ends.date() >= items_cutoff:
                    items = self._items(client, headers, promo["id"])
                bundles.append(PromotionBundle(parse_promotion(promo, details.get(promo["id"])), tuple(items)))
        return bundles

    def _calendar(self, client: httpx.Client, headers: dict[str, str], today: date) -> list[dict[str, Any]]:
        end = today + timedelta(days=self._ahead_days)
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            response = request_with_retry(
                client, "GET", CALENDAR_URL, headers=headers,
                params={
                    "startDateTime": f"{self._calendar_start}T00:00:00Z",
                    "endDateTime": f"{end.isoformat()}T00:00:00Z",
                    "allPromo": "true", "limit": _CALENDAR_PAGE, "offset": offset,
                },
            )
            page = ((response.json().get("data") or {}).get("promotions")) or []
            out.extend(page)
            if len(page) < _CALENDAR_PAGE:
                return out
            offset += _CALENDAR_PAGE
            time.sleep(self._pause)

    def _details(self, client: httpx.Client, headers: dict[str, str], ids: list[int]) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        for i in range(0, len(ids), _DETAILS_BATCH):
            time.sleep(self._pause)
            response = request_with_retry(
                client, "GET", f"{CALENDAR_URL}/details", headers=headers,
                params={"promotionIDs": ids[i : i + _DETAILS_BATCH]},
            )
            for promo in ((response.json().get("data") or {}).get("promotions")) or []:
                out[promo["id"]] = promo
        return out

    def _items(self, client: httpx.Client, headers: dict[str, str], promo_id: int) -> list[PromotionItemLine]:
        out: list[PromotionItemLine] = []
        offset = 0
        while True:
            time.sleep(self._pause)
            try:
                response = request_with_retry(
                    client, "GET", f"{CALENDAR_URL}/nomenclatures", headers=headers,
                    params={"promotionID": promo_id, "inAction": "true", "limit": _ITEMS_PAGE, "offset": offset},
                )
            except httpx.HTTPStatusError as exc:
                logger.warning("WB promotion %s: no product list (%s)", promo_id, exc.response.status_code)
                return out
            page = ((response.json().get("data") or {}).get("nomenclatures")) or []
            out.extend(parse_nomenclature(n) for n in page)
            if len(page) < _ITEMS_PAGE:
                return out
            offset += _ITEMS_PAGE


def parse_promotion(promo: dict[str, Any], detail: Optional[dict[str, Any]]) -> PromotionLine:
    detail = detail or {}
    in_total = detail.get("inPromoActionTotal")
    out_total = detail.get("notInPromoActionTotal")
    description = detail.get("description")
    return PromotionLine(
        marketplace="wb",
        promo_id=str(promo["id"]),
        name=promo.get("name"),
        promo_type=promo.get("type"),
        start_at=parse_datetime(promo.get("startDateTime")),
        end_at=parse_datetime(promo.get("endDateTime")),
        description=description[:_DESCRIPTION_LIMIT] if isinstance(description, str) else None,
        potential_count=(in_total + out_total) if isinstance(in_total, int) and isinstance(out_total, int) else None,
        participating_count=in_total if isinstance(in_total, int) else None,
        discount_type=None,
        discount_value=None,
    )


def parse_nomenclature(raw: dict[str, Any]) -> PromotionItemLine:
    return PromotionItemLine(
        item_id=raw["id"],
        in_action=bool(raw.get("inAction")),
        price=raw.get("price"),
        plan_price=raw.get("planPrice"),
        discount=raw.get("discount"),
        plan_discount=raw.get("planDiscount"),
        stock=None,
    )
