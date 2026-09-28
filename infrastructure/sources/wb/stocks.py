from __future__ import annotations

import time
from typing import Any

import httpx

from domain.models import WbStockLine
from infrastructure.sources.wb.client import ANALYTICS_BASE, CONTENT_BASE, wb_headers
from shared.http_retry import request_with_retry

_REPORT_POLL_INTERVAL_SECONDS = 4
_REPORT_POLL_MAX_ATTEMPTS = 60
_CONTENT_PAGE_LIMIT = 100  # WB always caps cards/list at 100, whatever `limit` asks for
_CONTENT_MAX_PAGES = 10000  # loop guard only — real catalogs stop on the first empty page


class WBStocksSource:
    """Warehouse remains report: create task -> poll status -> download,
    then join in vendorCode (the report itself doesn't carry it) via a
    separate Content API cards listing."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def fetch(self, account: str) -> list[WbStockLine]:
        headers = wb_headers(self._api_key)
        with httpx.Client(timeout=60.0) as client:
            task_id = self._create_report(client, headers)
            self._wait_for_report(client, headers, task_id)
            rows = self._download_report(client, headers, task_id)
            vendor_codes = self._vendor_code_map(client, headers)

        result: list[WbStockLine] = []
        for row in rows:
            nm_id = row.get("nmId")
            for warehouse in row.get("warehouses", []) or []:
                result.append(
                    WbStockLine(
                        nm_id=nm_id,
                        vendor_code=vendor_codes.get(nm_id),
                        barcode=row.get("barcode"),
                        tech_size=row.get("techSize"),
                        volume=row.get("volume"),
                        warehouse_name=warehouse["warehouseName"],
                        quantity=warehouse["quantity"],
                    )
                )
        return result

    def _create_report(self, client: httpx.Client, headers: dict[str, str]) -> str:
        response = request_with_retry(
            client, "GET", f"{ANALYTICS_BASE}/api/v1/warehouse_remains", headers=headers,
            params={"groupByNm": "true", "groupByBarcode": "true", "groupBySize": "true"},
        )
        return response.json()["data"]["taskId"]

    def _wait_for_report(self, client: httpx.Client, headers: dict[str, str], task_id: str) -> None:
        url = f"{ANALYTICS_BASE}/api/v1/warehouse_remains/tasks/{task_id}/status"
        for _ in range(_REPORT_POLL_MAX_ATTEMPTS):
            time.sleep(_REPORT_POLL_INTERVAL_SECONDS)
            response = request_with_retry(client, "GET", url, headers=headers)
            if response.json().get("data", {}).get("status") == "done":
                return
        raise TimeoutError(f"warehouse_remains report {task_id} timed out")

    def _download_report(
        self, client: httpx.Client, headers: dict[str, str], task_id: str
    ) -> list[dict[str, Any]]:
        url = f"{ANALYTICS_BASE}/api/v1/warehouse_remains/tasks/{task_id}/download"
        response = request_with_retry(client, "GET", url, headers=headers)
        return response.json()

    def _vendor_code_map(self, client: httpx.Client, headers: dict[str, str]) -> dict[int, str]:
        mapping: dict[int, str] = {}
        cursor: dict[str, Any] = {"limit": _CONTENT_PAGE_LIMIT}
        seen_nm_ids: set[int] = set()
        for _ in range(_CONTENT_MAX_PAGES):
            response = request_with_retry(
                client, "POST", f"{CONTENT_BASE}/content/v2/get/cards/list", headers=headers,
                json={"settings": {"cursor": cursor, "filter": {"withPhoto": -1}}},
            )
            data = response.json()
            cards = data.get("cards", [])
            if not cards:
                break
            for card in cards:
                mapping[card.get("nmID")] = card.get("vendorCode")
            new_cursor = data.get("cursor", {})
            next_nm_id = new_cursor.get("nmID")
            if not next_nm_id or next_nm_id in seen_nm_ids:
                break
            seen_nm_ids.add(next_nm_id)
            cursor = {"limit": _CONTENT_PAGE_LIMIT, "updatedAt": new_cursor["updatedAt"], "nmID": next_nm_id}
        return mapping
