from __future__ import annotations

from pathlib import Path

import psycopg

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn)


def apply_schema(conn: psycopg.Connection) -> None:
    """Idempotent (all `CREATE ... IF NOT EXISTS`) — safe to call on every run."""
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
