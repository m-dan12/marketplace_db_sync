"""One-off (and re-runnable) backfill from the `Выгрузка авто` Drive archive.

The nightly xlsx export keeps dated copies of every report in each
cabinet's `Архив` folder (from 09-10.09.2026), and Selsup has daily stock
files since 06.08.2026 plus a receipts/shipments file since the start of the
year. This loads all of it into the same tables the live sync writes to.

Snapshot-like reports (stocks, prices) get the **modification date of the
file** in Moscow time as their snapshot date — the same "date the state was
observed" that the live sync uses. Window reports (orders, sales, ads) are
upserted by natural key, oldest file first, so later files win.

Already-loaded files are remembered in `backfill_files` (keyed by Drive file
id + modification time), so re-running is cheap and safe; `force=True`
reloads.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from infrastructure.sources.drive import xlsx
from infrastructure.sources.drive.client import DriveClient, DriveFile

logger = logging.getLogger("marketplace_db_sync.backfill")

MOSCOW = timezone(timedelta(hours=3))

_CABINET_KEYWORDS = (
    ("сказка", "skazka"),
    ("профтекс", "skazka"),
    ("milky", "milky_garden"),
    ("timeless", "timeless"),
)
_DATED_NAME = r"{prefix} (\d{{2}}\.\d{{2}}\.\d{{4}})\.xlsx"


def cabinet_account(folder_name: str) -> Optional[str]:
    """`Кабинет 2 (Milky Garden)` -> `milky_garden`."""
    lowered = folder_name.lower()
    for keyword, account in _CABINET_KEYWORDS:
        if keyword in lowered:
            return account
    return None


@dataclass(frozen=True)
class Kind:
    name: str
    marketplace: str  # top-level Drive folder
    prefixes: tuple[str, ...]  # file-name prefixes of this report
    parse: Callable[[list[xlsx.Row]], list]
    repo: str  # attribute of `Repos`
    mode: str  # 'snapshot' (dated copy) | 'upsert' (window by natural key)
    sheet: Optional[str] = None
    sparse: bool = False  # window reports: first + last few files are enough


KINDS: tuple[Kind, ...] = (
    Kind("wb_stocks", "Wildberries", ("Остатки",), xlsx.parse_wb_stocks, "wb_stock", "snapshot"),
    Kind("wb_prices", "Wildberries", ("Цены и скидки",), xlsx.parse_wb_prices, "wb_price", "snapshot"),
    Kind("wb_ads", "Wildberries", ("Реклама",), xlsx.parse_wb_ads, "wb_ad", "upsert", sheet="По товарам"),
    Kind(
        "wb_orders", "Wildberries",
        ("Лента заказов (30 дней)", "Лента заказов (предыдущие 30 дней)"),
        xlsx.parse_wb_orders, "wb_order", "upsert", sparse=True,
    ),
    Kind("wb_sales", "Wildberries", ("Продажи (30 дней)",), xlsx.parse_wb_sales, "wb_sale", "upsert", sparse=True),
    Kind("ozon_stocks", "Ozon", ("Остатки",), xlsx.parse_ozon_stocks, "ozon_stock", "snapshot", sheet="Остатки"),
    Kind("ozon_prices", "Ozon", ("Цены",), xlsx.parse_ozon_prices, "ozon_price", "snapshot"),
    Kind(
        "ozon_orders", "Ozon", ("Заказы (30 дней)", "Заказы (предыдущие 30 дней)"),
        xlsx.parse_ozon_orders, "ozon_order", "upsert", sparse=True,
    ),
)
SELSUP_KINDS = ("selsup_stocks", "selsup_movements")
ALL_KIND_NAMES = tuple(k.name for k in KINDS) + SELSUP_KINDS

# Windows files are sparse-loaded: the oldest file reaches furthest back,
# the newest ones carry the final (cancel/return) statuses.
_SPARSE_FIRST = 1
_SPARSE_LAST = 3


@dataclass
class Repos:
    wb_stock: Any
    wb_price: Any
    wb_ad: Any
    wb_order: Any
    wb_sale: Any
    ozon_stock: Any
    ozon_price: Any
    ozon_order: Any
    selsup_stock: Any
    selsup_movement: Any
    backfill: Any


@dataclass
class FileResult:
    kind: str
    account: str
    file_name: str
    rows: int
    status: str  # 'loaded' | 'skipped' | 'error'
    error: Optional[str] = None


@dataclass
class BackfillReport:
    results: list[FileResult] = field(default_factory=list)

    def totals(self) -> dict[str, dict[str, int]]:
        summary: dict[str, dict[str, int]] = {}
        for r in self.results:
            bucket = summary.setdefault(r.kind, {"files": 0, "rows": 0, "skipped": 0, "errors": 0})
            if r.status == "loaded":
                bucket["files"] += 1
                bucket["rows"] += r.rows
            elif r.status == "skipped":
                bucket["skipped"] += 1
            else:
                bucket["errors"] += 1
        return summary


def _require_parsed(file: DriveFile, rows: list, lines: list) -> None:
    """A non-empty file that yields nothing means the format changed (or a
    filter is wrong): fail loudly instead of recording it as loaded."""
    if rows and not lines:
        raise ValueError(f"0 of {len(rows)} rows recognised in {file.name!r} (format changed?)")


def _file_key(file: DriveFile) -> str:
    return f"{file.id}:{file.modified_time.isoformat()}"


def _snapshot_date(file: DriveFile) -> date:
    return file.modified_time.astimezone(MOSCOW).date()


def select_files(files: list[DriveFile], prefix: str) -> list[DriveFile]:
    """Dated archive copies plus the current undated file, oldest first."""
    dated = re.compile(_DATED_NAME.format(prefix=re.escape(prefix)))
    picked = [f for f in files if not f.is_folder and (dated.fullmatch(f.name) or f.name == f"{prefix}.xlsx")]
    return sorted(picked, key=lambda f: (f.modified_time, f.name))


def sparse_pick(files: list[DriveFile]) -> list[DriveFile]:
    if len(files) <= _SPARSE_FIRST + _SPARSE_LAST:
        return files
    return files[:_SPARSE_FIRST] + files[-_SPARSE_LAST:]


class DriveArchiveBackfill:
    def __init__(
        self,
        drive: DriveClient,
        repos: Repos,
        root_folder_id: str,
        organization_accounts: dict[int, str],
    ) -> None:
        self._drive = drive
        self._repos = repos
        self._root_folder_id = root_folder_id
        self._organization_accounts = organization_accounts

    def run(
        self,
        kinds: Optional[set[str]] = None,
        accounts: Optional[set[str]] = None,
        force: bool = False,
        orders_mode: str = "sparse",
        max_files_per_kind: Optional[int] = None,
    ) -> BackfillReport:
        wanted = kinds or set(ALL_KIND_NAMES)
        report = BackfillReport()
        loaded = set() if force else self._repos.backfill.loaded_file_ids()

        for kind in KINDS:
            if kind.name in wanted:
                self._run_marketplace_kind(kind, accounts, loaded, orders_mode, max_files_per_kind, report)
        if wanted & set(SELSUP_KINDS):
            self._run_selsup(wanted, loaded, max_files_per_kind, report)
        return report

    # -- marketplace cabinets ------------------------------------------------

    def _run_marketplace_kind(
        self, kind: Kind, accounts: Optional[set[str]], loaded: set[str],
        orders_mode: str, max_files: Optional[int], report: BackfillReport,
    ) -> None:
        marketplace = self._drive.find_child(self._root_folder_id, kind.marketplace)
        if marketplace is None:
            logger.warning("Drive folder %r not found", kind.marketplace)
            return
        for cabinet in self._drive.list_children(marketplace.id):
            account = cabinet_account(cabinet.name)
            if not cabinet.is_folder or account is None or (accounts and account not in accounts):
                continue
            current = self._drive.list_children(cabinet.id)
            archive = self._drive.find_child(cabinet.id, "Архив")
            files = current + (self._drive.list_children(archive.id) if archive else [])
            for prefix in kind.prefixes:
                selected = select_files(files, prefix)
                if kind.sparse and orders_mode == "sparse":
                    selected = sparse_pick(selected)
                if max_files:
                    selected = selected[-max_files:]
                for file in selected:
                    report.results.append(self._load_file(kind, account, file, loaded))

    def _load_file(self, kind: Kind, account: str, file: DriveFile, loaded: set[str]) -> FileResult:
        key = _file_key(file)
        if key in loaded:
            return FileResult(kind.name, account, file.name, 0, "skipped")
        try:
            rows = xlsx.read_rows(self._drive.download(file.id), kind.sheet)
            lines = kind.parse(rows)
            _require_parsed(file, rows, lines)
            repo = getattr(self._repos, kind.repo)
            snapshot = _snapshot_date(file) if kind.mode == "snapshot" else None
            if kind.mode == "snapshot":
                count = repo.save_snapshot(account, snapshot, lines)
            else:
                count = repo.upsert(account, lines)
            self._repos.backfill.mark_loaded(key, kind.name, account, file.name, snapshot, count)
            logger.info("  %s [%s] %s: %d rows", kind.name, account, file.name, count)
            return FileResult(kind.name, account, file.name, count, "loaded")
        except Exception as exc:  # one bad file must not stop the rest
            logger.error("  %s [%s] %s: FAILED %s", kind.name, account, file.name, exc)
            return FileResult(kind.name, account, file.name, 0, "error", str(exc))

    # -- Selsup (flat folder, all cabinets in one file) ---------------------

    def _run_selsup(
        self, wanted: set[str], loaded: set[str], max_files: Optional[int], report: BackfillReport
    ) -> None:
        folder = self._drive.find_child(self._root_folder_id, "Selsup")
        if folder is None:
            logger.warning("Drive folder 'Selsup' not found")
            return
        files = self._drive.list_children(folder.id)
        if "selsup_stocks" in wanted:
            selected = select_files(files, "Остатки")
            sku_accounts = self._sku_account_map(selected)
            for file in selected[-max_files:] if max_files else selected:
                report.results.append(self._load_selsup_stock_file(file, loaded, sku_accounts))
        if "selsup_movements" in wanted:
            selected = select_files(files, "Приёмки и отгрузки")
            year = [f for f in files if f.name == "Приёмки и отгрузки (с начала года).xlsx"]
            ordered = year + selected  # year file first, daily files on top
            for file in ordered[-max_files:] if max_files else ordered:
                report.results.append(self._load_selsup_movements_file(file, loaded))

    def _sku_account_map(self, stock_files: list[DriveFile]) -> dict[int, str]:
        """`skuId -> account` from the newest stock file that carries
        `organizationId`; used for the older files that do not."""
        for file in reversed(stock_files):
            rows = xlsx.read_rows(self._drive.download(file.id))
            if rows and "organizationId" in rows[0]:
                return xlsx.sku_accounts_from_rows(rows, self._organization_accounts)
        return {}

    def _load_selsup_stock_file(
        self, file: DriveFile, loaded: set[str], sku_accounts: dict[int, str]
    ) -> FileResult:
        key = _file_key(file)
        if key in loaded:
            return FileResult("selsup_stocks", "all", file.name, 0, "skipped")
        try:
            rows = xlsx.read_rows(self._drive.download(file.id))
            by_account = xlsx.parse_selsup_stocks(rows, self._organization_accounts, sku_accounts)
            _require_parsed(file, rows, [line for lines in by_account.values() for line in lines])
            snapshot = _snapshot_date(file)
            count = sum(
                self._repos.selsup_stock.save_snapshot(account, snapshot, lines)
                for account, lines in by_account.items()
            )
            self._repos.backfill.mark_loaded(key, "selsup_stocks", "all", file.name, snapshot, count)
            logger.info("  selsup_stocks %s: %d rows", file.name, count)
            return FileResult("selsup_stocks", "all", file.name, count, "loaded")
        except Exception as exc:
            logger.error("  selsup_stocks %s: FAILED %s", file.name, exc)
            return FileResult("selsup_stocks", "all", file.name, 0, "error", str(exc))

    def _load_selsup_movements_file(self, file: DriveFile, loaded: set[str]) -> FileResult:
        key = _file_key(file)
        if key in loaded:
            return FileResult("selsup_movements", "all", file.name, 0, "skipped")
        try:
            rows = xlsx.read_rows(self._drive.download(file.id))
            lines = xlsx.parse_selsup_movements(rows)
            _require_parsed(file, rows, lines)
            count = self._repos.selsup_movement.upsert("all", lines)
            self._repos.backfill.mark_loaded(key, "selsup_movements", "all", file.name, None, count)
            logger.info("  selsup_movements %s: %d rows", file.name, count)
            return FileResult("selsup_movements", "all", file.name, count, "loaded")
        except Exception as exc:
            logger.error("  selsup_movements %s: FAILED %s", file.name, exc)
            return FileResult("selsup_movements", "all", file.name, 0, "error", str(exc))
