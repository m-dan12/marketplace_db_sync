from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from domain.models import SupplyBundle, SupplyItemLine, SupplyLine
from infrastructure.config.settings import OZON_SUPPLY_RECENT_PAGES
from infrastructure.sources.ozon.client import OZON_BASE, ozon_headers
from infrastructure.sources.parsing import parse_datetime
from shared.http_retry import request_with_retry

logger = logging.getLogger(__name__)

ACTIVE_STATES = [
    "DATA_FILLING", "READY_TO_SUPPLY", "ACCEPTED_AT_SUPPLY_WAREHOUSE", "IN_TRANSIT",
    "ACCEPTANCE_AT_STORAGE_WAREHOUSE", "REPORTS_CONFIRMATION_AWAITING", "REPORT_REJECTED",
]
ARCHIVE_STATES = ["COMPLETED", "CANCELLED", "REJECTED_AT_SUPPLY_WAREHOUSE", "OVERDUE"]
_LIST_LIMIT = 100
_GET_BATCH = 50
_BUNDLE_LIMIT = 100


class OzonSuppliesSource:
    """FBO supply orders of one cabinet: ids (`/v3/supply-order/list`, newest
    first), orders (`/v3/supply-order/get`, 50 at a time) and the content of
    every supply's bundle (`/v1/supply-order/bundle`).

    Nightly (`full=False`): every order in an *active* state (their state and
    content still change) plus the newest `recent_pages` pages of all orders.
    Backfill (`full=True`): every order ever."""

    def __init__(
        self,
        client_id: str,
        api_key: str,
        full: bool = False,
        recent_pages: int = OZON_SUPPLY_RECENT_PAGES,
    ) -> None:
        self._client_id = client_id
        self._api_key = api_key
        self._full = full
        self._recent_pages = recent_pages

    def fetch(self, account: str) -> list[SupplyBundle]:
        headers = ozon_headers(self._client_id, self._api_key)
        with httpx.Client(timeout=60.0) as client:
            ids = self._order_ids(client, headers)
            orders = self._orders(client, headers, ids)
            bundles: list[SupplyBundle] = []
            for order in orders:
                bundles.extend(self._order_bundles(client, headers, order))
        return bundles

    def _order_ids(self, client: httpx.Client, headers: dict[str, str]) -> list[int]:
        everything = ACTIVE_STATES + ARCHIVE_STATES
        ids = self._list(client, headers, everything, None if self._full else self._recent_pages)
        if not self._full:
            ids += self._list(client, headers, ACTIVE_STATES, None)
        return list(dict.fromkeys(ids))

    def _list(
        self, client: httpx.Client, headers: dict[str, str], states: list[str], max_pages: Optional[int]
    ) -> list[int]:
        out: list[int] = []
        last_id = ""
        pages = 0
        while True:
            body: dict[str, Any] = {
                "filter": {"states": states}, "limit": _LIST_LIMIT,
                "sort_by": "ORDER_CREATION", "sort_dir": "DESC",
            }
            if last_id:
                body["last_id"] = last_id
            data = request_with_retry(
                client, "POST", f"{OZON_BASE}/v3/supply-order/list", headers=headers, json=body
            ).json()
            batch = data.get("order_ids") or []
            out.extend(batch)
            last_id = data.get("last_id") or ""
            pages += 1
            if not batch or not last_id or (max_pages and pages >= max_pages):
                return out

    def _orders(self, client: httpx.Client, headers: dict[str, str], ids: list[int]) -> list[dict[str, Any]]:
        orders: list[dict[str, Any]] = []
        for i in range(0, len(ids), _GET_BATCH):
            data = request_with_retry(
                client, "POST", f"{OZON_BASE}/v3/supply-order/get", headers=headers,
                json={"order_ids": ids[i : i + _GET_BATCH]},
            ).json()
            orders.extend(data.get("orders") or [])
        return orders

    def _order_bundles(
        self, client: httpx.Client, headers: dict[str, str], order: dict[str, Any]
    ) -> list[SupplyBundle]:
        supplies = order.get("supplies") or [{}]
        result: list[SupplyBundle] = []
        for supply in supplies:
            items = self._bundle_items(client, headers, supply.get("bundle_id")) if supply.get("bundle_id") else []
            result.append(parse_order_supply(order, supply, items))
        return result

    def _bundle_items(
        self, client: httpx.Client, headers: dict[str, str], bundle_id: str
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        last_id = ""
        while True:
            body: dict[str, Any] = {"bundle_ids": [bundle_id], "limit": _BUNDLE_LIMIT}
            if last_id:
                body["last_id"] = last_id
            try:
                data = request_with_retry(
                    client, "POST", f"{OZON_BASE}/v1/supply-order/bundle", headers=headers, json=body
                ).json()
            except httpx.HTTPStatusError as exc:
                logger.warning("Ozon bundle %s unavailable (%s)", bundle_id, exc.response.status_code)
                return items
            items.extend(data.get("items") or [])
            last_id = data.get("last_id") or ""
            if not data.get("has_next") or not last_id:
                return items


def parse_order_supply(
    order: dict[str, Any], supply: dict[str, Any], raw_items: list[dict[str, Any]]
) -> SupplyBundle:
    order_id = order.get("order_id")
    supply_id = supply.get("supply_id")
    storage = supply.get("storage_warehouse") or {}
    drop_off = order.get("drop_off_warehouse") or {}
    slot = ((order.get("timeslot") or {}).get("timeslot")) or {}
    items = tuple(
        SupplyItemLine(
            item_key=str(i.get("barcode") or i.get("sku") or i.get("offer_id")),
            article=i.get("offer_id"),
            nm_id=None,
            sku=i.get("sku"),
            barcode=i.get("barcode"),
            tech_size=None,
            quantity=float(i["quantity"]) if isinstance(i.get("quantity"), (int, float)) else None,
            accepted_quantity=None,
            ready_for_sale_quantity=None,
        )
        for i in raw_items
    )
    total = sum(i.quantity or 0 for i in items) if items else None
    supply_line = SupplyLine(
        marketplace="ozon",
        supply_key=f"s{supply_id}" if supply_id is not None else f"o{order_id}",
        order_id=str(order_id) if order_id is not None else None,
        created_at=parse_datetime(order.get("created_date")),
        planned_date=parse_datetime(slot.get("from")),
        fact_date=None,
        updated_at=parse_datetime(order.get("state_updated_date")),
        status=supply.get("state") or order.get("state"),
        warehouse_name=storage.get("name") or drop_off.get("name"),
        actual_warehouse_name=storage.get("name"),
        transit_warehouse_name=drop_off.get("name") if supply.get("is_crossdock") else None,
        is_crossdock=supply.get("is_crossdock"),
        quantity=total,
        accepted_quantity=None,
        ready_for_sale_quantity=None,
    )
    return SupplyBundle(supply=supply_line, items=items)
