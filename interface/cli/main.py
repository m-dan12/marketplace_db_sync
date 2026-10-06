"""Composition root: the only place concrete adapters are constructed and
wired into use cases. Usage:

    python -m interface.cli.main sync <wb|ozon|selsup|sheets|all> [--account <name|all>]
    python -m interface.cli.main status [--limit N]
"""
from __future__ import annotations

import argparse
import logging
import os
import traceback

import psycopg
from dotenv import load_dotenv

from application.use_cases.baseline_source import BaselineSource
from application.use_cases.sync_orders import SyncOrdersUseCase
from application.use_cases.sync_sales import SyncSalesUseCase
from application.use_cases.sync_stocks import SyncStocksUseCase
from infrastructure.config import accounts as cfg
from infrastructure.persistence.postgres.connection import apply_schema, connect
from infrastructure.persistence.postgres.repositories import (
    PostgresOzonOrderRepository,
    PostgresOzonStockRepository,
    PostgresSelsupStockRepository,
    PostgresSyncRunRepository,
    PostgresWbOrderRepository,
    PostgresWbSaleRepository,
    PostgresWbStockRepository,
)
from infrastructure.config import settings
from infrastructure.sources.ozon.orders import OzonOrdersSource
from infrastructure.sources.ozon.prices import OzonPricesSource
from infrastructure.sources.ozon.promotions import OzonActionsSource
from infrastructure.sources.ozon.supplies import OzonSuppliesSource
from infrastructure.sources.ozon.warehouse_stocks import OzonWarehouseStocksSource
from infrastructure.sources.ozon.stocks import OzonStocksSource
from infrastructure.sources.selsup.movements import SelsupMovementsSource
from infrastructure.sources.selsup.stocks import SelsupStocksSource
from infrastructure.sources.sheets.client import SheetsClient
from infrastructure.sources.sheets.planning import (
    FABRIC_STOCK_SHEET,
    FABRICS_SHEET,
    QUANT_SHEET,
    SPECS_SHEET,
    SheetTableSource,
    parse_article_specs,
    parse_fabric_stock,
    parse_fabrics,
    parse_quant_multiples,
)
from infrastructure.sources.sheets.production import ProductionSheetSource
from infrastructure.sources.wb.ads import WBAdsSource
from infrastructure.sources.wb.funnel import WBFunnelSource
from infrastructure.sources.wb.orders import WBOrdersSource
from infrastructure.sources.wb.prices import WBPricesSource
from infrastructure.sources.wb.promotions import WBPromotionsSource
from infrastructure.sources.wb.supplies import WBSuppliesSource
from infrastructure.sources.wb.sales import WBSalesSource
from infrastructure.sources.wb.stocks import WBStocksSource
from infrastructure.persistence.postgres.repositories_baseline import (
    PostgresBaselineFactsReader,
    PostgresBaselineRepository,
)
from infrastructure.persistence.postgres.repositories_ml import (
    PostgresOzonPriceRepository,
    PostgresSelsupMovementRepository,
    PostgresWbAdStatRepository,
    PostgresWbPriceRepository,
)
from infrastructure.persistence.postgres.repositories_supply_demand import (
    PostgresOzonWarehouseStockRepository,
    PostgresPromotionRepository,
    PostgresSupplyRepository,
    PostgresWbFunnelRepository,
)
from infrastructure.persistence.postgres.repositories_sheets import (
    PostgresArticleSpecRepository,
    PostgresFabricRepository,
    PostgresFabricStockRepository,
    PostgresProductionRepository,
    PostgresQuantMultipleRepository,
)
from interface.cli import ml_commands

logger = logging.getLogger("marketplace_db_sync")


def _resolve_accounts(selection: str) -> list[str]:
    if selection == "all":
        return list(cfg.ACCOUNTS.keys())
    if selection not in cfg.ACCOUNTS:
        raise SystemExit(
            f"unknown account {selection!r}; known accounts: {', '.join(cfg.ACCOUNTS)}"
        )
    return [selection]


def _run(use_case, account: str) -> None:
    """One source/account sync run. Failures are logged (they are already
    recorded in `sync_runs` by the use case itself) and swallowed here so
    the rest of the sources/accounts still run."""
    try:
        result = use_case.execute(account)
        logger.info("  %s [%s]: %d records", use_case.source_name, account, result.records_fetched)
    except Exception:
        logger.error("  %s [%s]: FAILED\n%s", use_case.source_name, account, traceback.format_exc())


