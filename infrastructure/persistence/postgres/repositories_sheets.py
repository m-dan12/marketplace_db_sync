"""Postgres adapters for the data read from the production / planning sheets."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Sequence

import psycopg

from domain.models import (
    ArticleSpecLine,
    CostModelLine,
    FabricLine,
    FabricReceiptLine,
    FabricStockLine,
    ProductionLine,
    QuantMultipleLine,
    WbArticlePricingLine,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Fields people update after the row was created; a change is written to the log.
_TRACKED = ("status", "fact_quantity", "fact_ship_date", "fact_accept_date", "receipt_no")

_PRODUCTION_UPSERT_SQL = """
    INSERT INTO production_lines (
        row_key, sheet_row, article, quantity, region, order_text, size_text, meters, meters2,
        week_number, direction, task_total, task_key, task_quantity, fact_quantity,
        fact_ship_date, fact_ship_raw, fact_accept_date, fact_accept_raw, status, status_group,
        week_start, week_end, receipt_no, brand, workshop, first_seen_at, last_seen_at, deleted_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, NULL)
    ON CONFLICT (row_key) DO UPDATE SET
        sheet_row = EXCLUDED.sheet_row,
        meters = EXCLUDED.meters,
        meters2 = EXCLUDED.meters2,
        week_number = EXCLUDED.week_number,
        task_total = EXCLUDED.task_total,
        task_quantity = EXCLUDED.task_quantity,
        fact_quantity = EXCLUDED.fact_quantity,
        fact_ship_date = EXCLUDED.fact_ship_date,
        fact_ship_raw = EXCLUDED.fact_ship_raw,
        fact_accept_date = EXCLUDED.fact_accept_date,
        fact_accept_raw = EXCLUDED.fact_accept_raw,
        status = EXCLUDED.status,
        status_group = EXCLUDED.status_group,
        week_end = EXCLUDED.week_end,
        receipt_no = EXCLUDED.receipt_no,
        last_seen_at = EXCLUDED.last_seen_at,
        deleted_at = NULL
"""

_LOG_SQL = """
    INSERT INTO production_line_log (row_key, changed_at, event, before, after)
    VALUES (%s, %s, %s, %s::jsonb, %s::jsonb)
"""


def _tracked(values: Sequence[Any]) -> dict[str, Any]:
    return {name: (v.isoformat() if isinstance(v, date) else v) for name, v in zip(_TRACKED, values)}


class PostgresProductionRepository:
    """Mirrors the whole production sheet. A read that keeps fewer than
    `min_kept_ratio` of the rows we already hold is refused: a sheet that was
    cut short (a failed import, a filter left on) must not wipe the plan."""

    def __init__(self, conn: psycopg.Connection, min_kept_ratio: float = 0.5) -> None:
        self._conn = conn
        self._min_kept_ratio = min_kept_ratio

    def upsert(self, account: str, lines: Sequence[ProductionLine]) -> int:
        now = _now()
        current = {line.row_key: line for line in lines}
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT row_key, status, fact_quantity, fact_ship_date, fact_accept_date, receipt_no, deleted_at "
                "FROM production_lines"
            )
            existing = {row[0]: (row[1:6], row[6]) for row in cur.fetchall()}

            active = {key for key, (_, deleted_at) in existing.items() if deleted_at is None}
            if active and len(active & current.keys()) < len(active) * self._min_kept_ratio:
                raise ValueError(
                    f"the sheet kept only {len(active & current.keys())} of {len(active)} known rows; "
                    "refusing to treat the rest as deleted (use a lower ratio if that is intended)"
                )

            log: list[tuple] = []
            for key, line in current.items():
                if key not in existing:
                    continue
                before, deleted_at = existing[key]
                after = tuple(getattr(line, name) for name in _TRACKED)
                if deleted_at is not None:
                    log.append((key, now, "restored", None, json.dumps(_tracked(after), default=str)))
                elif tuple(before) != after:
                    log.append((key, now, "changed", json.dumps(_tracked(before), default=str),
                                json.dumps(_tracked(after), default=str)))
            gone = sorted(active - current.keys())
            log.extend((key, now, "deleted", json.dumps(_tracked(existing[key][0]), default=str), None) for key in gone)

            cur.executemany(
                _PRODUCTION_UPSERT_SQL,
                [
                    (
                        l.row_key, l.sheet_row, l.article, l.quantity, l.region, l.order_text, l.size_text,
                        l.meters, l.meters2, l.week_number, l.direction, l.task_total, l.task_key,
                        l.task_quantity, l.fact_quantity, l.fact_ship_date, l.fact_ship_raw,
                        l.fact_accept_date, l.fact_accept_raw, l.status, l.status_group, l.week_start,
                        l.week_end, l.receipt_no, l.brand, l.workshop, now, now,
                    )
                    for l in current.values()
                ],
            )
            if gone:
                cur.execute("UPDATE production_lines SET deleted_at = %s WHERE row_key = ANY(%s)", (now, gone))
            if log:
                cur.executemany(_LOG_SQL, log)
        self._conn.commit()
        return len(current)


class PostgresQuantMultipleRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert(self, account: str, rows: Sequence[QuantMultipleLine]) -> int:
        fetched_at = _now()
        with self._conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO quant_multiples (
                    size_key, name, volume_liters, fits_in_box, calculated_in_box, desired_count,
                    final_count, final_v4, quant, fetched_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (size_key) DO UPDATE SET
                    name = EXCLUDED.name, volume_liters = EXCLUDED.volume_liters,
                    fits_in_box = EXCLUDED.fits_in_box, calculated_in_box = EXCLUDED.calculated_in_box,
                    desired_count = EXCLUDED.desired_count, final_count = EXCLUDED.final_count,
                    final_v4 = EXCLUDED.final_v4, quant = EXCLUDED.quant, fetched_at = EXCLUDED.fetched_at
                """,
                [
                    (r.size_key, r.name, r.volume_liters, r.fits_in_box, r.calculated_in_box,
                     r.desired_count, r.final_count, r.final_v4, r.quant, fetched_at)
                    for r in rows
                ],
            )
        self._conn.commit()
        return len(rows)


class PostgresArticleSpecRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert(self, account: str, rows: Sequence[ArticleSpecLine]) -> int:
        fetched_at = _now()
        with self._conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO article_specs (
                    article, brand_name, fabric_no_1, fabric_no_2, meters_per_item_1,
                    meters_per_item_2, purpose, size_text, fetched_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (article) DO UPDATE SET
                    brand_name = EXCLUDED.brand_name, fabric_no_1 = EXCLUDED.fabric_no_1,
                    fabric_no_2 = EXCLUDED.fabric_no_2, meters_per_item_1 = EXCLUDED.meters_per_item_1,
                    meters_per_item_2 = EXCLUDED.meters_per_item_2, purpose = EXCLUDED.purpose,
                    size_text = EXCLUDED.size_text, fetched_at = EXCLUDED.fetched_at
                """,
                [
                    (r.article, r.brand_name, r.fabric_no_1, r.fabric_no_2, r.meters_per_item_1,
                     r.meters_per_item_2, r.purpose, r.size_text, fetched_at)
                    for r in rows
                ],
            )
        self._conn.commit()
        return len(rows)


class PostgresFabricRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert(self, account: str, rows: Sequence[FabricLine]) -> int:
        fetched_at = _now()
        with self._conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO dim_fabric (
                    fabric_no, material, price_category, roll_length_m, roll_width_cm, audience, color,
                    pattern, weave, short_name, supplier_article, supplier_code, supplier, products, fetched_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (fabric_no) DO UPDATE SET
                    material = EXCLUDED.material, price_category = EXCLUDED.price_category,
                    roll_length_m = EXCLUDED.roll_length_m, roll_width_cm = EXCLUDED.roll_width_cm,
                    audience = EXCLUDED.audience, color = EXCLUDED.color, pattern = EXCLUDED.pattern,
                    weave = EXCLUDED.weave, short_name = EXCLUDED.short_name,
                    supplier_article = EXCLUDED.supplier_article, supplier_code = EXCLUDED.supplier_code,
                    supplier = EXCLUDED.supplier, products = EXCLUDED.products, fetched_at = EXCLUDED.fetched_at
                """,
                [
                    (r.fabric_no, r.material, r.price_category, r.roll_length_m, r.roll_width_cm, r.audience,
                     r.color, r.pattern, r.weave, r.short_name, r.supplier_article, r.supplier_code,
                     r.supplier, r.products, fetched_at)
                    for r in rows
                ],
            )
        self._conn.commit()
        return len(rows)


