from datetime import date, datetime

from infrastructure.sources.ozon.prices import parse_price_item
from infrastructure.sources.selsup.movements import parse_movement
from infrastructure.sources.wb.ads import parse_campaigns, parse_fullstats
from infrastructure.sources.wb.prices import parse_goods


def test_wb_goods_expand_to_one_line_per_size_with_product_level_discount():
    goods = [{
        "nmID": 10, "vendorCode": "PT1/0-0-0/1", "discount": 47,
        "sizes": [
            {"sizeID": 1, "techSizeName": "0", "price": 40589, "discountedPrice": 21512.17},
            {"sizeID": 2, "techSizeName": "1", "price": 41000, "discountedPrice": 21730},
        ],
    }, {"nmID": 11, "vendorCode": "x", "sizes": None}]
    lines = parse_goods(goods)
    assert [(line.tech_size, line.price, line.discount) for line in lines] == [
        ("0", 40589, 47), ("1", 41000, 47),
    ]


def test_ozon_price_item():
    line = parse_price_item({
        "offer_id": "PT140/0-0-56/1", "product_id": 520675773,
        "price": {"price": 602, "old_price": 1189, "min_price": 494, "marketing_seller_price": 602},
    })
    assert (line.offer_id, line.price, line.old_price, line.min_price) == ("PT140/0-0-56/1", 602, 1189, 494)
    assert parse_price_item({"offer_id": "a"}).price is None


def test_campaigns_dedupe_ids_and_label_status():
    ids, status = parse_campaigns({"adverts": [
        {"status": 9, "advert_list": [{"advertId": 1}, {"advertId": 2}]},
        {"status": 7, "advert_list": [{"advertId": 2}, {"advertId": 3}]},
    ]})
    assert ids == [1, 2, 3]
    assert status[1] == "активна" and status[3] == "завершена"


def test_fullstats_flatten_campaign_day_app_product():
    stats = [{
        "advertId": 5,
        "boosterStats": [{"date": "2026-09-19T00:00:00Z", "avg_position": 96}],
        "days": [{"date": "2026-09-19T00:00:00Z", "apps": [
            {"nms": [{"nmId": 7, "views": 101, "clicks": 3, "ctr": 2.97, "cpc": 12.1, "sum": 36.3,
                      "orders": 0, "atbs": 1}]},
            {"nms": [{"nmId": 7, "views": 10, "clicks": 0, "ctr": 0, "cpc": 0, "sum": 0,
                      "orders": 0, "atbs": 0}]},
        ]}],
    }]
    lines = parse_fullstats(stats, {5: "активна"})
    assert len(lines) == 2
    assert lines[0].stat_date == date(2026, 9, 19) and lines[0].avg_position == 96
    assert lines[0].campaign_status == "активна" and lines[0].carts == 1


def test_selsup_movement_row():
    raw = {
        "_group": "Отгрузка", "operation": "TAKE", "date": "2026-09-29T14:41:25.000Z", "warehouseId": 10001,
        "orderId": 7, "order": {"type": "OUTCOME"}, "userId": 3, "itemTotalQuantity": 4,
        "item": {"skuId": 23808, "cell": {"fullName": "51-A"}},
    }
    line = parse_movement(raw, {23808: ("PT4214/4-0-0/1", "Пододеяльник")})
    assert line.moved_at == datetime(2026, 9, 29, 14, 41, 25) and line.moved_at.tzinfo is None
    assert (line.article, line.product_name, line.quantity, line.order_type) == (
        "PT4214/4-0-0/1", "Пододеяльник", 4, "OUTCOME",
    )
    unknown = parse_movement({**raw, "item": {"skuId": 99}}, {})
    assert unknown.article is None
