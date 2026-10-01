"""Supplies, funnel, promotions and Ozon warehouse stocks against a faked HTTP
transport. The payload shapes are the ones the live APIs returned in the
probe run of 01.10.2026."""
import json
from datetime import date, datetime, timezone

import httpx
import pytest

from infrastructure.sources.ozon import promotions as ozon_promotions
from infrastructure.sources.ozon import supplies as ozon_supplies
from infrastructure.sources.ozon import warehouse_stocks as ozon_wh
from infrastructure.sources.wb import funnel as wb_funnel
from infrastructure.sources.wb import promotions as wb_promotions
from infrastructure.sources.wb import supplies as wb_supplies

# --- WB supplies --------------------------------------------------------------

WB_LISTED = {
    "supplyID": 41488662, "preorderID": 53789798, "createDate": "2026-09-29T15:17:12+03:00",
    "supplyDate": "2026-10-03T00:00:00+03:00", "factDate": None,
    "updatedDate": "2026-09-29T15:17:30+03:00", "statusID": 2, "boxTypeID": 2,
}
WB_DETAIL = {
    "statusID": 2, "warehouseID": 300168, "warehouseName": "СЦ Радумля", "actualWarehouseName": "",
    "transitWarehouseName": "", "quantity": 82, "readyForSaleQuantity": 0, "acceptedQuantity": 0,
}
WB_GOODS = [
    {"barcode": "2040694504512", "vendorCode": "PT5633/4-13-26/1", "nmID": 244950557, "techSize": "0",
     "quantity": 6, "readyForSaleQuantity": 0, "acceptedQuantity": 0},
    {"barcode": "2040694504994", "vendorCode": "PT4288/0-16-0/0", "nmID": 244950501, "techSize": "0",
     "quantity": 10, "readyForSaleQuantity": 0, "acceptedQuantity": 0},
]


def test_wb_supply_bundle_merges_list_detail_and_goods():
    bundle = wb_supplies.parse_supply_bundle(WB_LISTED, WB_DETAIL, WB_GOODS)
    supply = bundle.supply
    assert (supply.marketplace, supply.supply_key, supply.order_id) == ("wb", "s41488662", "53789798")
    assert supply.status == "planned" and supply.warehouse_name == "СЦ Радумля"
    assert supply.actual_warehouse_name is None and supply.transit_warehouse_name is None  # "" -> None
    assert supply.quantity == 82 and supply.fact_date is None
    assert supply.planned_date.date() == date(2026, 10, 3)
    assert [(i.article, i.nm_id, i.quantity, i.item_key) for i in bundle.items] == [
        ("PT5633/4-13-26/1", 244950557, 6.0, "2040694504512"),
        ("PT4288/0-16-0/0", 244950501, 10.0, "2040694504994"),
    ]


def test_wb_supply_without_a_supply_id_is_keyed_by_the_preorder():
    bundle = wb_supplies.parse_supply_bundle({"supplyID": None, "preorderID": 7, "statusID": 1}, {}, [])
    assert bundle.supply.supply_key == "p7" and bundle.supply.status == "not_planned" and bundle.items == ()


def test_wb_supplies_source_lists_then_reads_each_supply(fake_http):
    def handler(request):
        path = request.url.path
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["dates"][0]["type"] == "updatedDate"
            return httpx.Response(200, json=[WB_LISTED])
        if path.endswith("/goods"):
            assert request.url.params["isPreorderID"] == "false"
            return httpx.Response(200, json=WB_GOODS)
        return httpx.Response(200, json=WB_DETAIL)

    fake_http(wb_supplies, handler)
    source = wb_supplies.WBSuppliesSource("key", today=date(2026, 9, 30), pause_seconds=0)
    [bundle] = source.fetch("skazka")
    assert bundle.supply.warehouse_name == "СЦ Радумля" and len(bundle.items) == 2
    assert [r.method for r in fake_http.requests] == ["POST", "GET", "GET"]


def test_wb_supplies_backfill_walks_creation_dates_and_survives_an_unreadable_supply(fake_http):
    def handler(request):
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["dates"][0] == {"from": "2025-01-01", "till": "2026-09-30", "type": "createDate"}
            return httpx.Response(200, json=[WB_LISTED])
        return httpx.Response(404, json={"title": "not found"})

    fake_http(wb_supplies, handler)
    source = wb_supplies.WBSuppliesSource("key", created_from=date(2025, 1, 1), today=date(2026, 9, 30), pause_seconds=0)
    [bundle] = source.fetch("skazka")
    assert bundle.supply.supply_key == "s41488662" and bundle.items == ()  # header from the list only