class PostgresFabricStockRepository:
    """One snapshot per day: a repeated run on the same day replaces it (a fabric
    that left the sheet in the meantime must not linger)."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def save_snapshot(self, account: str, snapshot_date: date, rows: Sequence[FabricStockLine]) -> int:
        if not rows:
            return 0
        fetched_at = _now()
        merged: dict[tuple[str, str], FabricStockLine] = {}
        for r in rows:
            key = (r.fabric_no, r.supplier or "")
            if key in merged:  # the same fabric listed twice at one supplier: sum
                first = merged[key]
                total = (first.quantity_m or 0) + (r.quantity_m or 0)
                merged[key] = FabricStockLine(first.fabric_no, total, first.supplier, first.name, first.brand)
            else:
                merged[key] = r
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM fabric_stock WHERE snapshot_date = %s", (snapshot_date,))
            cur.executemany(
                """
                INSERT INTO fabric_stock (snapshot_date, fabric_no, supplier, quantity_m, name, brand, fetched_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (snapshot_date, fabric_no, supplier, r.quantity_m, r.name, r.brand, fetched_at)
                    for (fabric_no, supplier), r in merged.items()
                ],
            )
        self._conn.commit()
        return len(merged)


class PostgresCostModelRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def save_snapshot(self, account: str, snapshot_date: date, rows: Sequence[CostModelLine]) -> int:
        fetched_at = _now()
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM cost_models WHERE snapshot_date = %s", (snapshot_date,))
            cur.executemany(
                """
                INSERT INTO cost_models (
                    snapshot_date, model_key, fabric_price, price_type, base_price, cost_total,
                    ozon_limit_discount, wb_max_discount, min_price, fetched_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (snapshot_date, r.model_key, r.fabric_price, r.price_type, r.base_price, r.cost_total,
                     r.ozon_limit_discount, r.wb_max_discount, r.min_price, fetched_at)
                    for r in rows
                ],
            )
        self._conn.commit()
        return len(rows)


class PostgresFabricReceiptRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert(self, account: str, rows: Sequence[FabricReceiptLine]) -> int:
        """Replaces every sheet that is in `rows`; sheets not read this time stay as they were."""
        fetched_at = _now()
        sheets = sorted({r.sheet for r in rows})
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM fabric_receipts WHERE sheet = ANY(%s)", (sheets,))
            cur.executemany(
                """
                INSERT INTO fabric_receipts (
                    sheet, sheet_row, week_start, task_number, task_text, received_date, workshop,
                    supplier_text, price, nomenclature, meters, amount, document, fabric_no,
                    fabric_name, brand, fetched_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (r.sheet, r.sheet_row, r.week_start, r.task_number, r.task_text, r.received_date, r.workshop,
                     r.supplier_text, r.price, r.nomenclature, r.meters, r.amount, r.document, r.fabric_no,
                     r.fabric_name, r.brand, fetched_at)
                    for r in rows
                ],
            )
        self._conn.commit()
        return len(rows)


class PostgresWbArticlePricingRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert(self, account: str, rows: Sequence[WbArticlePricingLine]) -> int:
        fetched_at = _now()
        with self._conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO wb_article_pricing (
                    account, article, nm_id, brand, category, model_key, cost, base_price, range_start,
                    range_end, limit_price, limit_discount, launch_discount, fetched_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (account, article) DO UPDATE SET
                    nm_id = EXCLUDED.nm_id, brand = EXCLUDED.brand, category = EXCLUDED.category,
                    model_key = EXCLUDED.model_key, cost = EXCLUDED.cost, base_price = EXCLUDED.base_price,
                    range_start = EXCLUDED.range_start, range_end = EXCLUDED.range_end,
                    limit_price = EXCLUDED.limit_price, limit_discount = EXCLUDED.limit_discount,
                    launch_discount = EXCLUDED.launch_discount, fetched_at = EXCLUDED.fetched_at
                """,
                [
                    (account, r.article, r.nm_id, r.brand, r.category, r.model_key, r.cost, r.base_price,
                     r.range_start, r.range_end, r.limit_price, r.limit_discount, r.launch_discount, fetched_at)
                    for r in rows
                ],
            )
        self._conn.commit()
        return len(rows)
