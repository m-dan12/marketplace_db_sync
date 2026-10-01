"""The new API sources against a faked HTTP transport: pagination, request
shape and the wiring of parsing. (The contracts come from
`reference/nightly_export.py`; this checks our use of them, not the live API.)"""
import json
from datetime import date, datetime

import httpx
import pytest

from infrastructure.sources.ozon import prices as ozon_prices
from infrastructure.sources.selsup import movements as selsup_movements
from infrastructure.sources.wb import ads as wb_ads
from infrastructure.sources.wb import prices as wb_prices


@pytest.fixture()
def fake_http(monkeypatch):
    """Route every `httpx.Client` created by the given module through a handler."""
    real_client = httpx.Client
    requests: list[httpx.Request] = []

    def install(module, handler):
        def recording(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return handler(request)

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(recording)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(module.httpx, "Client", factory)

    install.requests = requests
    return install


def test_wb_prices_paginates_until_a_short_page(fake_http):
    def handler(request):
        offset = int(request.url.params["offset"])
        count = 1000 if offset == 0 else 3
        goods = [
            {"nmID": offset + i, "vendorCode": f"A{offset + i}", "discount": 10,
             "sizes": [{"sizeID": 1, "techSizeName": "0", "price": 100, "discountedPrice": 90}]}
            for i in range(count)
        ]
        return httpx.Response(200, json={"data": {"listGoods": goods}})

    fake_http(wb_prices, handler)
    lines = wb_prices.WBPricesSource("key").fetch("skazka")
    assert len(lines) == 1003
    assert [r.url.params["offset"] for r in fake_http.requests] == ["0", "1000"]
    assert fake_http.requests[0].headers["authorization"] == "key"


def test_ozon_prices_follow_the_cursor(fake_http):
    def handler(request):
        body = json.loads(request.content)
        if not body.get("cursor"):
            return httpx.Response(200, json={
                "items": [{"offer_id": "A", "product_id": 1, "price": {"price": 10}}], "cursor": "next",
            })
        assert body["cursor"] == "next"
        return httpx.Response(200, json={
            "items": [{"offer_id": "B", "product_id": 2, "price": {"price": 20}}], "cursor": "",
        })

    fake_http(ozon_prices, handler)
    lines = ozon_prices.OzonPricesSource("cid", "key").fetch("skazka")
    assert [(line.offer_id, line.price) for line in lines] == [("A", 10), ("B", 20)]
    assert fake_http.requests[0].headers["client-id"] == "cid"


def test_wb_ads_lists_campaigns_then_pulls_stats_in_chunks_of_50(fake_http):
    advert_ids = list(range(1, 121))  # 120 campaigns -> 3 chunks

    def handler(request):
        if request.url.path.endswith("/promotion/count"):
            return httpx.Response(200, json={"adverts": [
                {"status": 9, "advert_list": [{"advertId": i} for i in advert_ids]},
            ]})
        ids = [int(i) for i in request.url.params["ids"].split(",")]
        assert len(ids) <= 50
        return httpx.Response(200, json=[
            {"advertId": i, "days": [{"date": "2026-09-19T00:00:00Z", "apps": [
                {"nms": [{"nmId": 7, "views": 1, "clicks": 0, "ctr": 0, "cpc": 0, "sum": 1,
                          "orders": 0, "atbs": 0}]}]}]}
            for i in ids
        ])

    fake_http(wb_ads, handler)
    lines = wb_ads.WBAdsSource("key", chunk_pause_seconds=0).fetch("skazka")
    assert len(lines) == 120
    stats_calls = [r for r in fake_http.requests if r.url.path.endswith("/fullstats")]
    assert len(stats_calls) == 3
    params = stats_calls[0].url.params
    assert (date.fromisoformat(params["endDate"]) - date.fromisoformat(params["beginDate"])).days == 7
    assert {line.campaign_status for line in lines} == {"активна"}


def test_selsup_movements_reads_recent_rows_and_resolves_articles(fake_http, monkeypatch):
    monkeypatch.setattr(selsup_movements.time, "sleep", lambda _: None)

    def handler(request):
        path, params = request.url.path, request.url.params
        if path.endswith("/findItemHistory"):
            if params["operation"] != "PUT":
                return httpx.Response(200, json={"total": 0, "rows": []})
            return httpx.Response(200, json={"total": 3, "rows": [
                {"operation": "PUT", "date": "2026-09-20T10:00:00", "warehouseId": 10001, "orderId": 1,
                 "order": {"type": "INCOME"}, "userId": 1, "itemTotalQuantity": 1,
                 "item": {"skuId": 5, "cell": {"fullName": "old"}}},  # before the window
                {"operation": "PUT", "date": "2026-09-29T10:00:00", "warehouseId": 10001, "orderId": 2,
                 "order": {"type": "INCOME"}, "userId": 1, "itemTotalQuantity": 4,
                 "item": {"skuId": 5, "cell": {"fullName": "c1"}}},
                {"operation": "PUT", "date": "2026-09-30T08:00:00", "warehouseId": 10020, "orderId": 3,
                 "order": {"type": "INCOME"}, "userId": 1, "itemTotalQuantity": 2,
                 "item": {"skuId": 6, "cell": {"fullName": "c2"}}},
            ]})
        assert path.endswith("/product/find")
        return httpx.Response(200, json={"rows": [
            {"id": 5, "name": "Пододеяльник", "view": {"model": {"article": "PT1/4-0-0/1"}}},
            {"id": 6, "name": "Наволочка", "view": {"model": {"article": "PT1/0-0-25/1"}}},
        ]})

    fake_http(selsup_movements, handler)
    source = selsup_movements.SelsupMovementsSource("tok", window_days=3, today=date(2026, 9, 30))
    lines = source.fetch("skazka")

    assert [(l.order_id, l.movement_type, l.article, l.quantity) for l in lines] == [
        (2, "Приёмка", "PT1/4-0-0/1", 4), (3, "Приёмка", "PT1/0-0-25/1", 2),
    ]
    assert lines[1].moved_at == datetime(2026, 9, 30, 8, 0)
    calls_before = len(fake_http.requests)
    assert source.fetch("timeless") is lines  # cached: one run serves every account
    assert len(fake_http.requests) == calls_before
