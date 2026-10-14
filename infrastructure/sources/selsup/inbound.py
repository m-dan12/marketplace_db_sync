"""Selsup inbound orders — receipts of goods at our own warehouse.

Not to be confused with `movements.py` (warehouse operations without the
organization or the human-readable receipt number). This source is the one the
workshop sheets are reconciled against: `invoiceNumber` of an order is the
number the logisticians write into the "номер приемки" column, and
`introducedQuantity` of a line is what was actually received.

The shapes below were checked on the live API (priemka project, 2026-09-24 ..
2026-10-07):

* `GET /api/inbound/find` — orders without lines. `dateFrom` must carry a `Z`
  suffix (without a time zone the backend answers HTTP 500 "error_unknown").
  `hasMorePages` is unreliable (False on page 1 while 800+ orders exist), so
  paging stops on a short page instead.
* `GET /api/inbound/fetchOrderProduct?orderId=` — the lines: `quantity` is
  declared, `introducedQuantity` is received (0 until the order is posted in
  Selsup), `productId` is the internal product, NOT the article.
* `GET /api/product/find?ids=` — `view.wbArticle` is our seller's article
  (identical to WB `vendorCode` / Ozon `offer_id`), `barcodes[0].barcode` the
  barcode. A product not bound to a card has no `wbArticle`.
* One token covers every cabinet; the cabinet is chosen by `organizations=<id>`.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Any, Optional

import httpx

from domain.models import SelsupInboundBundle, SelsupInboundItemLine, SelsupInboundOrderLine
from infrastructure.config.settings import SELSUP_INBOUND_WINDOW_DAYS
from infrastructure.sources.parsing import parse_datetime
from infrastructure.sources.selsup.stocks import SELSUP_BASE
from shared.http_retry import request_with_retry

INBOUND_ORDER_TYPE = "INCOME"
_PAGE_SIZE = 200
_PRODUCT_LOOKUP_BATCH = 200
_PAUSE_SECONDS = 0.2


class SelsupInboundSource:
    """Inbound orders of one cabinet with their lines.

    Nightly runs read the orders created in the last `window_days` (upserts by
    natural key make overlapping windows harmless); a backfill passes `since`.
    """

    def __init__(
        self,
        token: str,
        organization_ids: dict[int, str],
        window_days: int = SELSUP_INBOUND_WINDOW_DAYS,
        since: Optional[date] = None,
        today: Optional[date] = None,
        pause_seconds: float = _PAUSE_SECONDS,
    ) -> None:
        self._token = token
        # organization id -> account in the config; the API wants the id.
        self._organization_by_account = {account: org for org, account in organization_ids.items()}
        self._window_days = window_days
        self._since = since
        self._today = today
        self._pause = pause_seconds

    def fetch(self, account: str) -> list[SelsupInboundBundle]:
        organization_id = self._organization_by_account.get(account)
        if organization_id is None:
            return []
        start = self._since or ((self._today or date.today()) - timedelta(days=self._window_days))
        date_from = datetime.combine(start, datetime.min.time()).isoformat() + "Z"
        headers = {"Authorization": self._token}

        bundles: list[SelsupInboundBundle] = []
        with httpx.Client(timeout=60.0) as client:
            for order in self._orders(client, headers, organization_id, date_from):
                order_id = order.get("id")
                if order_id is None:
                    continue
                items = self._lines(client, headers, order_id)
                resolved = self._products(client, headers, organization_id, items)
                bundles.append(parse_inbound_bundle(order, items, resolved))
        return bundles

    def _orders(
        self, client: httpx.Client, headers: dict[str, str], organization_id: int, date_from: str
    ) -> list[dict[str, Any]]:
        orders: list[dict[str, Any]] = []
        page = 1
        while True:
            response = request_with_retry(
                client, "GET", f"{SELSUP_BASE}/api/inbound/find", headers=headers,
                params={
                    "organizations": [organization_id], "type": INBOUND_ORDER_TYPE,
                    "dateFrom": date_from, "limit": _PAGE_SIZE, "page": page,
                },
            )
            rows = response.json().get("rows") or []
            orders.extend(rows)
            if len(rows) < _PAGE_SIZE:
                return orders
            page += 1
            time.sleep(self._pause)

    def _lines(self, client: httpx.Client, headers: dict[str, str], order_id: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            time.sleep(self._pause)
            response = request_with_retry(
                client, "GET", f"{SELSUP_BASE}/api/inbound/fetchOrderProduct", headers=headers,
                params={"orderId": order_id, "limit": _PAGE_SIZE, "page": page},
            )
            rows = response.json().get("rows") or []
            items.extend(rows)
            if len(rows) < _PAGE_SIZE:
                return items
            page += 1

    def _products(
        self, client: httpx.Client, headers: dict[str, str], organization_id: int, items: list[dict[str, Any]]
    ) -> dict[int, tuple[Optional[str], Optional[str]]]:
        product_ids = sorted({item["productId"] for item in items if item.get("productId")})
        resolved: dict[int, tuple[Optional[str], Optional[str]]] = {}
        for i in range(0, len(product_ids), _PRODUCT_LOOKUP_BATCH):
            chunk = product_ids[i : i + _PRODUCT_LOOKUP_BATCH]
            time.sleep(self._pause)
            response = request_with_retry(
                client, "GET", f"{SELSUP_BASE}/api/product/find", headers=headers,
                params={
                    "ids": chunk, "organizations": [organization_id],
                    "limit": len(chunk), "page": 1,
                },
            )
            for product in response.json().get("rows") or []:
                article = (product.get("view") or {}).get("wbArticle") or None
                barcodes = product.get("barcodes") or []
                barcode = (barcodes[0] or {}).get("barcode") if barcodes else None
                resolved[product["id"]] = (article, barcode)
        return resolved


def _number(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) else None


def parse_inbound_bundle(
    order: dict[str, Any],
    items: list[dict[str, Any]],
    resolved: dict[int, tuple[Optional[str], Optional[str]]],
) -> SelsupInboundBundle:
    supplier = order.get("supplier") or {}
    invoice = order.get("invoiceNumber")
    warehouse = order.get("warehouseId")
    header = SelsupInboundOrderLine(
        order_id=int(order["id"]),
        receipt_number=str(invoice) if invoice is not None else None,
        order_name=order.get("name") or None,
        supplier_id=str(supplier["id"]) if supplier.get("id") is not None else None,
        supplier_name=supplier.get("name") or None,
        status=order.get("status"),
        warehouse_id=str(warehouse) if warehouse is not None else None,
        created_at=parse_datetime(order.get("createdDate")),
        closed_at=parse_datetime(order.get("closeDate")),
    )
    lines = []
    for item in items:
        product_id = item.get("productId")
        article, barcode = resolved.get(product_id, (None, None))
        lines.append(
            SelsupInboundItemLine(
                line_id=str(item.get("id")),
                product_id=product_id,
                article=article,
                barcode=barcode,
                quantity=_number(item.get("quantity")),
                introduced_quantity=_number(item.get("introducedQuantity")) or 0.0,
            )
        )
    return SelsupInboundBundle(order=header, items=tuple(lines))
