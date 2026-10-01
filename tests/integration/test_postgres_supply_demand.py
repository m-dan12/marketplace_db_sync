"""Integration tests (real Postgres) for supplies, funnel, promotions and the
Ozon warehouse stock. Skipped without TEST_DATABASE_URL, like the others."""
import os
from datetime import date, datetime, timezone

import pytest

from domain.models import (
    OzonStockLine,
    OzonWarehouseStockLine,
    PromotionBundle,
    PromotionItemLine,
    PromotionLine,
    SupplyBundle,
    SupplyItemLine,
    SupplyLine,
    WbFunnelLine,
    WbPriceLine,
)
from infrastructure.persistence.postgres.connection import apply_schema, connect
from infrastructure.persistence.postgres.repositories import PostgresOzonStockRepository
from infrastructure.persistence.postgres.repositories_ml import PostgresWbPriceRepository
from infrastructure.persistence.postgres.repositories_supply_demand import (
    PostgresOzonWarehouseStockRepository,
    PostgresPromotionRepository,
    PostgresSupplyRepository,
    PostgresWbFunnelRepository,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set"
)

TABLES = (
    "wb_prices ozon_stocks supplies supply_items wb_funnel_daily wb_funnel_days promotions "
    "promotion_items ozon_warehouse_stocks"
).split()


@pytest.fixture()
def conn():
    connection = connect(os.environ["TEST_DATABASE_URL"])
    apply_schema(connection)
    with connection.cursor() as cur:
        cur.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY")
    connection.commit()
    yield connection
    connection.close()


def rows(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def make_supply(key="s1", status="planned", quantity=82.0, marketplace="wb") -> SupplyLine:
    return SupplyLine(
        marketplace=marketplace, supply_key=key, order_id="9",
        created_at=datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc), planned_date=None, fact_date=None,
        updated_at=None, status=status, warehouse_name="СЦ Радумля", actual_warehouse_name=None,
        transit_warehouse_name=None, is_crossdock=None, quantity=quantity, accepted_quantity=None,
        ready_for_sale_quantity=None,
    )


def make_item(key, article="A", quantity=6.0, accepted=None) -> SupplyItemLine:
    return SupplyItemLine(item_key=key, article=article, nm_id=1, sku=None, barcode=key,
                          tech_size="0", quantity=quantity, accepted_quantity=accepted,
                          ready_for_sale_quantity=None)


def test_supply_items_are_replaced_when_the_supply_is_read_again(conn):
    repo = PostgresSupplyRepository(conn)
    repo.upsert("skazka", [SupplyBundle(make_supply(), (make_item("b1"), make_item("b2")))])
    assert rows(conn, "SELECT COUNT(*) FROM supply_items") == [(2,)]

    # accepted at the warehouse: one line gone, the other accepted, status moved on
    repo.upsert("skazka", [SupplyBundle(make_supply(status="accepted", quantity=6.0), (make_item("b1", accepted=6.0),))])
    assert rows(conn, "SELECT COUNT(*) FROM supplies") == [(1,)]
    assert rows(conn, "SELECT status, quantity FROM supplies") == [("accepted", 6)]
    assert rows(conn, "SELECT item_key, accepted_quantity FROM supply_items") == [("b1", 6)]


def test_duplicate_item_keys_inside_one_supply_are_summed_not_dropped(conn):
    PostgresSupplyRepository(conn).upsert(
        "skazka", [SupplyBundle(make_supply(), (make_item("b1", quantity=4.0), make_item("b1", quantity=3.0)))]
    )
    assert rows(conn, "SELECT quantity FROM supply_items") == [(7,)]


def test_a_supply_without_items_keeps_its_header_and_a_reread_does_not_touch_other_supplies(conn):
    repo = PostgresSupplyRepository(conn)
    repo.upsert("skazka", [SupplyBundle(make_supply("s1"), (make_item("b1"),)), SupplyBundle(make_supply("s2"), ())])
    repo.upsert("skazka", [SupplyBundle(make_supply("s2", status="accepted"), (make_item("b9"),))])
    assert rows(conn, "SELECT supply_key, item_key FROM supply_items ORDER BY 1") == [("s1", "b1"), ("s2", "b9")]
    # the same key in another marketplace/account is a different supply
    repo.upsert("skazka", [SupplyBundle(make_supply("s1", marketplace="ozon"), (make_item("o1"),))])
    repo.upsert("timeless", [SupplyBundle(make_supply("s1"), (make_item("t1"),))])
    assert rows(conn, "SELECT COUNT(*) FROM supplies") == [(4,)]


def test_supply_items_view_joins_header_and_lines(conn):
    PostgresSupplyRepository(conn).upsert("skazka", [SupplyBundle(make_supply(), (make_item("b1", article="PT1/0-0-0/1"),))])
    assert rows(conn, "SELECT marketplace, article, warehouse_name, status, quantity FROM supply_items_v") == [
        ("wb", "PT1/0-0-0/1", "СЦ Радумля", "planned", 6),
    ]


def make_funnel(day, nm=1, opens=100, orders=2) -> WbFunnelLine:
    return WbFunnelLine(day=day, nm_id=nm, vendor_code="A", subject_name="s", open_count=opens, cart_count=10,
                        order_count=orders, order_sum=200.0, buyout_count=1, buyout_sum=100.0, cancel_count=0,
                        cancel_sum=0.0, add_to_wishlist=3, product_rating=10, feedback_rating=4.8)


