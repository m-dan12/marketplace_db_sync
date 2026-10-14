"""Feature rows per planning date, forecast-evaluation samples and the log of evaluations."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Optional

import psycopg

from domain.forecast_eval import DemandSample, Metrics

_BUILD_SQL = """
INSERT INTO features_daily (
    as_of, article, category, brand, quant, cost, wb_price, ozon_price,
    wb_s7, wb_s30, wb_sp30, ozon_s7, ozon_s30, ozon_sp30,
    wb_stock, ozon_stock, stock_fbs, stock_kvant,
    in_prod_wb, in_prod_ozon, in_prod_sklad, in_prod_kvant,
    wb_max_speed, ozon_max_speed, baseline_need, dow, month, iso_week, computed_at
)
WITH brand AS (
    SELECT article, MIN(brand) AS brand FROM product_cards WHERE brand IS NOT NULL GROUP BY article
), cost AS (
    SELECT article, MAX(cost) AS cost FROM wb_article_pricing WHERE cost > 0 GROUP BY article
), wb_price AS (
    SELECT DISTINCT ON (vendor_code) vendor_code, discounted_price
    FROM wb_prices WHERE snapshot_date <= %(as_of)s AND vendor_code IS NOT NULL
    ORDER BY vendor_code, snapshot_date DESC, discounted_price
), ozon_price AS (
    SELECT DISTINCT ON (offer_id) offer_id, price
    FROM ozon_prices WHERE snapshot_date <= %(as_of)s
    ORDER BY offer_id, snapshot_date DESC
)
SELECT b.as_of, b.article, b.category, br.brand, b.quant, c.cost, wp.discounted_price, op.price,
       (b.inputs #>> '{wb,sales_7d}')::numeric, (b.inputs #>> '{wb,sales_30d}')::numeric,
       (b.inputs #>> '{wb,sales_prev_30d}')::numeric,
       (b.inputs #>> '{ozon,sales_7d}')::numeric, (b.inputs #>> '{ozon,sales_30d}')::numeric,
       (b.inputs #>> '{ozon,sales_prev_30d}')::numeric,
       (b.inputs #>> '{wb,stock}')::numeric, (b.inputs #>> '{ozon,stock}')::numeric,
       (b.inputs ->> 'stock_fbs')::numeric, (b.inputs ->> 'stock_kvant')::numeric,
       (b.inputs #>> '{wb,in_production}')::numeric, (b.inputs #>> '{ozon,in_production}')::numeric,
       (b.inputs ->> 'in_production_sklad')::numeric, (b.inputs ->> 'in_production_kvant')::numeric,
       b.wb_speed, b.ozon_speed, b.need,
       EXTRACT(DOW FROM b.as_of)::int, EXTRACT(MONTH FROM b.as_of)::int, EXTRACT(WEEK FROM b.as_of)::int,
       %(now)s
FROM baseline_recommendation b
LEFT JOIN brand br ON br.article = b.article
LEFT JOIN cost c ON c.article = b.article
LEFT JOIN wb_price wp ON wp.vendor_code = b.article
LEFT JOIN ozon_price op ON op.offer_id = b.article
WHERE b.as_of = %(as_of)s
"""

_SAMPLES_SQL = """
SELECT f.as_of, f.article, f.wb_s7, f.wb_s30, f.wb_sp30, f.wb_max_speed, o.wb_next_{h},
       f.ozon_s7, f.ozon_s30, f.ozon_sp30, f.ozon_max_speed, o.ozon_next_{h}
FROM features_daily f
JOIN demand_outcome_v o USING (as_of, article)
WHERE o.complete_{h} AND f.as_of BETWEEN %(start)s AND %(end)s
ORDER BY f.as_of, f.article
"""


class PostgresFeatureRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def build(self, as_of: date) -> int:
        """Replaces the feature rows of `as_of`; the formula's inputs of that date must already be saved."""
        with self._conn.cursor() as cur:
            cur.execute("DELETE FROM features_daily WHERE as_of = %s", (as_of,))
            cur.execute(_BUILD_SQL, {"as_of": as_of, "now": datetime.now(timezone.utc)})
            count = cur.rowcount
        self._conn.commit()
        return count

    def has_baseline(self, as_of: date) -> bool:
        with self._conn.cursor() as cur:
            cur.execute("SELECT EXISTS (SELECT 1 FROM baseline_recommendation WHERE as_of = %s)", (as_of,))
            found = cur.fetchone()[0]
        self._conn.rollback()
        return bool(found)

    def samples(self, horizon: int, start: date, end: date) -> list[DemandSample]:
        if horizon not in (7, 30):
            raise ValueError("horizon must be 7 or 30 days")
        with self._conn.cursor() as cur:
            cur.execute(_SAMPLES_SQL.format(h=horizon), {"start": start, "end": end})
            rows = cur.fetchall()
        self._conn.rollback()
        samples: list[DemandSample] = []
        for r in rows:
            samples.append(DemandSample(r[0], r[1], "wb", float(r[2] or 0), float(r[3] or 0), float(r[4] or 0),
                                        float(r[5] or 0), float(r[6] or 0)))
            samples.append(DemandSample(r[0], r[1], "ozon", float(r[7] or 0), float(r[8] or 0), float(r[9] or 0),
                                        float(r[10] or 0), float(r[11] or 0)))
        return samples


class PostgresModelRunRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def save(
        self, horizon: int, start: date, end: date, test_from: Optional[date], samples: int,
        results: dict[str, dict[str, Metrics]],
    ) -> None:
        payload = {name: {channel: asdict(m) for channel, m in by_channel.items()} for name, by_channel in results.items()}
        with self._conn.cursor() as cur:
            cur.execute(
                """INSERT INTO model_runs (run_at, horizon_days, as_of_from, as_of_to, test_from, samples, results)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (datetime.now(timezone.utc), horizon, start, end, test_from, samples, json.dumps(payload)),
            )
        self._conn.commit()
