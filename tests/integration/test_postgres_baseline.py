"""Integration tests (real Postgres) for the nightly run of the analyst's formula."""
import os
from datetime import date

import pytest

from application.use_cases.baseline_source import BaselineSource
from domain.models import ProductionLine, QuantMultipleLine, SelsupStockLine, WbOrderLine, WbStockLine
from infrastructure.persistence.postgres.connection import apply_schema, connect
from infrastructure.persistence.postgres.repositories import (
    PostgresSelsupStockRepository,
    PostgresWbOrderRepository,
    PostgresWbStockRepository,
)
from infrastructure.persistence.postgres.repositories_baseline import (
    PostgresBaselineFactsReader,
    PostgresBaselineRepository,
)
from infrastructure.persistence.postgres.repositories_sheets import (
    PostgresProductionRepository,
    PostgresQuantMultipleRepository,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set"
)

TABLES = (
    "wb_orders wb_stocks selsup_stocks sync_runs production_lines production_line_log quant_multiples "
    "article_specs baseline_recommendation"
).split()
ARTICLE = "PT5930/6-17-17/1"
AS_OF = date(2026, 10, 7)


@pytest.fixture()
def conn():
    connection = connect(os.environ["TEST_DATABASE_URL"])
    apply_schema(connection)
    with connection.cursor() as cur:
        cur.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY")
    connection.commit()
    yield connection
    connection.close()


def wb_order(i, day, article=ARTICLE):
    return WbOrderLine(f"s{i}-{day}", day, None, None, None, article, 1, None, None, None, None,
                       None, None, None, 100.0, False, None)


def production(key, direction, status_group, quantity):
    return ProductionLine(
        row_key=key, sheet_row=2, article=ARTICLE, quantity=quantity, region="x", order_text="заказ",
        size_text=None, meters=None, meters2=None, week_number=40, direction=direction, task_total=None,
        task_key="40", task_quantity=None, fact_quantity=None, fact_ship_date=None, fact_ship_raw=None,
        fact_accept_date=None, fact_accept_raw=None, status="s", status_group=status_group,
        week_start=date(2026, 9, 28), week_end=date(2026, 10, 4), receipt_no=None, brand="Timeless", workshop="w",
    )


def test_the_nightly_run_stores_the_formula_result_with_its_inputs(conn):
    # 10 sold in the 7 days before the date, 20 more in the 23 days before them; nothing on the shelves
    orders = [wb_order(i, date(2026, 10, 3)) for i in range(10)] + [wb_order(100 + i, date(2026, 9, 20)) for i in range(20)]
    orders.append(wb_order(999, AS_OF))  # the planning date itself is not counted
    PostgresWbOrderRepository(conn).upsert("skazka", orders)
    PostgresWbStockRepository(conn).save_snapshot("skazka", AS_OF, [WbStockLine(1, ARTICLE, "b", "0", 1.0, "Тула", 0)])
    PostgresSelsupStockRepository(conn).save_snapshot("skazka", AS_OF, [
        SelsupStockLine(10001, "FBS", 1, ARTICLE, None, None, "c", 4, 4, 0, None)])
    PostgresQuantMultipleRepository(conn).upsert("all", [
        QuantMultipleLine("/6-17-17/", None, None, None, None, None, None, None, 8)])
    PostgresProductionRepository(conn).upsert("all", [
        production("a", "wb", "in_production", 5),
        production("b", "sklad", "in_acceptance", 3),
        production("c", "wb", "released", 100),  # already on the shelf, not "in progress"
    ])
    with conn.cursor() as cur:
        cur.execute("INSERT INTO article_specs (article, purpose, fetched_at) VALUES (%s, 'постельное', now())", (ARTICLE,))
    conn.commit()

    source = BaselineSource(PostgresBaselineFactsReader(conn), as_of_provider=lambda: AS_OF)
    rows = source.fetch("all")
    assert [r.article for r in rows] == [ARTICLE]
    facts = rows[0].inputs
    assert (facts.wb.sales_7d, facts.wb.sales_30d, facts.wb.sales_prev_30d) == (10, 30, 0)
    assert (facts.wb.stock, facts.wb.in_production, facts.in_production_sklad, facts.stock_fbs) == (0, 5, 3, 4)
    assert (facts.quant, facts.category) == (8, "постельное")

    assert PostgresBaselineRepository(conn).save_snapshot("all", AS_OF, rows) == 1
    PostgresBaselineRepository(conn).save_snapshot("all", AS_OF, rows)  # re-run replaces the date
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), MAX(need), MAX(inputs->'wb'->>'sales_30d') FROM baseline_recommendation")
        count, need, sales_30d = cur.fetchone()
    assert count == 1 and float(need) % 8 == 0 and float(sales_30d) == 30