def test_funnel_upsert_is_rerun_safe_and_remembers_loaded_days(conn):
    repo = PostgresWbFunnelRepository(conn)
    d1, d2 = date(2026, 9, 29), date(2026, 9, 30)
    repo.upsert("skazka", [make_funnel(d1, 1), make_funnel(d1, 2), make_funnel(d2, 1)])
    repo.upsert("skazka", [make_funnel(d1, 1, opens=150)])  # a settled re-pull replaces the row
    assert rows(conn, "SELECT COUNT(*) FROM wb_funnel_daily") == [(3,)]
    assert rows(conn, "SELECT open_count FROM wb_funnel_daily WHERE nm_id = 1 AND day = %s", (d1,)) == [(150,)]
    assert repo.loaded_days("skazka") == {d1, d2}
    assert repo.loaded_days("timeless") == set()


def test_funnel_rates_view_divides_safely(conn):
    repo = PostgresWbFunnelRepository(conn)
    repo.upsert("skazka", [make_funnel(date(2026, 9, 29), 1, opens=100, orders=5), make_funnel(date(2026, 9, 29), 2, opens=0, orders=0)])
    result = {r[0]: r[1:] for r in rows(conn, "SELECT nm_id, open_to_order, open_to_cart, order_to_buyout FROM wb_funnel_rates_v")}
    assert float(result[1][0]) == pytest.approx(0.05) and float(result[1][1]) == pytest.approx(0.1)
    assert result[2][0] is None and result[2][1] is None  # zero opens: NULL, not a division error


def make_promo(promo_id="1", marketplace="wb", participating=3) -> PromotionLine:
    return PromotionLine(marketplace=marketplace, promo_id=promo_id, name="Новинки", promo_type="regular",
                         start_at=datetime(2026, 8, 1, tzinfo=timezone.utc), end_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
                         description="d", potential_count=10, participating_count=participating,
                         discount_type=None, discount_value=None)


def make_promo_item(item_id, plan_price=100.0) -> PromotionItemLine:
    return PromotionItemLine(item_id=item_id, in_action=True, price=200.0, plan_price=plan_price,
                             discount=50.0, plan_discount=50.0, stock=None)


def test_promotion_history_is_kept_and_first_seen_never_moves(conn):
    first_day, later = date(2026, 9, 1), date(2026, 9, 20)
    PostgresPromotionRepository(conn, lambda: first_day).upsert(
        "skazka", [PromotionBundle(make_promo(), (make_promo_item(1), make_promo_item(2)))]
    )
    # three weeks later the product 2 has left the action and the description is no longer sent
    later_promo = make_promo(participating=1)
    later_promo = PromotionLine(**{**later_promo.__dict__, "description": None})
    PostgresPromotionRepository(conn, lambda: later).upsert(
        "skazka", [PromotionBundle(later_promo, (make_promo_item(1, plan_price=90.0),))]
    )
    assert rows(conn, "SELECT first_seen_date, participating_count, description FROM promotions") == [(first_day, 1, "d")]
    assert rows(conn, "SELECT item_id, plan_price, first_seen_date, last_seen_date FROM promotion_items ORDER BY item_id") == [
        (1, 90, first_day, later),
        (2, 100, first_day, first_day),  # never deleted: it was in the action until the 1st
    ]


def test_promotion_items_view_resolves_articles_for_both_marketplaces(conn):
    day = date(2026, 9, 30)
    PostgresWbPriceRepository(conn).save_snapshot("skazka", day, [WbPriceLine(1316382557, "PT1/0-0-0/1", "0", 1, 100, 0, 100)])
    PostgresOzonStockRepository(conn).save_snapshot("skazka", day, [OzonStockLine("PT2/0-0-0/1", 520687888, 5, "fbo", 1, 0)])
    repo = PostgresPromotionRepository(conn, lambda: day)
    repo.upsert("skazka", [
        PromotionBundle(make_promo("10", "wb"), (make_promo_item(1316382557), make_promo_item(999))),
        PromotionBundle(make_promo("20", "ozon"), (make_promo_item(520687888),)),
    ])
    result = rows(conn, "SELECT marketplace, item_id, article FROM promotion_items_v ORDER BY marketplace, item_id")
    assert result == [("ozon", 520687888, "PT2/0-0-0/1"), ("wb", 999, None), ("wb", 1316382557, "PT1/0-0-0/1")]


def test_ozon_warehouse_stock_snapshot_per_day_and_deduplicated(conn):
    repo = PostgresOzonWarehouseStockRepository(conn)

    def line(warehouse, free, offer="A"):
        return OzonWarehouseStockLine(offer, 9, "n", warehouse, free, 1, 2)

    d1, d2 = date(2026, 9, 29), date(2026, 9, 30)
    assert repo.save_snapshot("skazka", d1, [line("ХОРУГВИНО_РФЦ", 3), line("ХОРУГВИНО_РФЦ", 4), line("ПУШКИНО_1", 5)]) == 2
    repo.save_snapshot("skazka", d1, [line("ХОРУГВИНО_РФЦ", 7)])  # same day again
    repo.save_snapshot("skazka", d2, [line("ХОРУГВИНО_РФЦ", 1)])
    assert rows(conn, "SELECT snapshot_date, warehouse_name, free_to_sell FROM ozon_warehouse_stocks ORDER BY 1, 2") == [
        (d1, "ПУШКИНО_1", 5), (d1, "ХОРУГВИНО_РФЦ", 7), (d2, "ХОРУГВИНО_РФЦ", 1),
    ]
