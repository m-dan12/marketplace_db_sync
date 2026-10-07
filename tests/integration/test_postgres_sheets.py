"""Integration tests (real Postgres) for the production / planning sheet data.
Skipped without TEST_DATABASE_URL, like the others."""
import dataclasses
import os
from datetime import date, timedelta

import pytest

from domain.models import CostModelLine, FabricInvoiceLine, FabricReceiptLine, FabricStockLine, ProductionLine, QuantMultipleLine, WbArticlePricingLine
from infrastructure.persistence.postgres.connection import apply_schema, connect
from infrastructure.persistence.postgres.repositories_sheets import (
    PostgresCostModelRepository,
    PostgresFabricInvoiceRepository,
    PostgresFabricReceiptRepository,
    PostgresWbArticlePricingRepository,
    PostgresFabricStockRepository,
    PostgresProductionRepository,
    PostgresQuantMultipleRepository,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set"
)

TABLES = ("production_lines production_line_log quant_multiples fabric_stock cost_models wb_article_pricing "
          "fabric_receipts fabric_invoices").split()


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


def line(key="k1", article="A/1-0-0/1", order_text="заказ Задание", **overrides) -> ProductionLine:
    base = dict(
        row_key=key, sheet_row=2, article=article, quantity=2, region="Центральный", order_text=order_text,
        size_text=None, meters=7.28, meters2=0.0, week_number=14, direction="sklad", task_total=1137,
        task_key="14т", task_quantity=1137, fact_quantity=None, fact_ship_date=None, fact_ship_raw=None,
        fact_accept_date=None, fact_accept_raw=None, status="в производстве", status_group="in_production",
        week_start=date(2026, 3, 30), week_end=date(2026, 4, 5), receipt_no=None, brand="Timeless", workshop="Солях",
    )
    return ProductionLine(**{**base, **overrides})


def test_first_load_inserts_without_log_and_a_repeat_changes_nothing(conn):
    repo = PostgresProductionRepository(conn)
    assert repo.upsert("all", [line("a"), line("b")]) == 2
    repo.upsert("all", [line("a"), line("b")])
    assert rows(conn, "SELECT COUNT(*) FROM production_lines") == [(2,)]
    assert rows(conn, "SELECT COUNT(*) FROM production_line_log") == [(0,)]


def test_a_changed_status_and_fact_are_logged_with_before_and_after(conn):
    repo = PostgresProductionRepository(conn)
    repo.upsert("all", [line("a")])
    repo.upsert("all", [line("a", status="выпущено", status_group="released", fact_quantity=1097,
                             fact_ship_date=date(2026, 4, 21), fact_ship_raw="21.04.2026", receipt_no="41268617")])
    assert rows(conn, "SELECT status, fact_quantity, fact_ship_date FROM production_lines") == [
        ("выпущено", 1097, date(2026, 4, 21)),
    ]
    event, before, after = rows(conn, "SELECT event, before, after FROM production_line_log")[0]
    assert event == "changed"
    assert before["status"] == "в производстве" and before["fact_quantity"] is None
    assert after["status"] == "выпущено" and after["fact_quantity"] == 1097
    assert after["fact_ship_date"] == "2026-04-21" and after["receipt_no"] == "41268617"


def test_first_seen_stays_and_last_seen_moves(conn):
    repo = PostgresProductionRepository(conn)
    repo.upsert("all", [line("a")])
    first = rows(conn, "SELECT first_seen_at, last_seen_at FROM production_lines")[0]
    repo.upsert("all", [line("a")])
    second = rows(conn, "SELECT first_seen_at, last_seen_at FROM production_lines")[0]
    assert second[0] == first[0] and second[1] > first[1]


