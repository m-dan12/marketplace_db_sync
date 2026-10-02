"""Integration tests against a real Postgres. Skipped unless TEST_DATABASE_URL
points at a throw-away database (the tables are truncated):

    TEST_DATABASE_URL=postgresql://postgres@localhost:55432/mdb_test pytest tests/integration
"""
import os
from datetime import date, datetime, timezone

import pytest

from domain.article import parse_article
from domain.models import (
    OzonOrderLine,
    OzonPriceLine,
    OzonStockLine,
    SelsupMovementLine,
    SelsupStockLine,
    WbAdStatLine,
    WbOrderLine,
    WbPriceLine,
    WbStockLine,
)
from infrastructure.persistence.postgres.connection import apply_schema, connect
from infrastructure.persistence.postgres.repositories import (
    PostgresOzonOrderRepository,
    PostgresOzonStockRepository,
    PostgresSelsupStockRepository,
    PostgresWbOrderRepository,
    PostgresWbStockRepository,
)
from infrastructure.persistence.postgres.repositories_ml import (
    PostgresArticleRepository,
    PostgresBackfillRepository,
    PostgresOzonPriceRepository,
    PostgresSelsupMovementRepository,
    PostgresWbAdStatRepository,
    PostgresWbPriceRepository,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set"
)