def _sync_wb(conn: psycopg.Connection, accounts: list[str]) -> None:
    order_repo = PostgresWbOrderRepository(conn)
    sale_repo = PostgresWbSaleRepository(conn)
    stock_repo = PostgresWbStockRepository(conn)
    price_repo = PostgresWbPriceRepository(conn)
    ad_repo = PostgresWbAdStatRepository(conn)
    supply_repo = PostgresSupplyRepository(conn)
    promo_repo = PostgresPromotionRepository(conn)
    funnel_repo = PostgresWbFunnelRepository(conn)
    sync_run_repo = PostgresSyncRunRepository(conn)
    for account in accounts:
        api_key = cfg.resolve_wb_api_key(account)
        _run(SyncOrdersUseCase("wb_orders", WBOrdersSource(api_key), order_repo, sync_run_repo), account)
        _run(SyncSalesUseCase("wb_sales", WBSalesSource(api_key), sale_repo, sync_run_repo), account)
        _run(SyncStocksUseCase("wb_stocks", WBStocksSource(api_key), stock_repo, sync_run_repo), account)
        _run(SyncStocksUseCase("wb_prices", WBPricesSource(api_key), price_repo, sync_run_repo), account)
        _run(SyncOrdersUseCase("wb_ads", WBAdsSource(api_key), ad_repo, sync_run_repo), account)
        _run(SyncOrdersUseCase("wb_supplies", WBSuppliesSource(api_key), supply_repo, sync_run_repo), account)
        _run(SyncOrdersUseCase("wb_promotions", WBPromotionsSource(api_key), promo_repo, sync_run_repo), account)
        # The funnel endpoint allows 3 requests a minute, so it goes last.
        _run(SyncOrdersUseCase("wb_funnel", WBFunnelSource(api_key), funnel_repo, sync_run_repo), account)


def _sync_ozon(conn: psycopg.Connection, accounts: list[str]) -> None:
    order_repo = PostgresOzonOrderRepository(conn)
    stock_repo = PostgresOzonStockRepository(conn)
    price_repo = PostgresOzonPriceRepository(conn)
    warehouse_repo = PostgresOzonWarehouseStockRepository(conn)
    supply_repo = PostgresSupplyRepository(conn)
    promo_repo = PostgresPromotionRepository(conn)
    sync_run_repo = PostgresSyncRunRepository(conn)
    for account in accounts:
        client_id, api_key = cfg.resolve_ozon_credentials(account)
        _run(SyncOrdersUseCase("ozon_orders", OzonOrdersSource(client_id, api_key), order_repo, sync_run_repo), account)
        _run(SyncStocksUseCase("ozon_stocks", OzonStocksSource(client_id, api_key), stock_repo, sync_run_repo), account)
        _run(SyncStocksUseCase("ozon_prices", OzonPricesSource(client_id, api_key), price_repo, sync_run_repo), account)
        _run(SyncStocksUseCase("ozon_warehouse_stocks", OzonWarehouseStocksSource(client_id, api_key), warehouse_repo, sync_run_repo), account)
        _run(SyncOrdersUseCase("ozon_supplies", OzonSuppliesSource(client_id, api_key), supply_repo, sync_run_repo), account)
        _run(SyncOrdersUseCase("ozon_promotions", OzonActionsSource(client_id, api_key), promo_repo, sync_run_repo), account)


def _sync_selsup(conn: psycopg.Connection, accounts: list[str]) -> None:
    stock_repo = PostgresSelsupStockRepository(conn)
    sync_run_repo = PostgresSyncRunRepository(conn)
    token = cfg.resolve_selsup_token()
    # One shared source instance: the API returns all cabinets' data
    # together per warehouse, so the first `fetch()` caches it and later
    # calls (for the other accounts) just filter the cache.
    source = SelsupStocksSource(token, cfg.SELSUP_WAREHOUSES, cfg.SELSUP_ORGANIZATION_IDS)
    for account in accounts:
        _run(SyncStocksUseCase("selsup_stocks", source, stock_repo, sync_run_repo), account)
    # Movement history has no organization: one run for everything, recorded
    # under the pseudo-account 'all' (accounts are resolved in the DB view).
    movement_repo = PostgresSelsupMovementRepository(conn)
    _run(SyncOrdersUseCase("selsup_movements", SelsupMovementsSource(token), movement_repo, sync_run_repo), "all")


