from __future__ import annotations

from typing import Any

import httpx

from domain.models import PromotionBundle, PromotionItemLine, PromotionLine
from infrastructure.sources.ozon.client import OZON_BASE, ozon_headers
from infrastructure.sources.parsing import parse_datetime
from shared.http_retry import request_with_retry

_ITEMS_PAGE = 1000


class OzonActionsSource:
    """`GET /v1/actions` lists only current and upcoming actions (no history
    in the API), so the history builds up in the DB night by night. The
    products taking part are read for actions that have any
    (`POST /v1/actions/products`)."""

    def __init__(self, client_id: str, api_key: str) -> None:
        self._client_id = client_id
        self._api_key = api_key

    def fetch(self, account: str) -> list[PromotionBundle]:
        headers = ozon_headers(self._client_id, self._api_key)
        bundles: list[PromotionBundle] = []
        with httpx.Client(timeout=60.0) as client:
            actions = request_with_retry(
                client, "GET", f"{OZON_BASE}/v1/actions", headers=headers
            ).json().get("result") or []
            for action in actions:
                items: list[PromotionItemLine] = []
                if action.get("participating_products_count"):
                    items = self._products(client, headers, action["id"])
                bundles.append(PromotionBundle(parse_action(action), tuple(items)))
        return bundles

    def _products(self, client: httpx.Client, headers: dict[str, str], action_id: int) -> list[PromotionItemLine]:
        out: list[PromotionItemLine] = []
        offset = 0
        while True:
            result = request_with_retry(
                client, "POST", f"{OZON_BASE}/v1/actions/products", headers=headers,
                json={"action_id": action_id, "limit": _ITEMS_PAGE, "offset": offset},
            ).json().get("result") or {}
            page = result.get("products") or []
            out.extend(parse_action_product(p) for p in page)
            offset += _ITEMS_PAGE
            if len(page) < _ITEMS_PAGE or offset >= (result.get("total") or 0):
                return out


def parse_action(action: dict[str, Any]) -> PromotionLine:
    return PromotionLine(
        marketplace="ozon",
        promo_id=str(action["id"]),
        name=action.get("title"),
        promo_type=action.get("action_type"),
        start_at=parse_datetime(action.get("date_start")),
        end_at=parse_datetime(action.get("date_end")),
        description=(action.get("description") or None) and action["description"][:1000],
        potential_count=action.get("potential_products_count"),
        participating_count=action.get("participating_products_count"),
        discount_type=action.get("discount_type"),
        discount_value=action.get("discount_value"),
    )


def parse_action_product(raw: dict[str, Any]) -> PromotionItemLine:
    return PromotionItemLine(
        item_id=raw["id"],
        in_action=True,
        price=raw.get("price"),
        plan_price=raw.get("action_price"),
        discount=None,
        plan_discount=None,
        stock=raw.get("stock"),
    )
