"""A failed step must not poison the shared connection (real Postgres, TEST_DATABASE_URL)."""
import os

import psycopg
import pytest

from infrastructure.persistence.postgres.connection import apply_schema, connect
from infrastructure.persistence.postgres.repositories import PostgresSyncRunRepository

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not set"
)


@pytest.fixture()
def conn():
    connection = connect(os.environ["TEST_DATABASE_URL"])
    apply_schema(connection)
    with connection.cursor() as cur:
        cur.execute("TRUNCATE sync_runs, selsup_movements RESTART IDENTITY")
    connection.commit()
    yield connection
    connection.close()


def rows(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def test_finishing_a_failed_run_works_on_a_connection_with_an_aborted_transaction(conn):
    runs = PostgresSyncRunRepository(conn)
    run_id = runs.start("selsup_movements", "all")

    with pytest.raises(psycopg.errors.NotNullViolation):
        with conn.cursor() as cur:  # the step dies in the middle of a batch, no commit/rollback
            cur.execute(
                "INSERT INTO selsup_movements (movement_key, movement_type, operation, moved_at, "
                "sku_id, quantity, fetched_at) VALUES ('k', 'x', 'PUT', now(), NULL, 1, now())"
            )

    runs.finish(run_id, status="error", records_fetched=0, error_message="boom")  # used to raise

    assert rows(conn, "SELECT status, error_message FROM sync_runs WHERE id = %s", (run_id,)) == [("error", "boom")]
    # and the next step on the same connection works
    next_id = runs.start("selsup_inbound", "skazka")
    runs.finish(next_id, status="ok", records_fetched=3, error_message=None)
    assert rows(conn, "SELECT status FROM sync_runs WHERE id = %s", (next_id,)) == [("ok",)]


def test_finishing_a_successful_run_keeps_the_committed_data(conn):
    runs = PostgresSyncRunRepository(conn)
    run_id = runs.start("wb_orders", "skazka")

    runs.finish(run_id, status="ok", records_fetched=7, error_message=None)

    assert rows(conn, "SELECT status, records_fetched FROM sync_runs WHERE id = %s", (run_id,)) == [("ok", 7)]
