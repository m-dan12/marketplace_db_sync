"""Postgres adapters for the ML-foundation tables (prices, advertising,
Selsup movements, article dimension, backfill bookkeeping)."""
from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date, datetime, timezone
from typing import Iterable, Optional, Sequence

import psycopg

from domain.article import ParsedArticle
from domain.models import OzonPriceLine, ProductCardLine, SelsupMovementLine, WbAdStatLine, WbPriceLine


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _executemany(conn: psycopg.Connection, sql: str, params: list) -> None:
    with conn.cursor() as cur:
        cur.executemany(sql, params)
    conn.commit()


_WB_PRICES_SQL = """
    INSERT INTO wb_prices (
        account, snapshot_date, nm_id, vendor_code, tech_size, size_id,
        price, discount, discounted_price, fetched_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (account, snapshot_date, nm_id, tech_size) DO UPDATE SET
        vendor_code = EXCLUDED.vendor_code,
        size_id = EXCLUDED.size_id,
        price = EXCLUDED.price,
        discount = EXCLUDED.discount,
        discounted_price = EXCLUDED.discounted_price,
        fetched_at = EXCLUDED.fetched_at
"""


class PostgresWbPriceRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def save_snapshot(self, account: str, snapshot_date: date, rows: Sequence[WbPriceLine]) -> int:
        if not rows:
            return 0
        fetched_at = _now()
        params = [
            (
                account, snapshot_date, r.nm_id, r.vendor_code, r.tech_size or "", r.size_id,
                r.price, r.discount, r.discounted_price, fetched_at,
            )
            for r in rows
        ]
        _executemany(self._conn, _WB_PRICES_SQL, params)
        return len(rows)


_OZON_PRICES_SQL = """
    INSERT INTO ozon_prices (
        account, snapshot_date, offer_id, product_id, price, old_price, min_price,
        marketing_seller_price, net_price, fetched_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (account, snapshot_date, offer_id) DO UPDATE SET
        product_id = EXCLUDED.product_id,
        price = EXCLUDED.price,
        old_price = EXCLUDED.old_price,
        min_price = EXCLUDED.min_price,
        marketing_seller_price = EXCLUDED.marketing_seller_price,
        net_price = EXCLUDED.net_price,
        fetched_at = EXCLUDED.fetched_at
"""


class PostgresOzonPriceRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def save_snapshot(self, account: str, snapshot_date: date, rows: Sequence[OzonPriceLine]) -> int:
        if not rows:
            return 0
        fetched_at = _now()
        params = [
            (
                account, snapshot_date, r.offer_id, r.product_id, r.price, r.old_price,
                r.min_price, r.marketing_seller_price, r.net_price, fetched_at,
            )
            for r in rows
        ]
        _executemany(self._conn, _OZON_PRICES_SQL, params)
        return len(rows)


_WB_AD_STATS_SQL = """
    INSERT INTO wb_ad_stats (
        account, stat_date, campaign_id, nm_id, campaign_status, views, clicks, ctr, cpc,
        spend, orders, carts, avg_position, fetched_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (account, stat_date, campaign_id, nm_id) DO UPDATE SET
        campaign_status = EXCLUDED.campaign_status,
        views = EXCLUDED.views,
        clicks = EXCLUDED.clicks,
        ctr = EXCLUDED.ctr,
        cpc = EXCLUDED.cpc,
        spend = EXCLUDED.spend,
        orders = EXCLUDED.orders,
        carts = EXCLUDED.carts,
        avg_position = EXCLUDED.avg_position,
        fetched_at = EXCLUDED.fetched_at
"""


class PostgresWbAdStatRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert(self, account: str, rows: Sequence[WbAdStatLine]) -> int:
        if not rows:
            return 0
        fetched_at = _now()
        # One campaign can list the same product under several apps/days in a
        # single fetch; the natural key is (day, campaign, product) so fold
        # duplicates (sum the counters) to keep the upsert deterministic.
        folded: dict[tuple, WbAdStatLine] = {}
        for r in rows:
            key = (r.stat_date, r.campaign_id, r.nm_id)
            prev = folded.get(key)
            folded[key] = r if prev is None else _merge_ad_lines(prev, r)
        params = [
            (
                account, r.stat_date, r.campaign_id, r.nm_id, r.campaign_status, r.views,
                r.clicks, r.ctr, r.cpc, r.spend, r.orders, r.carts, r.avg_position, fetched_at,
            )
            for r in folded.values()
        ]
        _executemany(self._conn, _WB_AD_STATS_SQL, params)
        return len(params)


def _add(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)


def _merge_ad_lines(a: WbAdStatLine, b: WbAdStatLine) -> WbAdStatLine:
    views, clicks = _add(a.views, b.views), _add(a.clicks, b.clicks)
    spend = _add(a.spend, b.spend)
    return WbAdStatLine(
        stat_date=a.stat_date,
        campaign_id=a.campaign_id,
        nm_id=a.nm_id,
        campaign_status=b.campaign_status or a.campaign_status,
        views=views,
        clicks=clicks,
        ctr=round(clicks / views * 100, 2) if views else a.ctr,
        cpc=round(spend / clicks, 2) if clicks else a.cpc,
        spend=spend,
        orders=_add(a.orders, b.orders),
        carts=_add(a.carts, b.carts),
        avg_position=b.avg_position if b.avg_position is not None else a.avg_position,
    )


def _fmt_qty(value: float) -> str:
    return format(float(value), "g")


def movement_keys(rows: Iterable[SelsupMovementLine]) -> list[str]:
    """Stable identity of each movement: a hash of its fields plus the
    occurrence index among identical rows in the batch. The same movement
    therefore gets the same key whether it comes from the API or from an
    xlsx backfill."""
    seen: Counter[str] = Counter()
    keys: list[str] = []
    for r in rows:
        raw = "|".join(
            str(part)
            for part in (
                r.movement_type, r.operation, r.moved_at.isoformat(timespec="seconds"),
                r.warehouse_id, r.order_id, r.order_type, r.sku_id, r.cell_name,
                _fmt_qty(r.quantity), r.user_id,
            )
        )
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        keys.append(f"{digest}#{seen[digest]}")
        seen[digest] += 1
    return keys


_SELSUP_MOVEMENTS_SQL = """
    INSERT INTO selsup_movements (
        movement_key, movement_type, operation, moved_at, warehouse_id, order_id, order_type,
        sku_id, article, product_name, cell_name, quantity, user_id, fetched_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (movement_key) DO UPDATE SET
        article = COALESCE(EXCLUDED.article, selsup_movements.article),
        product_name = COALESCE(EXCLUDED.product_name, selsup_movements.product_name),
        fetched_at = EXCLUDED.fetched_at
"""


class PostgresSelsupMovementRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert(self, account: str, rows: Sequence[SelsupMovementLine]) -> int:
        """`account` is accepted for the port's signature; Selsup movements
        are not split by account at load time (see `selsup_movements_v`)."""
        if not rows:
            return 0
        fetched_at = _now()
        params = [
            (
                key, r.movement_type, r.operation, r.moved_at, r.warehouse_id, r.order_id,
                r.order_type, r.sku_id, r.article, r.product_name, r.cell_name, r.quantity,
                r.user_id, fetched_at,
            )
            for key, r in zip(movement_keys(rows), rows)
        ]
        # 50k-row chunks keep each transaction (and the driver pipeline) bounded.
        for i in range(0, len(params), 50_000):
            _executemany(self._conn, _SELSUP_MOVEMENTS_SQL, params[i : i + 50_000])
        return len(params)


class PostgresArticleRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def list_known_articles(self) -> list[str]:
        """Every article string seen in any loaded table."""
        sql = """
            SELECT vendor_code FROM wb_stocks WHERE vendor_code IS NOT NULL
            UNION SELECT supplier_article FROM wb_orders WHERE supplier_article IS NOT NULL
            UNION SELECT supplier_article FROM wb_sales WHERE supplier_article IS NOT NULL
            UNION SELECT vendor_code FROM wb_prices WHERE vendor_code IS NOT NULL
            UNION SELECT offer_id FROM ozon_stocks
            UNION SELECT offer_id FROM ozon_orders
            UNION SELECT offer_id FROM ozon_prices
            UNION SELECT article FROM selsup_stocks WHERE article IS NOT NULL AND NOT article_from_model
            UNION SELECT article FROM selsup_movements WHERE article IS NOT NULL
            UNION SELECT vendor_code FROM wb_funnel_daily WHERE vendor_code IS NOT NULL
            UNION SELECT article FROM supply_items WHERE article IS NOT NULL
            UNION SELECT offer_id FROM ozon_warehouse_stocks
            UNION SELECT article FROM production_lines
            UNION SELECT article FROM article_specs
        """
        with self._conn.cursor() as cur:
            cur.execute(sql)
            return [row[0] for row in cur.fetchall()]

    def upsert_many(self, parsed: Sequence[ParsedArticle]) -> int:
        if not parsed:
            return 0
        now = _now()
        params = [
            (
                p.article, p.parsed, p.brand_code, p.brand_name, p.design_code, p.duvet_size_code,
                p.sheet_size_code, p.pillow_size_code, p.variant, now,
            )
            for p in parsed
        ]
        _executemany(
            self._conn,
            """
            INSERT INTO dim_article (
                article, parsed, brand_code, brand_name, design_code, duvet_size_code,
                sheet_size_code, pillow_size_code, variant, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (article) DO UPDATE SET
                parsed = EXCLUDED.parsed,
                brand_code = EXCLUDED.brand_code,
                brand_name = EXCLUDED.brand_name,
                design_code = EXCLUDED.design_code,
                duvet_size_code = EXCLUDED.duvet_size_code,
                sheet_size_code = EXCLUDED.sheet_size_code,
                pillow_size_code = EXCLUDED.pillow_size_code,
                variant = EXCLUDED.variant,
                updated_at = EXCLUDED.updated_at
            """,
            params,
        )
        return len(params)


class PostgresBackfillRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def loaded_file_ids(self) -> set[str]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT drive_file_id FROM backfill_files")
            return {row[0] for row in cur.fetchall()}

    def mark_loaded(
        self, drive_file_id: str, kind: str, account: str, file_name: str,
        snapshot_date: Optional[date], rows_loaded: int,
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO backfill_files (
                    drive_file_id, kind, account, file_name, snapshot_date, rows_loaded, loaded_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (drive_file_id) DO UPDATE SET
                    rows_loaded = EXCLUDED.rows_loaded, loaded_at = EXCLUDED.loaded_at
                """,
                (drive_file_id, kind, account, file_name, snapshot_date, rows_loaded, _now()),
            )
        self._conn.commit()


class PostgresProductCardRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert(self, account: str, rows: Sequence[ProductCardLine]) -> int:
        now = _now()
        unique = {(r.marketplace, r.article): r for r in rows}
        with self._conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO product_cards (
                    marketplace, account, article, external_id, title, brand, category, category_id,
                    first_seen_at, fetched_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (marketplace, account, article) DO UPDATE SET
                    external_id = EXCLUDED.external_id, title = EXCLUDED.title, brand = EXCLUDED.brand,
                    category = EXCLUDED.category, category_id = EXCLUDED.category_id,
                    fetched_at = EXCLUDED.fetched_at
                """,
                [
                    (r.marketplace, account, r.article, r.external_id, r.title, r.brand, r.category,
                     r.category_id, now, now)
                    for r in unique.values()
                ],
            )
        self._conn.commit()
        return len(unique)