TABLES = (
    "wb_orders wb_sales wb_stocks ozon_orders ozon_stocks selsup_stocks sync_runs wb_prices "
    "ozon_prices wb_ad_stats selsup_movements dim_article backfill_files wb_funnel_daily supply_items "
    "supplies ozon_warehouse_stocks production_lines article_specs"
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


def scalar(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


def test_schema_is_idempotent(conn):
    apply_schema(conn)
    apply_schema(conn)


def test_price_snapshots_are_per_day_and_rerun_safe(conn):
    wb = PostgresWbPriceRepository(conn)
    rows = [WbPriceLine(1, "PT1/0-0-0/1", "0", 10, 1000, 10, 900),
            WbPriceLine(1, "PT1/0-0-0/1", "1", 11, 1100, 10, 990)]
    wb.save_snapshot("skazka", date(2026, 9, 29), rows)
    wb.save_snapshot("skazka", date(2026, 9, 29), rows)  # same day again: no duplicates
    wb.save_snapshot("skazka", date(2026, 9, 30), [WbPriceLine(1, "PT1/0-0-0/1", "0", 10, 1200, 10, 1080)])
    assert scalar(conn, "SELECT COUNT(*) FROM wb_prices") == 3
    assert scalar(conn, "SELECT price FROM wb_prices WHERE snapshot_date='2026-09-30'") == 1200

    oz = PostgresOzonPriceRepository(conn)
    oz.save_snapshot("skazka", date(2026, 9, 29), [OzonPriceLine("A", 5, 602, 1189, 494, 602)])
    oz.save_snapshot("skazka", date(2026, 9, 29), [OzonPriceLine("A", 5, 650, 1189, 494, 650)])
    assert scalar(conn, "SELECT COUNT(*) FROM ozon_prices") == 1
    assert scalar(conn, "SELECT price FROM ozon_prices") == 650


def test_ad_stats_fold_duplicates_and_upsert(conn):
    repo = PostgresWbAdStatRepository(conn)
    day = date(2026, 9, 19)

    def line(views, clicks, spend, orders=0, carts=0):
        return WbAdStatLine(day, 5, 7, "активна", views, clicks, 0, 0, spend, orders, carts, None)

    written = repo.upsert("skazka", [line(100, 4, 40.0, 1, 2), line(50, 1, 10.0, 0, 1)])
    assert written == 1  # two apps of the same product fold into one row
    with conn.cursor() as cur:
        cur.execute("SELECT views, clicks, spend, orders, carts, ctr, cpc FROM wb_ad_stats")
        views, clicks, spend, orders, carts, ctr, cpc = cur.fetchone()
    assert (views, clicks, float(spend), orders, carts) == (150, 5, 50.0, 1, 3)
    assert (float(ctr), float(cpc)) == (pytest.approx(3.33), pytest.approx(10.0))

    repo.upsert("skazka", [line(200, 6, 60.0)])  # a later, settled pull replaces the row
    assert scalar(conn, "SELECT COUNT(*) FROM wb_ad_stats") == 1
    assert scalar(conn, "SELECT views FROM wb_ad_stats") == 200


def make_movement(**overrides) -> SelsupMovementLine:
    base = dict(
        movement_type="Приёмка", operation="PUT", moved_at=datetime(2026, 9, 29, 14, 41, 25),
        warehouse_id=10001, order_id=5, order_type="INCOME", sku_id=1, article=None, product_name=None,
        cell_name="c", quantity=2.0, user_id=7,
    )
    base.update(overrides)
    return SelsupMovementLine(**base)


def test_movements_dedupe_between_api_and_backfill_and_fill_article(conn):
    repo = PostgresSelsupMovementRepository(conn)
    repo.upsert("all", [make_movement(), make_movement(), make_movement(sku_id=2)])
    repo.upsert("all", [make_movement(), make_movement(), make_movement(sku_id=2)])  # re-run
    assert scalar(conn, "SELECT COUNT(*) FROM selsup_movements") == 3

    repo.upsert("all", [make_movement(article="PT1/0-0-0/1", product_name="n")])  # richer duplicate
    assert scalar(conn, "SELECT COUNT(*) FROM selsup_movements") == 3
    assert scalar(conn, "SELECT COUNT(*) FROM selsup_movements WHERE article = 'PT1/0-0-0/1'") == 1
    repo.upsert("all", [make_movement(article=None)])  # a poorer one must not erase it
    assert scalar(conn, "SELECT COUNT(*) FROM selsup_movements WHERE article = 'PT1/0-0-0/1'") == 1


def test_movements_view_resolves_account_and_missing_article_through_stocks(conn):
    PostgresSelsupStockRepository(conn).save_snapshot("timeless", date(2026, 9, 30), [
        SelsupStockLine(10001, "FBS", 1, "PT1/0-0-0/1", None, None, "c", 6, 6, 0, None),
    ])
    PostgresSelsupMovementRepository(conn).upsert("all", [
        make_movement(sku_id=1),                       # no article on the row
        make_movement(sku_id=2, article="PT2/0-0-0/1"),  # sku unknown to the stocks
    ])
    with conn.cursor() as cur:
        cur.execute("SELECT sku_id, article, account FROM selsup_movements_v ORDER BY sku_id")
        assert cur.fetchall() == [(1, "PT1/0-0-0/1", "timeless"), (2, "PT2/0-0-0/1", None)]


def test_dim_article_built_from_every_table(conn):
    PostgresWbStockRepository(conn).save_snapshot(
        "skazka", date(2026, 9, 30), [WbStockLine(1, "PT5930/6-17-17/1", "b", "0", 1.0, "Тула", 3)]
    )
    PostgresOzonStockRepository(conn).save_snapshot(
        "skazka", date(2026, 9, 30), [OzonStockLine("151/0-0-25/1", 1, 2, "fbo", 1, 0)]
    )
    PostgresSelsupMovementRepository(conn).upsert("all", [make_movement(article="PT6026/сарафан")])

    repo = PostgresArticleRepository(conn)
    articles = sorted(set(repo.list_known_articles()))
    assert articles == ["151/0-0-25/1", "PT5930/6-17-17/1", "PT6026/сарафан"]
    repo.upsert_many([parse_article(a) for a in articles])
    repo.upsert_many([parse_article(a) for a in articles])  # idempotent
    with conn.cursor() as cur:
        cur.execute("SELECT article, parsed, brand_name, design_code, sheet_size_code FROM dim_article ORDER BY 1")
        assert cur.fetchall() == [
            ("151/0-0-25/1", True, "Timeless", "151", 0),
            ("PT5930/6-17-17/1", True, None, "5930", 17),
            ("PT6026/сарафан", False, None, None, None),
        ]


def test_article_stock_daily_view_sums_places_and_flags_out_of_stock(conn):
    day = date(2026, 9, 30)
    PostgresWbStockRepository(conn).save_snapshot("skazka", day, [
        WbStockLine(1, "A", "b1", "0", 1.0, "Тула", 5),
        WbStockLine(1, "A", "b1", "0", 1.0, "Коледино", 2),
        WbStockLine(1, "A", "b1", "0", 1.0, "Всего находится на складах", 7),  # WB's own total row
        WbStockLine(1, "A", "b1", "0", 1.0, "В пути до получателей", 4),
        WbStockLine(2, "B", "b2", "0", 1.0, "Тула", 0),  # listed with zero -> out of stock
    ])
    PostgresOzonStockRepository(conn).save_snapshot("skazka", day, [
        OzonStockLine("A", 1, 1, "fbo", 3, 0),
        OzonStockLine("A", 1, 1, "fbs", 1, 0),
        OzonStockLine("B", 2, 2, "fbo", 0, 0),
    ])
    PostgresSelsupStockRepository(conn).save_snapshot("skazka", day, [
        SelsupStockLine(10001, "FBS", 1, "A", None, None, "c", 6, 6, 0, None),
        SelsupStockLine(10020, "Kvant", 1, "A", None, None, "c", 9, 9, 0, None),
    ])
    with conn.cursor() as cur:
        cur.execute(
            "SELECT article, wb_qty, ozon_qty, selsup_fbs_qty, selsup_kvant_qty, wb_out, ozon_out "
            "FROM article_stock_daily ORDER BY article"
        )
        assert cur.fetchall() == [
            ("A", 7, 4, 6, 9, False, False),
            ("B", 0, 0, 0, 0, True, True),
        ]
        cur.execute("SELECT COUNT(*) FROM stock_days WHERE snapshot_date = %s", (day,))
        assert cur.fetchone()[0] == 3  # wb, ozon, selsup


def test_wb_article_missing_from_the_stock_report_is_out_of_stock_when_catalogued(conn):
    """WB's report omits articles with no stock; the price snapshot is the catalogue."""
    day = date(2026, 9, 30)
    PostgresWbStockRepository(conn).save_snapshot(
        "skazka", day, [WbStockLine(1, "IN", "b", "0", 1.0, "Тула", 5)]
    )
    PostgresWbPriceRepository(conn).save_snapshot("skazka", day, [
        WbPriceLine(1, "IN", "0", 10, 100, 0, 100),
        WbPriceLine(2, "GONE", "0", 11, 100, 0, 100),  # catalogued, absent from the stock report
    ])
    PostgresOzonPriceRepository(conn).save_snapshot("skazka", day, [OzonPriceLine("GONE", 2, 10, 20, 5, 10)])
    with conn.cursor() as cur:
        cur.execute(
            "SELECT article, wb_qty, wb_listed, wb_out, ozon_listed, ozon_out "
            "FROM article_stock_daily ORDER BY article"
        )
        assert cur.fetchall() == [
            ("GONE", 0, True, True, True, True),
            ("IN", 5, True, False, False, False),
        ]
        cur.execute("SELECT wb_listed_days, wb_out_days FROM out_of_stock_days_30 WHERE article = 'GONE'")
        assert cur.fetchone() == (1, 1)


def test_out_of_stock_days_count_only_listed_days_in_the_last_30(conn):
    wb = PostgresWbStockRepository(conn)

    def snapshot(day, qty):
        wb.save_snapshot("skazka", day, [WbStockLine(1, "A", "b", "0", 1.0, "Тула", qty)])

    snapshot(date(2026, 8, 1), 0)    # older than 30 days before the latest snapshot: ignored
    snapshot(date(2026, 9, 28), 0)
    snapshot(date(2026, 9, 29), 5)
    snapshot(date(2026, 9, 30), 0)   # 29.09 -> in stock; 28.09 and 30.09 -> out
    # 10.09 has no snapshot at all (a missed sync) and must not count as out of stock
    with conn.cursor() as cur:
        cur.execute("SELECT wb_listed_days, wb_out_days, ozon_listed_days FROM out_of_stock_days_30 WHERE article = 'A'")
        assert cur.fetchone() == (3, 2, 0)


def test_orders_daily_view_counts_units_and_cancellations(conn):
    wb_orders = [
        WbOrderLine(f"s{i}", date(2026, 9, 29), None, None, None, "A", 1, None, None, None, None,
                    None, None, None, 100.0, cancelled, None)
        for i, cancelled in enumerate([False, False, True])
    ]
    PostgresWbOrderRepository(conn).upsert("skazka", wb_orders)
    PostgresOzonOrderRepository(conn).upsert("skazka", [
        OzonOrderLine("p1", "A", "delivered", datetime(2026, 9, 29, 21, 30, tzinfo=timezone.utc),
                      None, None, None, None, 1, "n", 2, 50.0, "RUB"),
        OzonOrderLine("p2", "A", "cancelled", datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc),
                      None, None, None, None, 1, "n", 1, 50.0, "RUB"),
    ])
    with conn.cursor() as cur:
        cur.execute(
            "SELECT marketplace, day, units, cancelled_units, revenue FROM orders_daily ORDER BY marketplace, day"
        )
        rows = cur.fetchall()
    assert rows == [
        ("ozon", date(2026, 9, 29), 0, 1, None),
        ("ozon", date(2026, 9, 30), 2, 0, 100),  # 21:30 UTC is already the 30th in Moscow
        ("wb", date(2026, 9, 29), 2, 1, 200),
    ]


def test_backfill_bookkeeping(conn):
    repo = PostgresBackfillRepository(conn)
    assert repo.loaded_file_ids() == set()
    repo.mark_loaded("id:2026-09-30", "wb_prices", "skazka", "f.xlsx", date(2026, 9, 30), 10)
    repo.mark_loaded("id:2026-09-30", "wb_prices", "skazka", "f.xlsx", date(2026, 9, 30), 12)
    assert repo.loaded_file_ids() == {"id:2026-09-30"}
    assert scalar(conn, "SELECT rows_loaded FROM backfill_files") == 12
