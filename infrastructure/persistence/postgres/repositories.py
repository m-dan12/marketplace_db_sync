"""Postgres adapters for the repository ports (`application/ports.py`).
Raw SQL via psycopg — no ORM, matching the rest of the codebase."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Sequence

import psycopg

from domain.models import (
    OzonOrderLine,
    OzonStockLine,
    SelsupStockLine,
    SyncRunSummary,
    WbOrderLine,
    WbSaleLine,
    WbStockLine,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


_WB_ORDERS_UPSERT_SQL = """
    INSERT INTO wb_orders (
        account, srid, order_date, last_change_date, warehouse_name, region_name,
        supplier_article, nm_id, barcode, subject, brand, tech_size,
        total_price, discount_percent, finished_price, price_with_disc,
        is_cancel, g_number, fetched_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (account, srid) DO UPDATE SET
        order_date = EXCLUDED.order_date,
        last_change_date = EXCLUDED.last_change_date,
        warehouse_name = EXCLUDED.warehouse_name,
        region_name = EXCLUDED.region_name,
        supplier_article = EXCLUDED.supplier_article,
        nm_id = EXCLUDED.nm_id,
        barcode = EXCLUDED.barcode,
        subject = EXCLUDED.subject,
        brand = EXCLUDED.brand,
        tech_size = EXCLUDED.tech_size,
        total_price = EXCLUDED.total_price,
        discount_percent = EXCLUDED.discount_percent,
        finished_price = EXCLUDED.finished_price,
        price_with_disc = EXCLUDED.price_with_disc,
        is_cancel = EXCLUDED.is_cancel,
        g_number = EXCLUDED.g_number,
        fetched_at = EXCLUDED.fetched_at
"""


class PostgresWbOrderRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert(self, account: str, rows: Sequence[WbOrderLine]) -> int:
        if not rows:
            return 0
        fetched_at = _now()
        params = [
            (
                account, r.srid, r.order_date, r.last_change_date, r.warehouse_name, r.region_name,
                r.supplier_article, r.nm_id, r.barcode, r.subject, r.brand, r.tech_size,
                r.total_price, r.discount_percent, r.finished_price, r.price_with_disc,
                r.is_cancel, r.g_number, fetched_at,
            )
            for r in rows
        ]
        with self._conn.cursor() as cur:
            cur.executemany(_WB_ORDERS_UPSERT_SQL, params)
        self._conn.commit()
        return len(rows)


_WB_SALES_UPSERT_SQL = """
    INSERT INTO wb_sales (
        account, sale_id, sale_date, last_change_date, warehouse_name, region_name,
        supplier_article, nm_id, barcode, subject, brand, tech_size,
        total_price, discount_percent, spp, for_pay, finished_price, price_with_disc,
        order_type, g_number, fetched_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (account, sale_id) DO UPDATE SET
        sale_date = EXCLUDED.sale_date,
        last_change_date = EXCLUDED.last_change_date,
        warehouse_name = EXCLUDED.warehouse_name,
        region_name = EXCLUDED.region_name,
        supplier_article = EXCLUDED.supplier_article,
        nm_id = EXCLUDED.nm_id,
        barcode = EXCLUDED.barcode,
        subject = EXCLUDED.subject,
        brand = EXCLUDED.brand,
        tech_size = EXCLUDED.tech_size,
        total_price = EXCLUDED.total_price,
        discount_percent = EXCLUDED.discount_percent,
        spp = EXCLUDED.spp,
        for_pay = EXCLUDED.for_pay,
        finished_price = EXCLUDED.finished_price,
        price_with_disc = EXCLUDED.price_with_disc,
        order_type = EXCLUDED.order_type,
        g_number = EXCLUDED.g_number,
        fetched_at = EXCLUDED.fetched_at
"""


class PostgresWbSaleRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert(self, account: str, rows: Sequence[WbSaleLine]) -> int:
        if not rows:
            return 0
        fetched_at = _now()
        params = [
            (
                account, r.sale_id, r.sale_date, r.last_change_date, r.warehouse_name, r.region_name,
                r.supplier_article, r.nm_id, r.barcode, r.subject, r.brand, r.tech_size,
                r.total_price, r.discount_percent, r.spp, r.for_pay, r.finished_price, r.price_with_disc,
                r.order_type, r.g_number, fetched_at,
            )
            for r in rows
        ]
        with self._conn.cursor() as cur:
            cur.executemany(_WB_SALES_UPSERT_SQL, params)
        self._conn.commit()
        return len(rows)


_WB_STOCKS_UPSERT_SQL = """
    INSERT INTO wb_stocks (
        account, snapshot_date, nm_id, vendor_code, barcode, tech_size, volume,
        warehouse_name, quantity, fetched_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (account, snapshot_date, nm_id, barcode, warehouse_name) DO UPDATE SET
        vendor_code = EXCLUDED.vendor_code,
        tech_size = EXCLUDED.tech_size,
        volume = EXCLUDED.volume,
        quantity = EXCLUDED.quantity,
        fetched_at = EXCLUDED.fetched_at
"""


class PostgresWbStockRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def save_snapshot(self, account: str, snapshot_date: date, rows: Sequence[WbStockLine]) -> int:
        if not rows:
            return 0
        fetched_at = _now()
        params = [
            (
                account, snapshot_date, r.nm_id, r.vendor_code, r.barcode, r.tech_size, r.volume,
                r.warehouse_name, r.quantity, fetched_at,
            )
            for r in rows
        ]
        with self._conn.cursor() as cur:
            cur.executemany(_WB_STOCKS_UPSERT_SQL, params)
        self._conn.commit()
        return len(rows)


_OZON_ORDERS_UPSERT_SQL = """
    INSERT INTO ozon_orders (
        account, posting_number, offer_id, status, order_date, source,
        warehouse_name, city, region, sku, product_name, quantity, price, currency, fetched_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (account, posting_number, offer_id) DO UPDATE SET
        status = EXCLUDED.status,
        order_date = EXCLUDED.order_date,
        source = EXCLUDED.source,
        warehouse_name = EXCLUDED.warehouse_name,
        city = EXCLUDED.city,
        region = EXCLUDED.region,
        sku = EXCLUDED.sku,
        product_name = EXCLUDED.product_name,
        quantity = EXCLUDED.quantity,
        price = EXCLUDED.price,
        currency = EXCLUDED.currency,
        fetched_at = EXCLUDED.fetched_at
"""


class PostgresOzonOrderRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert(self, account: str, rows: Sequence[OzonOrderLine]) -> int:
        if not rows:
            return 0
        fetched_at = _now()
        params = [
            (
                account, r.posting_number, r.offer_id, r.status, r.order_date, r.source,
                r.warehouse_name, r.city, r.region, r.sku, r.product_name, r.quantity, r.price,
                r.currency, fetched_at,
            )
            for r in rows
        ]
        with self._conn.cursor() as cur:
            cur.executemany(_OZON_ORDERS_UPSERT_SQL, params)
        self._conn.commit()
        return len(rows)


_OZON_STOCKS_UPSERT_SQL = """
    INSERT INTO ozon_stocks (
        account, snapshot_date, offer_id, product_id, sku, stock_type, present, reserved, fetched_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (account, snapshot_date, offer_id, stock_type) DO UPDATE SET
        product_id = EXCLUDED.product_id,
        sku = EXCLUDED.sku,
        present = EXCLUDED.present,
        reserved = EXCLUDED.reserved,
        fetched_at = EXCLUDED.fetched_at
"""


class PostgresOzonStockRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def save_snapshot(self, account: str, snapshot_date: date, rows: Sequence[OzonStockLine]) -> int:
        if not rows:
            return 0
        fetched_at = _now()
        params = [
            (
                account, snapshot_date, r.offer_id, r.product_id, r.sku, r.stock_type,
                r.present, r.reserved, fetched_at,
            )
            for r in rows
        ]
        with self._conn.cursor() as cur:
            cur.executemany(_OZON_STOCKS_UPSERT_SQL, params)
        self._conn.commit()
        return len(rows)


_SELSUP_STOCKS_UPSERT_SQL = """
    INSERT INTO selsup_stocks (
        account, snapshot_date, warehouse_id, warehouse_name, sku_id, article, wb_size,
        ozon_article, cell_name, quantity, available_quantity, calculated_quantity,
        modify_date, fetched_at, product_name, category, brand, model_article, purchase_price,
        organization_id
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (account, snapshot_date, warehouse_id, sku_id) DO UPDATE SET
        warehouse_name = EXCLUDED.warehouse_name,
        article = EXCLUDED.article,
        wb_size = EXCLUDED.wb_size,
        ozon_article = EXCLUDED.ozon_article,
        cell_name = EXCLUDED.cell_name,
        quantity = EXCLUDED.quantity,
        available_quantity = EXCLUDED.available_quantity,
        calculated_quantity = EXCLUDED.calculated_quantity,
        modify_date = EXCLUDED.modify_date,
        fetched_at = EXCLUDED.fetched_at,
        product_name = EXCLUDED.product_name,
        category = EXCLUDED.category,
        brand = EXCLUDED.brand,
        model_article = EXCLUDED.model_article,
        purchase_price = EXCLUDED.purchase_price,
        organization_id = EXCLUDED.organization_id
"""


class PostgresSelsupStockRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def save_snapshot(self, account: str, snapshot_date: date, rows: Sequence[SelsupStockLine]) -> int:
        if not rows:
            return 0
        fetched_at = _now()
        params = [
            (
                account, snapshot_date, r.warehouse_id, r.warehouse_name, r.sku_id, r.article,
                r.wb_size, r.ozon_article, r.cell_name, r.quantity, r.available_quantity,
                r.calculated_quantity, r.modify_date, fetched_at, r.product_name, r.category, r.brand,
                r.model_article, r.purchase_price, r.organization_id,
            )
            for r in rows
        ]
        with self._conn.cursor() as cur:
            cur.executemany(_SELSUP_STOCKS_UPSERT_SQL, params)
        self._conn.commit()
        return len(rows)


class PostgresSyncRunRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def start(self, source: str, account: str) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sync_runs (source, account, started_at, status, records_fetched) "
                "VALUES (%s, %s, %s, 'running', 0) RETURNING id",
                (source, account, _now()),
            )
            row = cur.fetchone()
        self._conn.commit()
        return row[0]

    def finish(self, run_id: int, *, status: str, records_fetched: int, error_message: str | None) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE sync_runs SET finished_at = %s, status = %s, records_fetched = %s, "
                "error_message = %s WHERE id = %s",
                (_now(), status, records_fetched, error_message, run_id),
            )
        self._conn.commit()

    def list_recent(self, limit: int = 20) -> list[SyncRunSummary]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT source, account, started_at, finished_at, status, records_fetched, error_message "
                "FROM sync_runs ORDER BY started_at DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
        return [SyncRunSummary(*row) for row in rows]
