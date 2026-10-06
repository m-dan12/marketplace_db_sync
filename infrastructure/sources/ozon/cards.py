from __future__ import annotations

from typing import Any, Optional

import httpx

from domain.models import ProductCardLine
from infrastructure.sources.ozon.client import OZON_BASE, ozon_headers
from shared.http_retry import request_with_retry

_LIST_LIMIT = 1000
_INFO_BATCH = 1000


class OzonCardsSource:
    """Every product of the cabinet (`/v3/product/list`), its name and category ids
    (`/v3/product/info/list`) and the names of those categories (`/v1/description-category/tree`)."""

    def __init__(self, client_id: str, api_key: str) -> None:
        self._client_id = client_id
        self._api_key = api_key

    def fetch(self, account: str) -> list[ProductCardLine]:
        headers = ozon_headers(self._client_id, self._api_key)
        with httpx.Client(timeout=120.0) as client:
            offer_ids = self._offer_ids(client, headers)
            names = self._category_names(client, headers)
            items: list[dict[str, Any]] = []
            for i in range(0, len(offer_ids), _INFO_BATCH):
                response = request_with_retry(
                    client, "POST", f"{OZON_BASE}/v3/product/info/list", headers=headers,
                    json={"offer_id": offer_ids[i : i + _INFO_BATCH]},
                )
                data = response.json()
                items.extend(data.get("items") or (data.get("result") or {}).get("items") or [])
        return parse_cards(items, names)

    def _offer_ids(self, client: httpx.Client, headers: dict[str, str]) -> list[str]:
        offer_ids: list[str] = []
        last_id = ""
        while True:
            response = request_with_retry(
                client, "POST", f"{OZON_BASE}/v3/product/list", headers=headers,
                json={"filter": {"visibility": "ALL"}, "last_id": last_id, "limit": _LIST_LIMIT},
            )
            result = response.json().get("result") or {}
            batch = result.get("items") or []
            offer_ids.extend(item["offer_id"] for item in batch if item.get("offer_id"))
            last_id = result.get("last_id") or ""
            if not batch or not last_id:
                return offer_ids

    def _category_names(self, client: httpx.Client, headers: dict[str, str]) -> dict[tuple[int, int], str]:
        response = request_with_retry(
            client, "POST", f"{OZON_BASE}/v1/description-category/tree", headers=headers,
            json={"language": "DEFAULT"},
        )
        return flatten_category_tree(response.json().get("result") or [])


def flatten_category_tree(
    nodes: list[dict[str, Any]], parent_category: Optional[tuple[int, str]] = None
) -> dict[tuple[int, int], str]:
    """(description_category_id, type_id) -> 'category > type'. A node with a `type_id` is a
    product type under the nearest category above it."""
    names: dict[tuple[int, int], str] = {}
    for node in nodes:
        if node.get("type_id") is not None and parent_category is not None:
            names[(parent_category[0], node["type_id"])] = f"{parent_category[1]} > {node.get('type_name')}"
        category = node.get("description_category_id")
        if category is not None:
            names.update(flatten_category_tree(node.get("children") or [], (category, node.get("category_name"))))
        else:
            names.update(flatten_category_tree(node.get("children") or [], parent_category))
    return names


def parse_cards(items: list[dict[str, Any]], names: dict[tuple[int, int], str]) -> list[ProductCardLine]:
    cards = []
    for item in items:
        if not item.get("offer_id"):
            continue
        category_id = item.get("description_category_id")
        cards.append(ProductCardLine(
            marketplace="ozon",
            article=item["offer_id"],
            external_id=item.get("id"),
            title=item.get("name"),
            brand=None,
            category=names.get((category_id, item.get("type_id"))),
            category_id=category_id,
        ))
    return cards
