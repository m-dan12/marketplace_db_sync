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
        "price": {"price": 602, "old_price": 1189, "min_price": 494, "marketing_seller_price": 602, "net_price": 217},
    })
    assert line.net_price == 217
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


def test_selsup_stock_row_carries_category_brand_and_the_model_article():
    from infrastructure.sources.selsup.stocks import _parse_stock_row

    raw = {
        "skuId": 146062, "quantity": 15000, "availableQuantity": 15000, "calculatedQuantity": 0,
        "purchasePrice": 0.54, "cell": {"fullName": "Без места"},
        "sku": {"product": {"anyArticle": "", "ozonArticle": None}},
    }
    info = {
        "name": "Ткань", "organizationId": 100980,
        "view": {"model": {"article": "1588552073", "category": {"name": "Ткани для рукоделия"}, "brand": {"name": "Сказка"}}},
    }
    line = _parse_stock_row(10016, "Фурнитура Профтекс", raw, info)
    # a set has no article of its own: the model's one stands in, flagged so it is not planned as goods
    assert (line.article, line.model_article, line.article_from_model) == ("1588552073", "1588552073", True)
    assert (line.category, line.brand, line.product_name, line.purchase_price) == ("Ткани для рукоделия", "Сказка", "Ткань", 0.54)
    assert _parse_stock_row(10016, "w", raw).category is None  # no product info found
    assert _parse_stock_row(10016, "w", raw).article is None


def test_wb_cards_keep_brand_title_and_subject():
    from infrastructure.sources.wb.cards import parse_cards

    cards = parse_cards([
        {"nmID": 5, "vendorCode": "PT1/0-0-24/1", "brand": "Сказка", "title": "Наволочки", "subjectID": 689, "subjectName": "Наволочки"},
        {"nmID": 6, "vendorCode": "", "brand": "x"},
    ])
    assert [(c.article, c.external_id, c.brand, c.category, c.category_id) for c in cards] == [
        ("PT1/0-0-24/1", 5, "Сказка", "Наволочки", 689)]


def test_ozon_category_tree_names_the_types_under_their_category():
    from infrastructure.sources.ozon.cards import flatten_category_tree, parse_cards

    tree = [{"description_category_id": 1, "category_name": "Дом и сад", "children": [
        {"description_category_id": 2, "category_name": "Постельное бельё", "children": [
            {"type_id": 9, "type_name": "Пододеяльник", "children": []}]}]}]
    names = flatten_category_tree(tree)
    assert names == {(2, 9): "Постельное бельё > Пододеяльник"}
    cards = parse_cards([
        {"id": 11, "offer_id": "A", "name": "Пододеяльник", "description_category_id": 2, "type_id": 9},
        {"id": 12, "offer_id": "B", "name": "x", "description_category_id": 3, "type_id": 1},
        {"id": 13, "name": "no offer"},
    ], names)
    assert [(c.article, c.category) for c in cards] == [("A", "Постельное бельё > Пододеяльник"), ("B", None)]
