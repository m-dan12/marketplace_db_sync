"""Ports: structural-typing Protocols. `application/` depends only on `domain/`
and the stdlib — never on `infrastructure/`."""
from __future__ import annotations

from datetime import date
from typing import Optional, Protocol, Sequence, TypeVar

from domain.models import SyncRunSummary

T = TypeVar("T")


class MarketplaceSource(Protocol[T]):
    """Pulls rows for a single account from one marketplace/report."""

    def fetch(self, account: str) -> Sequence[T]: ...


class StockRepository(Protocol[T]):
    """Persists a stock snapshot for one account/day. Never overwrites a
    previous day's snapshot — each call adds a new dated snapshot."""

    def save_snapshot(self, account: str, snapshot_date: date, rows: Sequence[T]) -> int: ...


class OrderRepository(Protocol[T]):
    """Upserts rows by their natural key (orders and sales alike)."""

    def upsert(self, account: str, rows: Sequence[T]) -> int: ...


class SyncRunRepository(Protocol):
    """Bookkeeping for `sync_runs` — one row per (source, account) run."""

    def start(self, source: str, account: str) -> int: ...

    def finish(
        self,
        run_id: int,
        *,
        status: str,
        records_fetched: int,
        error_message: Optional[str],
    ) -> None: ...

    def list_recent(self, limit: int = 20) -> Sequence[SyncRunSummary]: ...