def test_a_row_that_leaves_the_sheet_is_soft_deleted_and_can_come_back(conn):
    repo = PostgresProductionRepository(conn)
    repo.upsert("all", [line("a"), line("b"), line("c")])
    repo.upsert("all", [line("a"), line("b")])  # c was removed (the plan was revised)
    assert rows(conn, "SELECT row_key FROM production_lines_v ORDER BY 1") == [("a",), ("b",)]
    assert rows(conn, "SELECT row_key FROM production_lines WHERE deleted_at IS NOT NULL") == [("c",)]
    assert rows(conn, "SELECT row_key, event FROM production_line_log") == [("c", "deleted")]

    repo.upsert("all", [line("a"), line("b"), line("c")])
    assert rows(conn, "SELECT COUNT(*) FROM production_lines_v") == [(3,)]
    assert [r[0] for r in rows(conn, "SELECT event FROM production_line_log ORDER BY id")] == ["deleted", "restored"]


def test_a_truncated_sheet_is_refused_and_nothing_is_deleted(conn):
    repo = PostgresProductionRepository(conn)
    repo.upsert("all", [line(f"k{i}") for i in range(10)])
    with pytest.raises(ValueError, match="refusing"):
        repo.upsert("all", [line("k0"), line("k1")])
    conn.rollback()
    assert rows(conn, "SELECT COUNT(*) FROM production_lines_v") == [(10,)]
    # an intended clean-up is possible with a lower ratio
    PostgresProductionRepository(conn, min_kept_ratio=0.0).upsert("all", [line("k0"), line("k1")])
    assert rows(conn, "SELECT COUNT(*) FROM production_lines_v") == [(2,)]


def test_view_tells_sewing_from_transfers_and_resorting(conn):
    PostgresProductionRepository(conn).upsert("all", [
        line("a", order_text="заказ неделя 22 задание №8 цех Инна"),
        line("b", order_text="перемещение со склада", workshop="ФБС"),
        line("c", order_text="подсортировка фбс", workshop="ФБС"),
    ])
    assert rows(conn, "SELECT row_key, kind FROM production_lines_v ORDER BY 1") == [
        ("a", "sewing"), ("b", "transfer"), ("c", "resort"),
    ]


def test_lead_times_measure_the_way_and_flag_typos(conn):
    done = dict(status="выпущено", status_group="released", week_start=date(2026, 8, 3))
    PostgresProductionRepository(conn).upsert("all", [
        line("ok", **done, fact_ship_date=date(2026, 8, 20), fact_accept_date=date(2026, 8, 27)),
        line("typo", **done, fact_ship_date=date(2026, 8, 20), fact_accept_date=date(2025, 8, 27)),
        line("open", **done, fact_ship_date=date(2026, 8, 20)),
        line("move", order_text="перемещение со склада", **done,
             fact_ship_date=date(2026, 8, 20), fact_accept_date=date(2026, 8, 21)),
    ])
    assert rows(conn, "SELECT row_key, days_to_ship, days_ship_to_accept, days_total, is_valid "
                      "FROM lead_times_v ORDER BY 1") == [
        ("ok", 17, 7, 24, True),
        ("typo", 17, -358, -341, False),
    ]


def test_lead_time_summary_uses_valid_recent_lines_per_workshop(conn):
    recent = date.today()
    done = dict(status="выпущено", status_group="released", week_start=recent - timedelta(days=40))
    PostgresProductionRepository(conn).upsert("all", [
        line("a", **done, fact_ship_date=recent - timedelta(days=20), fact_accept_date=recent - timedelta(days=10)),
        line("b", **done, fact_ship_date=recent - timedelta(days=16), fact_accept_date=recent),
        line("bad", **done, fact_ship_date=recent - timedelta(days=16), fact_accept_date=recent - timedelta(days=50)),
    ])
    assert rows(conn, "SELECT workshop, lines, median_days, median_days_to_ship "
                      "FROM lead_times_by_workshop_v") == [("Солях", 2, 35, 22)]


