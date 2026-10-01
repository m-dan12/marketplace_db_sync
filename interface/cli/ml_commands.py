"""CLI commands of the ML-foundation stages: Drive-archive backfill, article
dimension refresh and a data-coverage report."""
from __future__ import annotations

import argparse
import logging
import os

from domain.article import parse_article
from infrastructure.backfill.drive_archive import ALL_KIND_NAMES, DriveArchiveBackfill, Repos
from infrastructure.config import accounts as cfg
from infrastructure.persistence.postgres.connection import apply_schema, connect
from infrastructure.persistence.postgres.repositories import (
    PostgresOzonOrderRepository,
    PostgresOzonStockRepository,
    PostgresSelsupStockRepository,
    PostgresWbOrderRepository,
    PostgresWbSaleRepository,
    PostgresWbStockRepository,
)
from infrastructure.persistence.postgres.repositories_ml import (
    PostgresArticleRepository,
    PostgresBackfillRepository,
    PostgresOzonPriceRepository,
    PostgresSelsupMovementRepository,
    PostgresWbAdStatRepository,
    PostgresWbPriceRepository,
)
from infrastructure.sources.drive.client import DriveClient

logger = logging.getLogger("marketplace_db_sync")

SERVICE_ACCOUNT_ENV = "GOOGLE_SERVICE_ACCOUNT_FILE"
DRIVE_ROOT_ENV = "DRIVE_ROOT_FOLDER_ID"


def cmd_backfill(args: argparse.Namespace) -> None:
    key_file = os.environ.get(SERVICE_ACCOUNT_ENV)
    root_id = os.environ.get(DRIVE_ROOT_ENV)
    if not key_file or not root_id:
        raise SystemExit(f"set {SERVICE_ACCOUNT_ENV} (path to the key file) and {DRIVE_ROOT_ENV}")
    unknown = set(args.kinds or []) - set(ALL_KIND_NAMES)
    if unknown:
        raise SystemExit(f"unknown kinds {sorted(unknown)}; known: {', '.join(ALL_KIND_NAMES)}")

    conn = connect(os.environ["DATABASE_URL"])
    try:
        apply_schema(conn)
        repos = Repos(
            wb_stock=PostgresWbStockRepository(conn),
            wb_price=PostgresWbPriceRepository(conn),
            wb_ad=PostgresWbAdStatRepository(conn),
            wb_order=PostgresWbOrderRepository(conn),
            wb_sale=PostgresWbSaleRepository(conn),
            ozon_stock=PostgresOzonStockRepository(conn),
            ozon_price=PostgresOzonPriceRepository(conn),
            ozon_order=PostgresOzonOrderRepository(conn),
            selsup_stock=PostgresSelsupStockRepository(conn),
            selsup_movement=PostgresSelsupMovementRepository(conn),
            backfill=PostgresBackfillRepository(conn),
        )
        backfill = DriveArchiveBackfill(
            DriveClient(key_file), repos, root_id, cfg.SELSUP_ORGANIZATION_IDS
        )
        report = backfill.run(
            kinds=set(args.kinds) if args.kinds else None,
            accounts=None if args.account == "all" else {args.account},
            force=args.force,
            orders_mode=args.orders_mode,
            max_files_per_kind=args.max_files,
        )
        for kind, total in sorted(report.totals().items()):
            logger.info(
                "%-17s files=%d rows=%d skipped=%d errors=%d",
                kind, total["files"], total["rows"], total["skipped"], total["errors"],
            )
        if any(r.status == "error" for r in report.results):
            raise SystemExit(1)
    finally:
        conn.close()


def cmd_refresh_dims(args: argparse.Namespace) -> None:
    conn = connect(os.environ["DATABASE_URL"])
    try:
        apply_schema(conn)
        repo = PostgresArticleRepository(conn)
        articles = sorted(set(repo.list_known_articles()))
        parsed = [parse_article(a) for a in articles]
        repo.upsert_many(parsed)
        unparsed = [p.article for p in parsed if not p.parsed]
        logger.info("dim_article: %d articles, %d not matching the pattern", len(parsed), len(unparsed))
        for article in unparsed[:20]:
            logger.info("  unparsed: %s", article)
    finally:
        conn.close()


_COVERAGE_QUERIES = (
    ("wb_stocks", "SELECT MIN(snapshot_date), MAX(snapshot_date), COUNT(DISTINCT snapshot_date) FROM wb_stocks"),
    ("ozon_stocks", "SELECT MIN(snapshot_date), MAX(snapshot_date), COUNT(DISTINCT snapshot_date) FROM ozon_stocks"),
    ("selsup_stocks", "SELECT MIN(snapshot_date), MAX(snapshot_date), COUNT(DISTINCT snapshot_date) FROM selsup_stocks"),
    ("wb_prices", "SELECT MIN(snapshot_date), MAX(snapshot_date), COUNT(DISTINCT snapshot_date) FROM wb_prices"),
    ("ozon_prices", "SELECT MIN(snapshot_date), MAX(snapshot_date), COUNT(DISTINCT snapshot_date) FROM ozon_prices"),
    ("wb_orders", "SELECT MIN(order_date), MAX(order_date), COUNT(DISTINCT order_date) FROM wb_orders"),
    ("wb_sales", "SELECT MIN(sale_date), MAX(sale_date), COUNT(DISTINCT sale_date) FROM wb_sales"),
    ("ozon_orders", "SELECT MIN(order_date)::date, MAX(order_date)::date, COUNT(DISTINCT order_date::date) FROM ozon_orders"),
    ("wb_ad_stats", "SELECT MIN(stat_date), MAX(stat_date), COUNT(DISTINCT stat_date) FROM wb_ad_stats"),
    ("selsup_movements", "SELECT MIN(moved_at)::date, MAX(moved_at)::date, COUNT(DISTINCT moved_at::date) FROM selsup_movements"),
)


def cmd_coverage(args: argparse.Namespace) -> None:
    """Per table: date range, number of distinct days and row count."""
    conn = connect(os.environ["DATABASE_URL"])
    try:
        apply_schema(conn)
        with conn.cursor() as cur:
            for table, query in _COVERAGE_QUERIES:
                cur.execute(query)
                first, last, days = cur.fetchone()
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                rows = cur.fetchone()[0]
                print(f"{table:<17} {first} .. {last}  days={days:<4} rows={rows}")
    finally:
        conn.close()


def register(sub: argparse._SubParsersAction) -> None:
    backfill_parser = sub.add_parser("backfill", help="Load the Drive archive into Postgres")
    backfill_parser.add_argument("--kinds", nargs="+", metavar="KIND", help=f"one or more of: {', '.join(ALL_KIND_NAMES)}")
    backfill_parser.add_argument("--account", default="all", help="account key, or 'all' (default)")
    backfill_parser.add_argument("--force", action="store_true", help="reload files already loaded")
    backfill_parser.add_argument(
        "--orders-mode", choices=["sparse", "all"], default="sparse",
        help="orders/sales windows overlap: 'sparse' loads the oldest + newest files only (default)",
    )
    backfill_parser.add_argument("--max-files", type=int, default=None, help="newest N files per report (for trial runs)")
    backfill_parser.set_defaults(func=cmd_backfill)

    dims_parser = sub.add_parser("refresh-dims", help="Rebuild dim_article from every article seen")
    dims_parser.set_defaults(func=cmd_refresh_dims)

    coverage_parser = sub.add_parser("coverage", help="Show the date range and row count per table")
    coverage_parser.set_defaults(func=cmd_coverage)
