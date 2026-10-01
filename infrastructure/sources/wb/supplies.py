from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any, Optional

import httpx

from domain.models import SupplyBundle, SupplyItemLine, SupplyLine
from infrastructure.config.settings import SUPPLIES_WINDOW_DAYS
from infrastructure.sources.parsing import parse_datetime
from infrastructure.sources.wb.client import wb_headers
from shared.http_retry import request_with_retry

logger = logging.getLogger(__name__)

SUPPLIES_BASE = "https://supplies-api.wildberries.ru/api/v1/supplies"
_PAGE_LIMIT = 1000
_PAUSE_SECONDS = 2.1  # the supplies API allows about 30 requests a minute

WB_SUPPLY_STATUSES = {
    1: "not_planned",
    2: "planned",
    3: "unloading_allowed",
    4: "acceptance_in_progress",
    5: "accepted",
    6: "unloaded_at_gate",
}


class WBSuppliesSource:
    """FBW supplies of one cabinet: the list (`POST /api/v1/supplies`), then
    the header (`GET /{id}`) and the goods (`GET /{id}/goods`) of every
    supply. Nightly runs re-read supplies *updated* in the last
    `window_days`; a backfill passes `created_from` to walk the whole history.
    """

    def __init__(
        self,
        api_key: str,
        window_days: int = SUPPLIES_WINDOW_DAYS,
        created_from: Optional[date] = None,
        today: Optional[date] = None,
        pause_seconds: float = _PAUSE_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._window_days = window_days
        self._created_from = created_from
        self._today = today
        self._pause = pause_seconds

    def fetch(self, account: str) -> list[SupplyBundle]:
        headers = wb_headers(self._api_key)
        today = self._today or date.today()
        if self._created_from is not None:
            dates = [{"from": self._created_from.isoformat(), "till": today.isoformat(), "type": "createDate"}]
        else:
            since = today - timedelta(days=self._window_days)
            dates = [{"from": since.isoformat(), "till": today.isoformat(), "type": "updatedDate"}]
        bundles: list[SupplyBundle] = []
        with httpx.Client(timeout=60.0) as client:
            for listed in self._list(client, headers, dates):
                bundles.append(self._bundle(client, headers, listed))
        return bundles

    def _list(self, client: httpx.Client, headers: dict[str, str], dates: list[dict]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            response = request_with_retry(
                client, "POST", SUPPLIES_BASE, headers=headers,
                params={"limit": _PAGE_LIMIT, "offset": offset}, json={"dates": dates},
            )
            page = response.json() or []
            out.extend(page)
            if len(page) < _PAGE_LIMIT:
                return out
            offset += _PAGE_LIMIT
            time.sleep(self._pause)

    def _bundle(self, client: httpx.Client, headers: dict[str, str], listed: dict[str, Any]) -> SupplyBundle:
        supply_id, preorder_id = listed.get("supplyID"), listed.get("preorderID")
        by_preorder = supply_id is None
        ident = preorder_id if by_preorder else supply_id
        params = {"isPreorderID": "true" if by_preorder else "false"}
        detail: dict[str, Any] = {}
        goods: list[dict[str, Any]] = []
        try:
            time.sleep(self._pause)
            detail = request_with_retry(client, "GET", f"{SUPPLIES_BASE}/{ident}", headers=headers, params=params).json()
            goods = self._goods(client, headers, ident, params)
        except httpx.HTTPStatusError as exc:
            # A supply that cannot be read (deleted, not yet a supply): keep
            # the header from the list, no items — and say so.
            logger.warning("WB supply %s: details unavailable (%s)", ident, exc.response.status_code)
        return parse_supply_bundle(listed, detail, goods)

    def _goods(
        self, client: httpx.Client, headers: dict[str, str], ident: Any, params: dict[str, str]
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            time.sleep(self._pause)
            page = request_with_retry(
                client, "GET", f"{SUPPLIES_BASE}/{ident}/goods", headers=headers,
                params={**params, "limit": _PAGE_LIMIT, "offset": offset},
            ).json() or []
            out.extend(page)
            if len(page) < _PAGE_LIMIT:
                return out
            offset += _PAGE_LIMIT


def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) else None


def parse_supply_bundle(
    listed: dict[str, Any], detail: dict[str, Any], goods: list[dict[str, Any]]
) -> SupplyBundle:
    merged = {**listed, **{k: v for k, v in detail.items() if v is not None}}
    supply_id, preorder_id = merged.get("supplyID"), merged.get("preorderID")
    key = f"s{supply_id}" if supply_id is not None else f"p{preorder_id}"
    status_id = merged.get("statusID")
    supply = SupplyLine(
        marketplace="wb",
        supply_key=key,
        order_id=str(preorder_id) if preorder_id is not None else None,
        created_at=parse_datetime(merged.get("createDate")),
        planned_date=parse_datetime(merged.get("supplyDate")),
        fact_date=parse_datetime(merged.get("factDate")),
        updated_at=parse_datetime(merged.get("updatedDate")),
        status=WB_SUPPLY_STATUSES.get(status_id, str(status_id) if status_id is not None else None),
        warehouse_name=merged.get("warehouseName"),
        actual_warehouse_name=merged.get("actualWarehouseName") or None,
        transit_warehouse_name=merged.get("transitWarehouseName") or None,
        is_crossdock=None,
        quantity=_num(merged.get("quantity")),
        accepted_quantity=_num(merged.get("acceptedQuantity")),
        ready_for_sale_quantity=_num(merged.get("readyForSaleQuantity")),
    )
    items = tuple(
        SupplyItemLine(
            item_key=str(g.get("barcode") or g.get("nmID") or g.get("vendorCode")),
            article=g.get("vendorCode"),
            nm_id=g.get("nmID"),
            sku=None,
            barcode=g.get("barcode"),
            tech_size=g.get("techSize"),
            quantity=_num(g.get("quantity")),
            accepted_quantity=_num(g.get("acceptedQuantity")),
            ready_for_sale_quantity=_num(g.get("readyForSaleQuantity")),
        )
        for g in goods
    )
    return SupplyBundle(supply=supply, items=items)
