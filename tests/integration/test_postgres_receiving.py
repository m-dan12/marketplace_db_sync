"""Integration tests (real Postgres) for the receipts: Selsup inbound orders and
the `receiving_events_v` view over WB / Ozon supplies and Selsup. Skipped
without TEST_DATABASE_URL, like the others."""
import os
from datetime import datetime, timezone

import pytest

from domain.models import (
    SelsupInboundBundle,
    SelsupInboundItemLine,
    SelsupInboundOrderLine,
    SupplyBundle,
    SupplyItemLine,
    SupplyLine,
)
from infrastructure.persistence.postgres.connection import apply_schema, connect
from infrastructure.persistence.postgres.repositories_receiving import PostgresSelsupInboundRepository
from infrastructure.persistence.postgres.repositories_supply_demand import PostgresSupplyRepository

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set"
)

TABLES = "supplies supply_items selsup_inbound_orders selsup_inbound_items".split()
VIEW_COLUMNS = (
    "source, account, external_supply_id, external_line_id, sku_raw, quantity_declared, "
    "quantity_accepted, status_raw, receipt_number, supplier_name, order_name"
)


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


def supply(marketplace, key, status, receipt_number=None, fact_date=None):
    return SupplyLine(
        marketplace=marketplace, supply_key=key, order_id="9", created_at=None, planned_date=None,
        fact_date=fact_date, updated_at=datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc), status=status,
        warehouse_name="СЦ Радумля", actual_warehouse_name=None, transit_warehouse_name=None,
        is_crossdock=None, quantity=680, accepted_quantity=623, ready_for_sale_quantity=None,
        receipt_number=receipt_number,
    )


def item(key, article, quantity, accepted=None):
    return SupplyItemLine(item_key=key, article=article, nm_id=1, sku=None, barcode=key,
                          tech_size="0", quantity=quantity, accepted_quantity=accepted,
                          ready_for_sale_quantity=None)


def inbound(order_id, invoice, status, lines):
    return SelsupInboundBundle(
        order=SelsupInboundOrderLine(
            order_id=order_id, receipt_number=invoice, order_name="неделя 35 цех Химки",
            supplier_id="4", supplier_name="Химки", status=status, warehouse_id="10020",
            created_at=datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc),
            closed_at=datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc),
        ),
        items=tuple(
            SelsupInboundItemLine(line_id=lid, product_id=pid, article=art, barcode=None,
                                  quantity=q, introduced_quantity=got)
            for lid, pid, art, q, got in lines
        ),
    )


def test_view_takes_only_accepted_wb_and_completed_ozon(conn):
    repo = PostgresSupplyRepository(conn)
    repo.upsert("milky_garden", [
        SupplyBundle(supply("wb", "s41453541", "accepted", "41453541",
                            datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)),
                     (item("b1", "MY1", 10, 8), item("b2", "MY2", 5, 5))),
        SupplyBundle(supply("wb", "s41453542", "unloading_allowed", "41453542"), (item("b3", "MY3", 7, 0),)),
        SupplyBundle(supply("ozon", "s2000068238169", "COMPLETED", "2000068238169"), (item("o1", "MY4", 67),)),
        SupplyBundle(supply("ozon", "s2000068238170", "ACCEPTANCE_AT_STORAGE_WAREHOUSE", "2000068238170"),
                     (item("o2", "MY5", 9),)),
    ])

    found = rows(conn, f"SELECT {VIEW_COLUMNS} FROM receiving_events_v ORDER BY source, external_line_id")

    assert [(r[0], r[2], r[3], r[4], r[5], r[6], r[8]) for r in found] == [
        ("ozon", "s2000068238169", "o1", "MY4", 67, 67, "2000068238169"),  # declared taken as received
        ("wb", "s41453541", "b1", "MY1", 10, 8, "41453541"),
        ("wb", "s41453541", "b2", "MY2", 5, 5, "41453541"),
    ]


def test_view_derives_receipt_number_of_old_rows_from_the_supply_key(conn):
    PostgresSupplyRepository(conn).upsert("skazka", [
        SupplyBundle(supply("wb", "s77", "accepted", receipt_number=None,
                            fact_date=datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)),
                     (item("b1", "PT1", 3, 3),)),
    ])

    [(source, receipt)] = rows(conn, "SELECT source, receipt_number FROM receiving_events_v")

    assert (source, receipt) == ("wb", "77")


def test_selsup_inbound_upsert_updates_lines_in_place_and_keeps_line_ids(conn):
    repo = PostgresSelsupInboundRepository(conn)
    repo.upsert("timeless", [inbound(1325198, "638", "CREATED", [("71", 900, "6432/5-0-0/1", 10, 0)])])
    first_id = rows(conn, "SELECT id FROM selsup_inbound_items")[0][0]

    # the order is posted: the same line now has the received quantity
    repo.upsert("timeless", [inbound(1325198, "638", "FINISHED", [("71", 900, "6432/5-0-0/1", 10, 8)])])

    items = rows(conn, "SELECT id, introduced_quantity FROM selsup_inbound_items")
    assert items == [(first_id, 8)]
    assert rows(conn, "SELECT status FROM selsup_inbound_orders") == [("FINISHED",)]


def test_view_selsup_row_shape_and_unknown_article_falls_back_to_product_id(conn):
    PostgresSelsupInboundRepository(conn).upsert("timeless", [
        inbound(1325198, "638", "FINISHED", [("71", 900, "6432/5-0-0/1", 10, 8), ("72", 901, None, 2, 2)]),
    ])

    found = rows(conn, f"SELECT {VIEW_COLUMNS} FROM receiving_events_v ORDER BY external_line_id")

    assert [(r[0], r[1], r[2], r[3], r[4], r[6], r[8], r[9]) for r in found] == [
        ("selsup", "timeless", "1325198", "71", "6432/5-0-0/1", 8, "638", "Химки"),
        ("selsup", "timeless", "1325198", "72", "901", 2, "638", "Химки"),
    ]


def test_same_invoice_number_in_two_cabinets_stays_two_orders(conn):
    repo = PostgresSelsupInboundRepository(conn)
    repo.upsert("timeless", [inbound(1, "638", "FINISHED", [("1", 1, "T/1", 1, 1)])])
    repo.upsert("skazka", [inbound(2, "638", "FINISHED", [("1", 1, "S/1", 1, 1)])])

    found = rows(conn, "SELECT account, sku_raw FROM receiving_events_v WHERE receipt_number = '638' ORDER BY account")

    assert found == [("skazka", "S/1"), ("timeless", "T/1")]
