from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Any, Optional

import httpx

from domain.models import SelsupMovementLine
from infrastructure.config.settings import SELSUP_MOVEMENTS_WINDOW_DAYS
from infrastructure.sources.parsing import parse_datetime
from infrastructure.sources.selsup.stocks import SELSUP_BASE
from shared.http_retry import request_with_retry

INCOME_OPS = ("PUT", "CONFIRMED", "FOUND")
OUTCOME_OPS = ("TAKE", "TAKE_MARKETPLACE", "SALE_PRODUCT")
_PAGE_SIZE = 500
_PRODUCT_LOOKUP_BATCH = 200


class SelsupMovementsSource:
    """`GET /api/wms/findItemHistory` per operation — receipts and shipments.
    Newer records sit at the end (id grows with time), so each operation is
    read from its last page backwards until a page is older than `since`.
    Selsup history carries no organization, so `fetch` ignores `account` and
    the result is cached for the run (one instance serves all accounts)."""

    def __init__(
        self,
        token: str,
        window_days: int = SELSUP_MOVEMENTS_WINDOW_DAYS,
        today: Optional[date] = None,
    ) -> None:
        self._token = token
        self._window_days = window_days
        self._today = today
        self._cache: Optional[list[SelsupMovementLine]] = None

    def fetch(self, account: str) -> list[SelsupMovementLine]:
        if self._cache is None:
            self._cache = self._fetch_all()
        return self._cache

    def _fetch_all(self) -> list[SelsupMovementLine]:
        headers = {"Authorization": self._token}
        since = (self._today or date.today()) - timedelta(days=self._window_days)
        raw_rows: list[dict[str, Any]] = []
        with httpx.Client(timeout=60.0) as client:
            for ops, group in ((INCOME_OPS, "Приёмка"), (OUTCOME_OPS, "Отгрузка")):
                for op in ops:
                    for row in self._rows_since(client, headers, op, since):
                        row["_group"] = group
                        raw_rows.append(row)
            sku_ids = {(r.get("item") or {}).get("skuId") for r in raw_rows}
            info = self._product_info(client, headers, {s for s in sku_ids if s})
        return [parse_movement(r, info) for r in raw_rows]

    def _rows_since(
        self, client: httpx.Client, headers: dict[str, str], op: str, since: date
    ) -> list[dict[str, Any]]:
        first = self._page(client, headers, op, 1)
        total = first.get("total") or 0
        if not total:
            return []
        collected: list[dict[str, Any]] = []
        page = (total // _PAGE_SIZE) + 1
        while page >= 1:
            rows = self._page(client, headers, op, page).get("rows") or []
            if not rows:
                page -= 1
                continue
            collected.extend(r for r in rows if (r.get("date") or "")[:10] >= since.isoformat())
            if (rows[0].get("date") or "")[:10] < since.isoformat():
                break
            page -= 1
            time.sleep(0.2)
        return collected

    def _page(self, client: httpx.Client, headers: dict[str, str], op: str, page: int) -> dict[str, Any]:
        response = request_with_retry(
            client, "GET", f"{SELSUP_BASE}/api/wms/findItemHistory", headers=headers,
            params={"operation": op, "limit": _PAGE_SIZE, "page": page, "count": True},
        )
        return response.json()

    def _product_info(
        self, client: httpx.Client, headers: dict[str, str], sku_ids: set[int]
    ) -> dict[int, tuple[Optional[str], Optional[str]]]:
        info: dict[int, tuple[Optional[str], Optional[str]]] = {}
        ids = list(sku_ids)
        for i in range(0, len(ids), _PRODUCT_LOOKUP_BATCH):
            chunk = ids[i : i + _PRODUCT_LOOKUP_BATCH]
            response = request_with_retry(
                client, "GET", f"{SELSUP_BASE}/api/product/find", headers=headers,
                params={"ids": chunk, "limit": _PRODUCT_LOOKUP_BATCH},
            )
            for row in response.json().get("rows", []):
                model = (row.get("view") or {}).get("model") or {}
                info[row.get("id")] = (model.get("article"), row.get("name"))
            time.sleep(0.3)
        return info


def _naive(value: Optional[datetime]) -> Optional[datetime]:
    return value.replace(tzinfo=None) if value is not None else None


def parse_movement(
    raw: dict[str, Any], info: dict[int, tuple[Optional[str], Optional[str]]]
) -> SelsupMovementLine:
    item = raw.get("item") or {}
    order = raw.get("order") or {}
    sku_id = item.get("skuId")
    article, name = info.get(sku_id, (None, None))
    return SelsupMovementLine(
        movement_type=raw["_group"],
        operation=raw.get("operation"),
        moved_at=_naive(parse_datetime(raw.get("date"))),
        warehouse_id=raw.get("warehouseId"),
        order_id=raw.get("orderId"),
        order_type=order.get("type"),
        sku_id=sku_id,
        article=article,
        product_name=name,
        cell_name=(item.get("cell") or {}).get("fullName"),
        quantity=raw.get("itemTotalQuantity"),
        user_id=raw.get("userId"),
    )
