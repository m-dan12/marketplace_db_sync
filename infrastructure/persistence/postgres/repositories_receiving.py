"""Postgres adapter for Selsup inbound orders (receipts at our own warehouse)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

import psycopg

from domain.models import SelsupInboundBundle

_ORDER_UPSERT_SQL = """
    INSERT INTO selsup_inbound_orders (
        account, order_id, receipt_number, order_name, supplier_id, supplier_name,
        status, warehouse_id, created_at, closed_at, fetched_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (account, order_id) DO UPDATE SET
        receipt_number = EXCLUDED.receipt_number,
        order_name = EXCLUDED.order_name,
        supplier_id = EXCLUDED.supplier_id,
        supplier_name = EXCLUDED.supplier_name,
        status = EXCLUDED.status,
        warehouse_id = EXCLUDED.warehouse_id,
        created_at = COALESCE(EXCLUDED.created_at, selsup_inbound_orders.created_at),
        closed_at = EXCLUDED.closed_at,
        fetched_at = EXCLUDED.fetched_at
"""

# Lines are upserted, never deleted: Selsup gives every line a stable id, and
# the downstream ledger keys on (account, order_id, line_id).
_ITEM_UPSERT_SQL = """
    INSERT INTO selsup_inbound_items (
        account, order_id, line_id, product_id, article, barcode,
        quantity, introduced_quantity, fetched_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (account, order_id, line_id) DO UPDATE SET
        product_id = EXCLUDED.product_id,
        article = COALESCE(EXCLUDED.article, selsup_inbound_items.article),
        barcode = COALESCE(EXCLUDED.barcode, selsup_inbound_items.barcode),
        quantity = EXCLUDED.quantity,
        introduced_quantity = EXCLUDED.introduced_quantity,
        fetched_at = EXCLUDED.fetched_at
"""


class PostgresSelsupInboundRepository:
    """Upserts inbound order headers and their lines by natural key."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert(self, account: str, bundles: Sequence[SelsupInboundBundle]) -> int:
        if not bundles:
            return 0
        fetched_at = datetime.now(timezone.utc)
        with self._conn.cursor() as cur:
            for bundle in bundles:
                o = bundle.order
                cur.execute(
                    _ORDER_UPSERT_SQL,
                    (
                        account, o.order_id, o.receipt_number, o.order_name, o.supplier_id,
                        o.supplier_name, o.status, o.warehouse_id, o.created_at, o.closed_at,
                        fetched_at,
                    ),
                )
                if bundle.items:
                    cur.executemany(
                        _ITEM_UPSERT_SQL,
                        [
                            (
                                account, o.order_id, i.line_id, i.product_id, i.article, i.barcode,
                                i.quantity, i.introduced_quantity, fetched_at,
                            )
                            for i in bundle.items
                        ],
                    )
        self._conn.commit()
        return len(bundles)
