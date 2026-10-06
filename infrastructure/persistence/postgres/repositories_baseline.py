"""Reads what the analyst's formula needs from the warehouse tables and stores its output."""
from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, timezone
from typing import Sequence

import psycopg

from domain.baseline import ArticleFacts, BaselineRow, ChannelFacts

# Sales windows end the day before the planning date (the night run sees yesterday's last order).
# Articles seen anywhere (sales, stock, production in progress) are planned.
# Stock comes from the newest snapshot not after the planning date. "Days without stock" is 0
# on purpose: the sheet's column for it is empty in practice (8 days over 18,000 rows), so its
# speeds are plain sales / 30; the real counts are in the view out_of_stock_days_30.
# Production in progress = every task that is not yet released or closed, whatever its kind
# (the sheet "в производстве" does the same).
_FACTS_SQL = """
WITH snap AS (
    SELECT MAX(snapshot_date) AS d FROM stock_days WHERE snapshot_date <= %(as_of)s
),
sales AS (
    SELECT article, marketplace,
           COALESCE(SUM(units) FILTER (WHERE day >= %(as_of)s - 7), 0) AS s7,
           COALESCE(SUM(units) FILTER (WHERE day >= %(as_of)s - 30), 0) AS s30,
           COALESCE(SUM(units) FILTER (WHERE day < %(as_of)s - 30), 0) AS sp30
    FROM orders_daily
    WHERE day >= %(as_of)s - 60 AND day < %(as_of)s
    GROUP BY article, marketplace
),
stock AS (
    SELECT a.article,
           SUM(wb_qty) AS wb, SUM(ozon_qty) AS ozon,
           SUM(selsup_fbs_qty) AS fbs, SUM(selsup_kvant_qty) AS kvant
    FROM article_stock_daily a, snap WHERE a.snapshot_date = snap.d
    GROUP BY a.article
),
production AS (
    SELECT article,
           COALESCE(SUM(quantity) FILTER (WHERE direction = 'wb'), 0) AS wb,
           COALESCE(SUM(quantity) FILTER (WHERE direction = 'ozon'), 0) AS ozon,
           COALESCE(SUM(quantity) FILTER (WHERE direction = 'sklad'), 0) AS sklad,
           COALESCE(SUM(quantity) FILTER (WHERE direction = 'sklad_kvant'), 0) AS kvant
    FROM production_lines_v
    WHERE status_group NOT IN ('released', 'closed') AND article IS NOT NULL
    GROUP BY article
),
planned AS (
    SELECT article FROM sales WHERE s30 + sp30 > 0
    UNION SELECT article FROM stock WHERE wb + ozon + fbs + kvant > 0
    UNION SELECT article FROM production WHERE wb + ozon + sklad + kvant > 0
)
SELECT p.article,
       COALESCE(sp.purpose, CASE WHEN LENGTH(p.article) < 7 THEN 'тдр' ELSE '' END),
       COALESCE(sw.s7, 0), COALESCE(sw.s30, 0), COALESCE(sw.sp30, 0),
       0, COALESCE(st.wb, 0), COALESCE(pr.wb, 0),
       COALESCE(so.s7, 0), COALESCE(so.s30, 0), COALESCE(so.sp30, 0),
       0, COALESCE(st.ozon, 0), COALESCE(pr.ozon, 0),
       COALESCE(st.fbs, 0), COALESCE(st.kvant, 0), COALESCE(pr.sklad, 0), COALESCE(pr.kvant, 0)
FROM planned p
LEFT JOIN article_specs sp ON sp.article = p.article
LEFT JOIN sales sw ON sw.article = p.article AND sw.marketplace = 'wb'
LEFT JOIN sales so ON so.article = p.article AND so.marketplace = 'ozon'
LEFT JOIN stock st ON st.article = p.article
LEFT JOIN production pr ON pr.article = p.article
ORDER BY p.article
"""


class PostgresBaselineFactsReader:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def read(self, as_of: date) -> list[ArticleFacts]:
        with self._conn.cursor() as cur:
            cur.execute(_FACTS_SQL, {"as_of": as_of})
            rows = cur.fetchall()
        self._conn.rollback()
        return [
            ArticleFacts(
                article=r[0],
                category=r[1],
                wb=ChannelFacts(*(float(x) for x in r[2:8])),
                ozon=ChannelFacts(*(float(x) for x in r[8:14])),
                stock_fbs=float(r[14]),
                stock_kvant=float(r[15]),
                in_production_sklad=float(r[16]),
                in_production_kvant=float(r[17]),
            )
            for r in rows
        ]

    def quants(self) -> dict[str, float]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT size_key, quant FROM quant_multiples WHERE quant IS NOT NULL")
            result = {key: float(quant) for key, quant in cur.fetchall()}
        self._conn.rollback()
        return result


class PostgresBaselineRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def save_snapshot(self, account: str, snapshot_date: date, rows: Sequence[BaselineRow]) -> int:
        """Replaces the whole planning date (the account is the pseudo-account 'all')."""
        now = datetime.now(timezone.utc)
        params = [
            (
                snapshot_date, r.article, r.inputs.category, r.inputs.quant,
                r.result.wb.max_speed, r.result.ozon.max_speed,
                r.result.wb.need, r.result.ozon.need, r.result.need, r.result.need_in_quants,
                json.dumps(dataclasses.asdict(r.inputs), ensure_ascii=False), now,
            )
            for r in rows
        ]
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM baseline_recommendation WHERE as_of = %s", (snapshot_date,))
            cur.executemany(
                """INSERT INTO baseline_recommendation (
                       as_of, article, category, quant, wb_speed, ozon_speed, wb_need, ozon_need,
                       need, need_quants, inputs, computed_at
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                params,
            )
        self._conn.commit()
        return len(rows)
