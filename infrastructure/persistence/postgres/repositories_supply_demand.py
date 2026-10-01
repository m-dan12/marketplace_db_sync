"""Postgres adapters for supplies, the WB sales funnel, promotions and the
Ozon FBO stock by warehouse."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Sequence

import psycopg

from domain.models import (
    OzonWarehouseStockLine,
    PromotionBundle,
    SupplyBundle,
    WbFunnelLine,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> date:
    return date.today()


_SUPPLY_UPSERT_SQL = """
    INSERT INTO supplies (
        marketplace, account, supply_key, order_id, created_at, planned_date, fact_date,
        updated_at, status, warehouse_name, actual_warehouse_name, transit_warehouse_name,
        is_crossdock, quantity, accepted_quantity, ready_for_sale_quantity, fetched_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (marketplace, account, supply_key) DO UPDATE SET
        order_id = EXCLUDED.order_id,
        created_at = COALESCE(EXCLUDED.created_at, supplies.created_at),
        planned_date = EXCLUDED.planned_date,
        fact_date = EXCLUDED.fact_date,
        updated_at = EXCLUDED.updated_at,
        status = EXCLUDED.status,
        warehouse_name = EXCLUDED.warehouse_name,
        actual_warehouse_name = EXCLUDED.actual_warehouse_name,
        transit_warehouse_name = EXCLUDED.transit_warehouse_name,
        is_crossdock = EXCLUDED.is_crossdock,
        quantity = EXCLUDED.quantity,
        accepted_quantity = EXCLUDED.accepted_quantity,
        ready_for_sale_quantity = EXCLUDED.ready_for_sale_quantity,
        fetched_at = EXCLUDED.fetched_at
"""

_SUPPLY_ITEM_INSERT_SQL = """
    INSERT INTO supply_items (
        marketplace, account, supply_key, item_key, article, nm_id, sku, barcode, tech_size,
        quantity, accepted_quantity, ready_for_sale_quantity, fetched_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (marketplace, account, supply_key, item_key) DO UPDATE SET
        quantity = supply_items.quantity + EXCLUDED.quantity,
        accepted_quantity = COALESCE(supply_items.accepted_quantity, 0) + COALESCE(EXCLUDED.accepted_quantity, 0),
        ready_for_sale_quantity = COALESCE(supply_items.ready_for_sale_quantity, 0) + COALESCE(EXCLUDED.ready_for_sale_quantity, 0)
"""


class PostgresSupplyRepository:
    """Upserts a supply header and replaces its items in one transaction.
    Two item rows with the same key inside one supply (the same barcode listed
    twice) are summed rather than dropped."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert(self, account: str, bundles: Sequence[SupplyBundle]) -> int:
        if not bundles:
            return 0
        fetched_at = _now()
        with self._conn.cursor() as cur:
            for bundle in bundles:
                s = bundle.supply
                cur.execute(
                    _SUPPLY_UPSERT_SQL,
                    (
                        s.marketplace, account, s.supply_key, s.order_id, s.created_at, s.planned_date,
                        s.fact_date, s.updated_at, s.status, s.warehouse_name, s.actual_warehouse_name,
                        s.transit_warehouse_name, s.is_crossdock, s.quantity, s.accepted_quantity,
                        s.ready_for_sale_quantity, fetched_at,
                    ),
                )
                cur.execute(
                    "DELETE FROM supply_items WHERE marketplace = %s AND account = %s AND supply_key = %s",
                    (s.marketplace, account, s.supply_key),
                )
                if bundle.items:
                    cur.executemany(
                        _SUPPLY_ITEM_INSERT_SQL,
                        [
                            (
                                s.marketplace, account, s.supply_key, i.item_key, i.article, i.nm_id, i.sku,
                                i.barcode, i.tech_size, i.quantity or 0, i.accepted_quantity,
                                i.ready_for_sale_quantity, fetched_at,
                            )
                            for i in bundle.items
                        ],
                    )
        self._conn.commit()
        return len(bundles)


_FUNNEL_UPSERT_SQL = """
    INSERT INTO wb_funnel_daily (
        account, day, nm_id, vendor_code, subject_name, open_count, cart_count, order_count,
        order_sum, buyout_count, buyout_sum, cancel_count, cancel_sum, add_to_wishlist,
        product_rating, feedback_rating, fetched_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (account, day, nm_id) DO UPDATE SET
        vendor_code = EXCLUDED.vendor_code,
        subject_name = EXCLUDED.subject_name,
        open_count = EXCLUDED.open_count,
        cart_count = EXCLUDED.cart_count,
        order_count = EXCLUDED.order_count,
        order_sum = EXCLUDED.order_sum,
        buyout_count = EXCLUDED.buyout_count,
        buyout_sum = EXCLUDED.buyout_sum,
        cancel_count = EXCLUDED.cancel_count,
        cancel_sum = EXCLUDED.cancel_sum,
        add_to_wishlist = EXCLUDED.add_to_wishlist,
        product_rating = EXCLUDED.product_rating,
        feedback_rating = EXCLUDED.feedback_rating,
        fetched_at = EXCLUDED.fetched_at
"""


class PostgresWbFunnelRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert(self, account: str, rows: Sequence[WbFunnelLine]) -> int:
        if not rows:
            return 0
        fetched_at = _now()
        params = [
            (
                account, r.day, r.nm_id, r.vendor_code, r.subject_name, r.open_count, r.cart_count,
                r.order_count, r.order_sum, r.buyout_count, r.buyout_sum, r.cancel_count, r.cancel_sum,
                r.add_to_wishlist, r.product_rating, r.feedback_rating, fetched_at,
            )
            for r in rows
        ]
        per_day: dict[date, int] = {}
        for r in rows:
            per_day[r.day] = per_day.get(r.day, 0) + 1
        with self._conn.cursor() as cur:
            cur.executemany(_FUNNEL_UPSERT_SQL, params)
            cur.executemany(
                """
                INSERT INTO wb_funnel_days (account, day, rows_loaded, loaded_at) VALUES (%s, %s, %s, %s)
                ON CONFLICT (account, day) DO UPDATE SET
                    rows_loaded = EXCLUDED.rows_loaded, loaded_at = EXCLUDED.loaded_at
                """,
                [(account, day, count, fetched_at) for day, count in per_day.items()],
            )
        self._conn.commit()
        return len(rows)

    def loaded_days(self, account: str) -> set[date]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT day FROM wb_funnel_days WHERE account = %s", (account,))
            return {row[0] for row in cur.fetchall()}


class PostgresPromotionRepository:
    """Upserts promotions and the items seen in them. Nothing is ever
    deleted: an item that left an action keeps its `last_seen_date`."""

    def __init__(self, conn: psycopg.Connection, today_provider=_today) -> None:
        self._conn = conn
        self._today = today_provider

    def upsert(self, account: str, bundles: Sequence[PromotionBundle]) -> int:
        if not bundles:
            return 0
        fetched_at = _now()
        today = self._today()
        with self._conn.cursor() as cur:
            for bundle in bundles:
                p = bundle.promotion
                cur.execute(
                    """
                    INSERT INTO promotions (
                        marketplace, account, promo_id, name, promo_type, start_at, end_at, description,
                        potential_count, participating_count, discount_type, discount_value,
                        first_seen_date, fetched_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (marketplace, account, promo_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        promo_type = EXCLUDED.promo_type,
                        start_at = EXCLUDED.start_at,
                        end_at = EXCLUDED.end_at,
                        description = COALESCE(EXCLUDED.description, promotions.description),
                        potential_count = EXCLUDED.potential_count,
                        participating_count = EXCLUDED.participating_count,
                        discount_type = EXCLUDED.discount_type,
                        discount_value = EXCLUDED.discount_value,
                        fetched_at = EXCLUDED.fetched_at
                    """,
                    (
                        p.marketplace, account, p.promo_id, p.name, p.promo_type, p.start_at, p.end_at,
                        p.description, p.potential_count, p.participating_count, p.discount_type,
                        p.discount_value, today, fetched_at,
                    ),
                )
                if bundle.items:
                    cur.executemany(
                        """
                        INSERT INTO promotion_items (
                            marketplace, account, promo_id, item_id, in_action, price, plan_price,
                            discount, plan_discount, stock, first_seen_date, last_seen_date
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (marketplace, account, promo_id, item_id) DO UPDATE SET
                            in_action = EXCLUDED.in_action,
                            price = EXCLUDED.price,
                            plan_price = EXCLUDED.plan_price,
                            discount = EXCLUDED.discount,
                            plan_discount = EXCLUDED.plan_discount,
                            stock = EXCLUDED.stock,
                            last_seen_date = EXCLUDED.last_seen_date
                        """,
                        [
                            (
                                p.marketplace, account, p.promo_id, i.item_id, i.in_action, i.price,
                                i.plan_price, i.discount, i.plan_discount, i.stock, today, today,
                            )
                            for i in bundle.items
                        ],
                    )
        self._conn.commit()
        return len(bundles)


_OZON_WH_SQL = """
    INSERT INTO ozon_warehouse_stocks (
        account, snapshot_date, offer_id, sku, product_name, warehouse_name,
        free_to_sell, reserved, promised, fetched_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (account, snapshot_date, offer_id, warehouse_name) DO UPDATE SET
        sku = EXCLUDED.sku,
        product_name = EXCLUDED.product_name,
        free_to_sell = EXCLUDED.free_to_sell,
        reserved = EXCLUDED.reserved,
        promised = EXCLUDED.promised,
        fetched_at = EXCLUDED.fetched_at
"""


class PostgresOzonWarehouseStockRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def save_snapshot(
        self, account: str, snapshot_date: date, rows: Sequence[OzonWarehouseStockLine]
    ) -> int:
        if not rows:
            return 0
        fetched_at = _now()
        # The same (offer, warehouse) can appear twice with different sku
        # variants; the natural key keeps the last one.
        deduped: dict[tuple[str, str], OzonWarehouseStockLine] = {}
        for r in rows:
            deduped[(r.offer_id, r.warehouse_name)] = r
        params = [
            (
                account, snapshot_date, r.offer_id, r.sku, r.product_name, r.warehouse_name,
                r.free_to_sell, r.reserved, r.promised, fetched_at,
            )
            for r in deduped.values()
        ]
        with self._conn.cursor() as cur:
            cur.executemany(_OZON_WH_SQL, params)
        self._conn.commit()
        return len(params)