# --- Ozon supplies ------------------------------------------------------------

OZON_ORDER = {
    "order_id": 131649555, "created_date": "2026-09-30T06:12:11.495697Z", "state": "READY_TO_SUPPLY",
    "state_updated_date": "2026-09-30T07:29:10.659340Z",
    "drop_off_warehouse": {"warehouse_id": 1, "name": "РАДУМЛЯ_РФЦ_НЕГАБАРИТ_КРОССДОКИНГ"},
    "timeslot": {"timeslot": {"from": "2026-10-01T10:00:00Z", "to": "2026-10-01T11:00:00Z"}},
    "supplies": [{"state": "READY_TO_SUPPLY", "supply_id": 2000068533951, "storage_warehouse": None,
                  "is_crossdock": True, "bundle_id": "bundle-1"}],
}
OZON_ITEMS = [
    {"sku": 3122082738, "quantity": 5, "offer_id": "PT125/5-16-17/1", "barcode": "4620428209197", "product_id": 1},
    {"sku": 3122081225, "quantity": 12, "offer_id": "PT125/4-13-17/1", "barcode": "4620428209198", "product_id": 2},
]


def test_ozon_order_supply_maps_warehouses_dates_and_items():
    bundle = ozon_supplies.parse_order_supply(OZON_ORDER, OZON_ORDER["supplies"][0], OZON_ITEMS)
    supply = bundle.supply
    assert (supply.marketplace, supply.supply_key, supply.order_id) == ("ozon", "s2000068533951", "131649555")
    assert supply.status == "READY_TO_SUPPLY" and supply.is_crossdock is True
    assert supply.warehouse_name == "РАДУМЛЯ_РФЦ_НЕГАБАРИТ_КРОССДОКИНГ"
    assert supply.transit_warehouse_name == "РАДУМЛЯ_РФЦ_НЕГАБАРИТ_КРОССДОКИНГ"
    assert supply.actual_warehouse_name is None
    assert supply.planned_date == datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
    assert supply.quantity == 17
    assert [(i.article, i.sku, i.quantity) for i in bundle.items] == [
        ("PT125/5-16-17/1", 3122082738, 5.0), ("PT125/4-13-17/1", 3122081225, 12.0),
    ]


def test_ozon_order_without_supplies_still_yields_a_header():
    order = {**OZON_ORDER, "supplies": [], "state": "DATA_FILLING"}
    [bundle] = ozon_supplies.OzonSuppliesSource("c", "k")._order_bundles(None, {}, order)
    assert bundle.supply.supply_key == "o131649555" and bundle.supply.status == "DATA_FILLING" and bundle.items == ()


def ozon_supplies_handler(order_ids_by_call, orders_by_id):
    state = {"list_calls": []}

    def handler(request):
        path, body = request.url.path, json.loads(request.content)
        if path.endswith("/supply-order/list"):
            state["list_calls"].append((tuple(body["filter"]["states"]), body.get("last_id", "")))
            ids, last = order_ids_by_call[len(state["list_calls"]) - 1]
            return httpx.Response(200, json={"order_ids": ids, "last_id": last})
        if path.endswith("/supply-order/get"):
            return httpx.Response(200, json={"orders": [orders_by_id[i] for i in body["order_ids"]]})
        assert path.endswith("/supply-order/bundle")
        return httpx.Response(200, json={"items": OZON_ITEMS, "has_next": False, "last_id": ""})

    return handler, state


def test_ozon_nightly_reads_recent_pages_of_everything_plus_all_active_orders(fake_http):
    other = {**OZON_ORDER, "order_id": 5, "supplies": [{**OZON_ORDER["supplies"][0], "supply_id": 55}]}
    handler, state = ozon_supplies_handler(
        # call 1: newest page of all states (more pages exist, but recent_pages=1), call 2: active states
        [([131649555], "cursor"), ([131649555, 5], "")], {131649555: OZON_ORDER, 5: other},
    )
    fake_http(ozon_supplies, handler)
    bundles = ozon_supplies.OzonSuppliesSource("c", "k", full=False, recent_pages=1).fetch("skazka")
    assert sorted(b.supply.supply_key for b in bundles) == ["s2000068533951", "s55"]  # deduplicated ids
    first_states, second_states = state["list_calls"][0][0], state["list_calls"][1][0]
    assert set(ozon_supplies.ARCHIVE_STATES) <= set(first_states)
    assert set(second_states) == set(ozon_supplies.ACTIVE_STATES)


