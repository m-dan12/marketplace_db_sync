from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, TypeVar

from application.ports import MarketplaceSource, StockRepository, SyncRunRepository
from application.use_cases.results import SyncResult

T = TypeVar("T")


@dataclass
class SyncStocksUseCase:
    """Fetches a stock snapshot for one account and stores it under today's
    (or an injected) snapshot date, wrapping the run in `sync_runs` bookkeeping.

    A failure is recorded in `sync_runs` (status='error') and then
    re-raised — it is the CLI's job to catch it and move on to the next
    source/account, not this use case's.
    """

    source_name: str
    source: MarketplaceSource[T]
    repository: StockRepository[T]
    sync_run_repository: SyncRunRepository
    snapshot_date_provider: Callable[[], date] = field(default=date.today)

    def execute(self, account: str) -> SyncResult:
        run_id = self.sync_run_repository.start(self.source_name, account)
        try:
            rows = self.source.fetch(account)
            snapshot_date = self.snapshot_date_provider()
            records_fetched = self.repository.save_snapshot(account, snapshot_date, rows)
        except Exception as exc:
            self.sync_run_repository.finish(
                run_id, status="error", records_fetched=0, error_message=str(exc)
            )
            raise
        self.sync_run_repository.finish(
            run_id, status="ok", records_fetched=records_fetched, error_message=None
        )
        return SyncResult(source=self.source_name, account=account, records_fetched=records_fetched)