def _sync_sheets(conn: psycopg.Connection) -> None:
    """The hand-kept production and planning sheets. They cover every cabinet,
    so each runs once under the pseudo-account 'all'."""
    key_file = os.environ.get(ml_commands.SERVICE_ACCOUNT_ENV)
    if not key_file:
        logger.error("  sheets: %s is not set, skipped", ml_commands.SERVICE_ACCOUNT_ENV)
        return
    reader = SheetsClient(key_file)
    sync_run_repo = PostgresSyncRunRepository(conn)
    planning = settings.PLANNING_SPREADSHEET_ID
    _run(SyncOrdersUseCase(
        "sheet_production", ProductionSheetSource(reader, settings.PRODUCTION_SPREADSHEET_ID),
        PostgresProductionRepository(conn), sync_run_repo), "all")
    _run(SyncOrdersUseCase(
        "sheet_quant_multiples", SheetTableSource(reader, planning, QUANT_SHEET, parse_quant_multiples),
        PostgresQuantMultipleRepository(conn), sync_run_repo), "all")
    _run(SyncOrdersUseCase(
        "sheet_article_specs", SheetTableSource(reader, planning, SPECS_SHEET, parse_article_specs),
        PostgresArticleSpecRepository(conn), sync_run_repo), "all")
    _run(SyncOrdersUseCase(
        "sheet_fabrics", SheetTableSource(reader, planning, FABRICS_SHEET, parse_fabrics),
        PostgresFabricRepository(conn), sync_run_repo), "all")
    _run(SyncStocksUseCase(
        "sheet_fabric_stock", SheetTableSource(reader, planning, FABRIC_STOCK_SHEET, parse_fabric_stock),
        PostgresFabricStockRepository(conn), sync_run_repo), "all")


def _sync_baseline(conn: psycopg.Connection) -> None:
    """The analyst's formula over what the other sources just loaded."""
    _run(SyncStocksUseCase(
        "baseline", BaselineSource(PostgresBaselineFactsReader(conn)),
        PostgresBaselineRepository(conn), PostgresSyncRunRepository(conn)), "all")


def cmd_sync(args: argparse.Namespace) -> None:
    accounts = _resolve_accounts(args.account)
    conn = connect(os.environ["DATABASE_URL"])
    try:
        apply_schema(conn)
        if args.source in ("wb", "all"):
            logger.info("=== WB ===")
            _sync_wb(conn, accounts)
        if args.source in ("ozon", "all"):
            logger.info("=== Ozon ===")
            _sync_ozon(conn, accounts)
        if args.source in ("selsup", "all"):
            logger.info("=== Selsup ===")
            _sync_selsup(conn, accounts)
        if args.source in ("sheets", "all"):
            logger.info("=== Sheets ===")
            _sync_sheets(conn)
        if args.source in ("baseline", "all"):
            logger.info("=== Baseline ===")
            _sync_baseline(conn)
    finally:
        conn.close()


def cmd_status(args: argparse.Namespace) -> None:
    conn = connect(os.environ["DATABASE_URL"])
    try:
        repo = PostgresSyncRunRepository(conn)
        for run in repo.list_recent(limit=args.limit):
            line = (
                f"{run.started_at.isoformat()}  {run.source:<15} {run.account:<15} "
                f"{run.status:<8} records={run.records_fetched}"
            )
            if run.error_message:
                line += f"  error={run.error_message}"
            print(line)
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="marketplace_db_sync")
    sub = parser.add_subparsers(dest="command", required=True)

    sync_parser = sub.add_parser("sync", help="Pull data from marketplace APIs into Postgres")
    sync_parser.add_argument("source", choices=["wb", "ozon", "selsup", "sheets", "baseline", "all"])
    sync_parser.add_argument("--account", default="all", help="account key, or 'all' (default)")
    sync_parser.set_defaults(func=cmd_sync)

    status_parser = sub.add_parser("status", help="Show recent sync_runs entries")
    status_parser.add_argument("--limit", type=int, default=20)
    status_parser.set_defaults(func=cmd_status)

    ml_commands.register(sub)

    return parser


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