def test_ozon_full_backfill_pages_through_everything(fake_http):
    handler, state = ozon_supplies_handler([([131649555], "c1"), ([5], "")], {131649555: OZON_ORDER, 5: OZON_ORDER})
    fake_http(ozon_supplies, handler)
    ozon_supplies.OzonSuppliesSource("c", "k", full=True).fetch("skazka")
    assert [call[1] for call in state["list_calls"]] == ["", "c1"]  # one chain, second page by last_id


# --- WB funnel ----------------------------------------------------------------

FUNNEL_PRODUCT = {
    "product": {"nmId": 623306314, "vendorCode": "PT125/5-16-17/1", "subjectName": "Постельное белье",
                "productRating": 10, "feedbackRating": 4.8},
    "statistic": {"selected": {"openCount": 386, "cartCount": 23, "orderCount": 1, "orderSum": 6672,
                               "buyoutCount": 0, "buyoutSum": 0, "cancelCount": 0, "cancelSum": 0,
                               "addToWishlist": 10}},
}


def test_funnel_product_parse():
    line = wb_funnel.parse_funnel_product(FUNNEL_PRODUCT, date(2026, 9, 29))
    assert (line.nm_id, line.vendor_code, line.open_count, line.cart_count, line.order_count) == (
        623306314, "PT125/5-16-17/1", 386, 23, 1,
    )
    assert (line.order_sum, line.add_to_wishlist, line.feedback_rating) == (6672, 10, 4.8)
    assert line.day == date(2026, 9, 29)


def test_funnel_fetch_reads_one_day_at_a_time_ending_yesterday_and_pages(fake_http):
    def handler(request):
        body = json.loads(request.content)
        assert body["selectedPeriod"]["start"] == body["selectedPeriod"]["end"]
        count = 1000 if body["offset"] == 0 else 2  # a full page, then a short one
        return httpx.Response(200, json={"data": {"products": [FUNNEL_PRODUCT] * count}})

    fake_http(wb_funnel, handler)
    source = wb_funnel.WBFunnelSource("key", window_days=2, today=date(2026, 10, 1), min_interval_seconds=0)
    lines = source.fetch("skazka")
    assert len(lines) == 2 * 1002
    assert sorted({line.day for line in lines}) == [date(2026, 9, 29), date(2026, 9, 30)]
    assert [json.loads(r.content)["offset"] for r in fake_http.requests] == [0, 1000, 0, 1000]


def test_funnel_requests_are_spaced_to_the_rate_limit(fake_http, monkeypatch):
    sleeps = []
    monkeypatch.setattr(wb_funnel.time, "sleep", sleeps.append)
    ticks = iter([0.0, 0.0, 1.0, 1.0, 30.0, 30.0])
    monkeypatch.setattr(wb_funnel.time, "monotonic", lambda: next(ticks))
    fake_http(wb_funnel, lambda request: httpx.Response(200, json={"data": {"products": []}}))
    source = wb_funnel.WBFunnelSource("key", today=date(2026, 10, 1), min_interval_seconds=20)
    source.fetch_day(date(2026, 9, 30))
    source.fetch_day(date(2026, 9, 29))
    source.fetch_day(date(2026, 9, 28))
    assert sleeps == [19.0]  # the second call waited out the remainder; the third was already late enough


# --- promotions ---------------------------------------------------------------

WB_PROMO_REGULAR = {"id": 2766, "name": "Новинки", "type": "regular",
                    "startDateTime": "2026-08-01T00:00:00Z", "endDateTime": "2026-10-20T00:00:00Z"}
WB_PROMO_AUTO = {"id": 3012, "name": "Осенние скидки", "type": "auto",
                 "startDateTime": "2026-10-07T21:00:00Z", "endDateTime": "2026-10-30T20:59:59Z"}
WB_PROMO_OLD = {"id": 100, "name": "Старая", "type": "regular",
                "startDateTime": "2026-03-01T00:00:00Z", "endDateTime": "2026-03-10T00:00:00Z"}


def wb_promotions_handler(requested_items):
    def handler(request):
        path = request.url.path
        if path.endswith("/nomenclatures"):
            promo = int(request.url.params["promotionID"])
            requested_items.append(promo)
            if promo == WB_PROMO_AUTO["id"]:
                return httpx.Response(422, json={"errorText": "Unprocessable entity"})
            return httpx.Response(200, json={"data": {"nomenclatures": [
                {"id": 1316382557, "inAction": True, "price": 8339, "planPrice": 4004, "discount": 53, "planDiscount": 53},
            ]}})
        if path.endswith("/details"):
            return httpx.Response(200, json={"data": {"promotions": [
                {"id": 2766, "description": "x" * 5000, "inPromoActionTotal": 40, "notInPromoActionTotal": 60},
                {"id": 3012}, {"id": 100},
            ]}})
        return httpx.Response(200, json={"data": {"promotions": [WB_PROMO_REGULAR, WB_PROMO_AUTO, WB_PROMO_OLD]}})

    return handler


