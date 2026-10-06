from __future__ import annotations

import time
from typing import Any

import httpx

from domain.models import ProductCardLine
from infrastructure.sources.wb.client import wb_headers
from shared.http_retry import request_with_retry

CONTENT_BASE = "https://content-api.wildberries.ru"
_PAGE_LIMIT = 100
_PAUSE_SECONDS = 0.7  # the content API allows 100 requests a minute


class WBCardsSource:
    """`POST /content/v2/get/cards/list` — every card of the cabinet (also those without orders
    or stock), cursor-paginated: brand, title and subject (category) of each."""

    def __init__(self, api_key: str, pause_seconds: float = _PAUSE_SECONDS) -> None:
        self._api_key = api_key
        self._pause_seconds = pause_seconds

    def fetch(self, account: str) -> list[ProductCardLine]:
        headers = wb_headers(self._api_key)
        cards: list[dict[str, Any]] = []
        cursor: dict[str, Any] = {"limit": _PAGE_LIMIT}
        with httpx.Client(timeout=60.0) as client:
            while True:
                response = request_with_retry(
                    client, "POST", f"{CONTENT_BASE}/content/v2/get/cards/list", headers=headers,
                    params={"locale": "ru"},
                    json={"settings": {"cursor": cursor, "filter": {"withPhoto": -1}}},
                )
                data = response.json()
                batch = data.get("cards") or []
                cards.extend(batch)
                page = data.get("cursor") or {}
                if len(batch) < _PAGE_LIMIT or not page.get("nmID"):
                    break
                cursor = {"limit": _PAGE_LIMIT, "updatedAt": page.get("updatedAt"), "nmID": page.get("nmID")}
                time.sleep(self._pause_seconds)
        return parse_cards(cards)


def parse_cards(cards: list[dict[str, Any]]) -> list[ProductCardLine]:
    return [
        ProductCardLine(
            marketplace="wb",
            article=card["vendorCode"],
            external_id=card.get("nmID"),
            title=card.get("title"),
            brand=card.get("brand") or None,
            category=card.get("subjectName"),
            category_id=card.get("subjectID"),
        )
        for card in cards
        if card.get("vendorCode")
    ]
