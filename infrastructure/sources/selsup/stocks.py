from __future__ import annotations

from typing import Any, Optional

import httpx

from domain.models import SelsupStockLine
from infrastructure.sources.parsing import parse_datetime
from shared.http_retry import request_with_retry

SELSUP_BASE = "https://api.selsup.ru"
_ORG_LOOKUP_BATCH_SIZE = 200


class SelsupStocksSource:
    """`GET /api/wms/stock/all` per warehouse. One token covers every
    cabinet — the cabinet is determined per row by `organizationId`, looked
    up in batches via `POST /api/product/find`.

    A single instance is meant to be shared across all three accounts: the
    warehouse listing is fetched and split by account once (`fetch` is
    called per account, but the underlying HTTP calls only happen on the
    first call and are cached for the rest)."""

    def __init__(
        self,
        token: str,
        warehouses: list[tuple[int, str]],
        organization_ids: dict[int, str],
    ) -> None:
        self._token = token
        self._warehouses = warehouses
        self._organization_ids = organization_ids
        self._by_account: Optional[dict[str, list[SelsupStockLine]]] = None

    def fetch(self, account: str) -> list[SelsupStockLine]:
        if self._by_account is None:
            self._by_account = self._fetch_all()
        return self._by_account.get(account, [])

    def _fetch_all(self) -> dict[str, list[SelsupStockLine]]:
        headers = {"Authorization": self._token}
        by_account: dict[str, list[SelsupStockLine]] = {}
        with httpx.Client(timeout=60.0) as client:
            for warehouse_id, warehouse_name in self._warehouses:
                response = request_with_retry(
                    client, "GET", f"{SELSUP_BASE}/api/wms/stock/all",
                    headers=headers, params={"warehouseId": warehouse_id},
                )
                # Rows with quantity == 0 are "phantom" zero remains — skip them.
                items = [row for row in response.json() if row.get("quantity")]
                sku_ids = {row.get("skuId") for row in items if row.get("skuId")}
                products = self._product_info(client, headers, sku_ids)

                for row in items:
                    info = products.get(row.get("skuId")) or {}
                    account_key = self._organization_ids.get(info.get("organizationId"))
                    if account_key is None:
                        continue
                    by_account.setdefault(account_key, []).append(
                        _parse_stock_row(warehouse_id, warehouse_name, row, info)
                    )
        return by_account

    def _product_info(
        self, client: httpx.Client, headers: dict[str, str], sku_ids: set[int]
    ) -> dict[int, dict[str, Any]]:
        """`product/find` rows by sku id: the cabinet (organizationId), name, category and brand."""
        info: dict[int, dict[str, Any]] = {}
        ids = list(sku_ids)
        for i in range(0, len(ids), _ORG_LOOKUP_BATCH_SIZE):
            chunk = ids[i : i + _ORG_LOOKUP_BATCH_SIZE]
            response = request_with_retry(
                client, "GET", f"{SELSUP_BASE}/api/product/find",
                headers=headers, params={"ids": chunk, "limit": _ORG_LOOKUP_BATCH_SIZE},
            )
            for row in response.json().get("rows", []):
                info[row.get("id")] = row
        return info


def _parse_stock_row(
    warehouse_id: int, warehouse_name: str, raw: dict[str, Any], info: Optional[dict[str, Any]] = None
) -> SelsupStockLine:
    product = ((raw.get("sku") or {}).get("product")) or {}
    cell = raw.get("cell") or {}
    model = ((info or {}).get("view") or {}).get("model") or {}
    return SelsupStockLine(
        warehouse_id=warehouse_id,
        warehouse_name=warehouse_name,
        sku_id=raw.get("skuId"),
        article=product.get("anyArticle") or None,  # blank for fabric and fittings
        wb_size=product.get("wildberriesSizeId"),
        ozon_article=product.get("ozonArticle"),
        cell_name=cell.get("fullName"),
        quantity=raw.get("quantity"),
        available_quantity=raw.get("availableQuantity"),
        calculated_quantity=raw.get("calculatedQuantity"),
        modify_date=parse_datetime(raw.get("modifyDate")),
        product_name=(info or {}).get("name"),
        category=(model.get("category") or {}).get("name"),
        brand=(model.get("brand") or {}).get("name"),
        model_article=model.get("article") or None,
        purchase_price=raw.get("purchasePrice"),
        organization_id=(info or {}).get("organizationId"),
    )