def test_pickup_batches_group_tasks_of_a_workshop_shipped_on_one_day(conn):
    done = dict(status="выпущено", status_group="released", week_start=date(2026, 8, 3),
                fact_accept_date=date(2026, 9, 1))
    PostgresProductionRepository(conn).upsert("all", [
        line("a", **done, task_key="t1", fact_quantity=10, fact_ship_date=date(2026, 8, 20)),
        line("b", **{**done, "week_start": date(2026, 8, 10)}, task_key="t2", fact_quantity=5,
             fact_ship_date=date(2026, 8, 20)),
        line("c", **done, task_key="t3", fact_quantity=7, fact_ship_date=date(2026, 8, 27)),
    ])
    assert rows(conn, "SELECT ship_date, tasks, pieces, fastest_task_days, slowest_task_days, spread_days "
                      "FROM shipment_batches_v ORDER BY ship_date") == [
        (date(2026, 8, 20), 2, 15, 10, 17, 7),
        (date(2026, 8, 27), 1, 7, 24, 24, 0),
    ]


def test_ready_wait_splits_sewing_from_waiting_for_pickup(conn):
    repo = PostgresProductionRepository(conn)
    repo.upsert("all", [line("a"), line("b")])
    repo.upsert("all", [line("a", status="готово,не вывезено", status_group="ready"), line("b")])
    repo.upsert("all", [line("a", status="выпущено", status_group="released",
                             fact_ship_date=date.today() + timedelta(days=3)), line("b")])
    ready = rows(conn, "SELECT row_key, ready_date, days_waiting_pickup FROM ready_wait_v")
    assert ready == [("a", date.today(), 3)]  # b never reached "ready"


def receipt(sheet="21.09-27.09 26", row=5, meters=80.0, **overrides) -> FabricReceiptLine:
    base = dict(sheet=sheet, sheet_row=row, week_start=date(2026, 9, 21), task_number="68", task_text=None,
                received_date=date(2026, 9, 24), workshop="Солях", supplier_text="юниколор", price=210.95,
                nomenclature="рис 1", meters=meters, amount=1.0, document=None, fabric_no="3526",
                fabric_name="Классика", brand="Сказка")
    return FabricReceiptLine(**{**base, **overrides})


def test_fabric_receipts_replace_each_sheet_that_was_read_and_keep_the_others(conn):
    repo = PostgresFabricReceiptRepository(conn)
    repo.upsert("all", [receipt(row=5), receipt(row=6), receipt(sheet="14.09-20.09 26", row=5)])
    # the first sheet was edited: a row went away, another changed; the second sheet was not read
    repo.upsert("all", [receipt(row=5, meters=99.0)])
    assert rows(conn, "SELECT sheet, sheet_row, meters FROM fabric_receipts ORDER BY sheet, sheet_row") == [
        ("14.09-20.09 26", 5, 80), ("21.09-27.09 26", 5, 99),
    ]


def test_task_fabric_matches_tasks_by_the_number_in_the_text(conn):
    PostgresFabricReceiptRepository(conn).upsert("all", [
        receipt(row=5, task_text="неделя 38 №38_00058(MG) цех Солях", received_date=date(2026, 9, 17), meters=100.0),
        receipt(row=6, task_text="неделя 38 №38_00058(MG) цех Солях", received_date=date(2026, 9, 21), meters=50.0),
        receipt(row=7, task_text="неделя 38 №38_00059(С) цех Солях", received_date=date(2026, 9, 18)),
        receipt(row=8, task_text=None, received_date=date(2026, 9, 18)),  # an old sheet: no task number
    ])
    done = dict(status="выпущено", status_group="released", week_start=date(2026, 9, 14),
                order_text="заказ неделя 38 №38_00058(MG) цех Солях", quantity=10,
                fact_ship_date=date(2026, 9, 28), fact_accept_date=date(2026, 9, 30))
    PostgresProductionRepository(conn).upsert("all", [line("a", **done), line("b", **{**done, "quantity": 5})])
    assert rows(conn, "SELECT task_no, pieces, first_received, last_received, meters_received, "
                      "days_fabric_to_ship, days_fabric_to_accept, days_week_to_fabric FROM task_fabric_v") == [
        ("38_00058", 15, date(2026, 9, 17), date(2026, 9, 21), 150, 7, 9, 7),
    ]


