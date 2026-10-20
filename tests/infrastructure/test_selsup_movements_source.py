"""Selsup movements against a faked HTTP transport: a row without skuId must not
break the whole run (16.10.2026 one such row violated selsup_movements.sku_id NOT NULL)."""
import logging
from datetime import date

import httpx

from infrastructure.sources.selsup import movements
from infrastructure.sources.selsup.movements import SelsupMovementsSource


def _row(sku_id, moment="2026-10-16T12:13:14+03:00"):
    return {
        "operation": "PUT", "date": moment, "warehouseId": 10001, "orderId": 1341679,
        "order": {"type": "INCOME"}, "item": {"skuId": sku_id, "cell": {"fullName": "A-1"}},
        "itemTotalQuantity": 5, "userId": 31,
    }


def _handler(request: httpx.Request) -> httpx.Response:
    params = dict(request.url.params)
    if request.url.path == "/api/wms/findItemHistory":
        if params["operation"] == "PUT":
            rows = [_row(111), _row(None), _row(222)]
            return httpx.Response(200, json={"total": len(rows), "rows": rows})
        return httpx.Response(200, json={"total": 0, "rows": []})
    assert request.url.path == "/api/product/find"
    return httpx.Response(200, json={"rows": [
        {"id": 111, "name": "one", "view": {"model": {"article": "A/1"}}},
        {"id": 222, "name": "two", "view": {"model": {"article": "A/2"}}},
    ]})


def test_movement_without_sku_id_is_skipped_and_the_rest_are_kept(fake_http, caplog):
    fake_http(movements, _handler)
    source = SelsupMovementsSource("token", window_days=3, today=date(2026, 10, 17))

    with caplog.at_level(logging.WARNING):
        lines = source.fetch("all")

    assert sorted(line.sku_id for line in lines) == [111, 222]
    assert all(line.article for line in lines)
    assert "1 rows without skuId skipped" in caplog.text