def test_wb_promotions_read_items_only_for_recent_regular_promotions(fake_http):
    requested: list[int] = []
    fake_http(wb_promotions, wb_promotions_handler(requested))
    source = wb_promotions.WBPromotionsSource("key", today=date(2026, 10, 1), pause_seconds=0)
    bundles = {b.promotion.promo_id: b for b in source.fetch("skazka")}

    assert requested == [2766]  # not the auto promotion (no list), not the one that ended in March
    regular = bundles["2766"]
    assert [(i.item_id, i.in_action, i.plan_price, i.plan_discount) for i in regular.items] == [(1316382557, True, 4004, 53)]
    assert regular.promotion.participating_count == 40 and regular.promotion.potential_count == 100
    assert len(regular.promotion.description) == 1000
    assert bundles["3012"].items == () and bundles["100"].items == ()


def test_wb_promotions_backfill_reads_items_of_older_regular_promotions_too(fake_http):
    requested: list[int] = []
    fake_http(wb_promotions, wb_promotions_handler(requested))
    source = wb_promotions.WBPromotionsSource(
        "key", items_from=date(2026, 1, 1), today=date(2026, 10, 1), pause_seconds=0
    )
    source.fetch("skazka")
    assert sorted(requested) == [100, 2766]  # the March promotion is read now; the auto one never is


def test_ozon_actions_read_products_of_participating_actions_only(fake_http):
    actions = [
        {"id": 1977747, "title": "Эластичный бустинг", "action_type": "MARKETPLACE_MULTI_LEVEL_DISCOUNT_ON_AMOUNT",
         "date_start": "2025-03-19T21:00:44Z", "date_end": "2026-12-31T20:59:59Z",
         "potential_products_count": 8187, "participating_products_count": 2, "discount_type": "PERCENT", "discount_value": 10},
        {"id": 2, "title": "Чужая", "date_start": "2026-10-06T00:00:00Z", "date_end": "2026-10-21T00:00:00Z",
         "potential_products_count": 5, "participating_products_count": 0},
    ]
    seen: list[int] = []

    def handler(request):
        if request.url.path.endswith("/v1/actions"):
            return httpx.Response(200, json={"result": actions})
        body = json.loads(request.content)
        seen.append(body["action_id"])
        return httpx.Response(200, json={"result": {"total": 2, "products": [
            {"id": 520687888, "price": 1495, "action_price": 1238, "stock": 1},
            {"id": 520687889, "price": 1366, "action_price": 1134, "stock": 0},
        ]}})

    fake_http(ozon_promotions, handler)
    bundles = ozon_promotions.OzonActionsSource("c", "k").fetch("skazka")
    assert seen == [1977747]
    first = bundles[0]
    assert (first.promotion.promo_id, first.promotion.discount_type, first.promotion.discount_value) == ("1977747", "PERCENT", 10)
    assert [(i.item_id, i.price, i.plan_price, i.stock) for i in first.items] == [(520687888, 1495, 1238, 1), (520687889, 1366, 1134, 0)]
    assert bundles[1].items == ()


# --- Ozon warehouse stocks ----------------------------------------------------

def test_ozon_warehouse_stocks_page_and_skip_rows_without_offer_or_warehouse(fake_http):
    def handler(request):
        offset = json.loads(request.content)["offset"]
        rows = [{"sku": i, "warehouse_name": "ХОРУГВИНО_РФЦ", "item_code": f"A{i}", "item_name": "n",
                 "promised_amount": 1, "free_to_sell_amount": 2, "reserved_amount": 3} for i in range(1000)]
        if offset:
            rows = [{"sku": 1, "warehouse_name": "", "item_code": "B"}, {"sku": 2, "warehouse_name": "W", "item_code": None}]
        return httpx.Response(200, json={"result": {"rows": rows}})

    fake_http(ozon_wh, handler)
    lines = ozon_wh.OzonWarehouseStocksSource("c", "k").fetch("skazka")
    assert len(lines) == 1000
    assert (lines[0].free_to_sell, lines[0].reserved, lines[0].promised) == (2, 3, 1)
