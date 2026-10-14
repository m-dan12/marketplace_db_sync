"""Integration tests (real Postgres): feature rows, outcome windows and the evaluation samples."""
import os
from datetime import date

import pytest

from domain.forecast_eval import evaluate
from domain.models import WbOrderLine, WbStockLine
from infrastructure.persistence.postgres.connection import apply_schema, connect
from infrastructure.persistence.postgres.repositories import PostgresWbOrderRepository, PostgresWbStockRepository
from infrastructure.persistence.postgres.repositories_features import (
    PostgresFeatureRepository,
    PostgresModelRunRepository,
)
from interface.cli.ml_commands import build_features

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set"
)

TABLES = (
    "wb_orders wb_stocks ozon_orders selsup_stocks sync_runs production_lines production_line_log quant_multiples "
    "article_specs baseline_recommendation features_daily model_runs product_cards wb_article_pricing wb_prices"
).split()
ARTICLE = "PT5930/6-17-17/1"


@pytest.fixture()
def conn():
    connection = connect(os.environ["TEST_DATABASE_URL"])
    apply_schema(connection)
    with connection.cursor() as cur:
        cur.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY")
    connection.commit()
    yield connection
    connection.close()


def wb_order(i, day):
    return WbOrderLine(f"s{i}-{day}", day, None, None, None, ARTICLE, 1, None, None, None, None,
                       None, None, None, 100.0, False, None)


def one_order_a_day(first, last):
    days = (last - first).days + 1
    return [wb_order(i, date.fromordinal(first.toordinal() + i)) for i in range(days)]


def test_features_carry_the_formula_inputs_and_the_calendar(conn):
    as_of = date(2026, 10, 7)
    PostgresWbOrderRepository(conn).upsert("skazka", one_order_a_day(date(2026, 8, 1), date(2026, 10, 6)))
    PostgresWbStockRepository(conn).save_snapshot("skazka", as_of, [WbStockLine(1, ARTICLE, "b", "0", 1.0, "Склад WB РФ", 12)])

    baseline_rows, feature_rows = build_features(conn, as_of)
    assert baseline_rows >= 1 and feature_rows == baseline_rows
    with conn.cursor() as cur:
        cur.execute("SELECT wb_s7, wb_s30, wb_sp30, wb_stock, dow, month FROM features_daily WHERE article = %s",
                    (ARTICLE,))
        assert cur.fetchall() == [(7, 30, 30, 12, 3, 10)]  # 2026-10-07 is a Wednesday
    # a second build replaces, it does not duplicate
    assert build_features(conn, as_of)[1] == feature_rows
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM features_daily")
        assert cur.fetchone()[0] == feature_rows


def test_outcome_windows_are_complete_only_when_the_orders_cover_them(conn):
    PostgresWbOrderRepository(conn).upsert("skazka", one_order_a_day(date(2026, 8, 1), date(2026, 10, 6)))
    for day in (date(2026, 9, 1), date(2026, 9, 20)):
        PostgresWbStockRepository(conn).save_snapshot("skazka", day, [WbStockLine(1, ARTICLE, "b", "0", 1.0, "Склад WB РФ", 5)])
        build_features(conn, day)
    with conn.cursor() as cur:
        cur.execute("SELECT as_of, wb_next_7, wb_next_30, complete_7, complete_30 FROM demand_outcome_v ORDER BY as_of")
        assert cur.fetchall() == [
            (date(2026, 9, 1), 7, 30, True, True),
            (date(2026, 9, 20), 7, 17, True, False),  # one order a day: 17 days are known, 30 are not
        ]


def test_samples_feed_the_evaluation_and_the_run_is_logged(conn):
    PostgresWbOrderRepository(conn).upsert("skazka", one_order_a_day(date(2026, 8, 1), date(2026, 10, 6)))
    day = date(2026, 9, 1)
    PostgresWbStockRepository(conn).save_snapshot("skazka", day, [WbStockLine(1, ARTICLE, "b", "0", 1.0, "Склад WB РФ", 5)])
    build_features(conn, day)

    repo = PostgresFeatureRepository(conn)
    samples = repo.samples(30, date(2026, 8, 1), date(2026, 10, 7))
    assert {s.channel for s in samples} == {"wb", "ozon"}
    wb = next(s for s in samples if s.channel == "wb" and s.article == ARTICLE)
    assert (wb.actual, wb.sales_30d) == (30.0, 30.0)  # one a day before and one a day after

    report = evaluate(samples, 30)
    assert report["last_30_days"]["wb"].wape == pytest.approx(0.0)
    PostgresModelRunRepository(conn).save(30, day, day, None, len(samples), report)
    with conn.cursor() as cur:
        cur.execute("SELECT horizon_days, samples, results -> 'last_30_days' -> 'wb' ->> 'wape' FROM model_runs")
        assert cur.fetchall() == [(30, len(samples), "0.0")]