def test_fabric_invoices_replace_the_sheet_and_keep_the_claim(conn):
    repo = PostgresFabricInvoiceRepository(conn)
    invoice = FabricInvoiceLine("15.06-21.06 26", 4, date(2026, 6, 15), "18", date(2026, 6, 15), "Солях", "док", 480.0,
                                65000.0, "брак", "№178", 63.0, 9954.0, "пр. отправлена", date(2026, 6, 25))
    repo.upsert("all", [invoice, FabricInvoiceLine(**{**invoice.__dict__, "sheet_row": 6, "claim_no": None})])
    repo.upsert("all", [invoice])
    assert rows(conn, "SELECT sheet_row, claim_no, claim_meters, compensation_date FROM fabric_invoices") == [
        (4, "№178", 63, date(2026, 6, 25)),
    ]


def test_quant_multiples_upsert_updates_the_multiple(conn):
    repo = PostgresQuantMultipleRepository(conn)
    first = QuantMultipleLine("/6-17-17/", "КПБ Евро", 9.58, 5, 3, None, None, None, 5)
    repo.upsert("all", [first])
    repo.upsert("all", [dataclasses.replace(first, quant=4)])
    assert rows(conn, "SELECT size_key, quant FROM quant_multiples") == [("/6-17-17/", 4)]


def test_fabric_stock_is_a_snapshot_per_day_that_replaces_itself(conn):
    repo = PostgresFabricStockRepository(conn)
    d1, d2 = date(2026, 10, 1), date(2026, 10, 2)
    repo.save_snapshot("all", d1, [FabricStockLine("110", 100.0, "тетрагон", "Пэчворк", "Сказка"),
                                   FabricStockLine("110", 50.0, "тетрагон", "Пэчворк", "Сказка"),  # listed twice: summed
                                   FabricStockLine("125", 70.0, None, "Бутоны", None)])
    repo.save_snapshot("all", d2, [FabricStockLine("110", 20.0, "тетрагон", "Пэчворк", "Сказка")])
    repo.save_snapshot("all", d1, [FabricStockLine("110", 90.0, "тетрагон", "Пэчворк", "Сказка")])  # a rerun: 125 is gone
    assert rows(conn, "SELECT snapshot_date, fabric_no, quantity_m FROM fabric_stock ORDER BY 1, 2") == [
        (d1, "110", 90), (d2, "110", 20),
    ]


def test_cost_models_are_a_snapshot_per_day_and_pricing_is_the_latest_state(conn):
    models = PostgresCostModelRepository(conn)
    model = CostModelLine("/4-18-26/1 - перкаль", 164, "1 - перкаль", 6679, 1132.2, 0.55, 0.53, 3139)
    models.save_snapshot("all", date(2026, 10, 6), [model])
    models.save_snapshot("all", date(2026, 10, 6), [model])  # same day again: replaced
    models.save_snapshot("all", date(2026, 10, 7), [CostModelLine(model.model_key, 170, "1 - перкаль", 6679, 1180.0, 0.55, 0.53, 3139)])
    assert rows(conn, "SELECT snapshot_date, cost_total::float FROM cost_models ORDER BY 1") == [
        (date(2026, 10, 6), 1132.2), (date(2026, 10, 7), 1180.0)]

    pricing = PostgresWbArticlePricingRepository(conn)

    def card(cost):
        return WbArticlePricingLine(5, "PT1/4-18-26/1", "Сказка", "Пододеяльники", "/4-18-26/1", cost, 6679, 0.4, 0.45, 3139, 0.5, 0.45)

    pricing.upsert("skazka", [card(300.0)])
    pricing.upsert("skazka", [card(310.0)])
    assert rows(conn, "SELECT category, cost::float FROM wb_article_pricing") == [("Пододеяльники", 310.0)]
