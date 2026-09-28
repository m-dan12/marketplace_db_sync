from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from application.ports import MarketplaceSource, OrderRepository, SyncRunRepository
from application.use_cases.results import SyncResult

T = TypeVar("T")


@dataclass
class SyncSalesUseCase:
    """Fetches a sales window for one account and upserts it by natural key,
    wrapping the run in `sync_runs` bookkeeping.

    A failure is recorded in `sync_runs` (status='error') and then
    re-raised — it is the CLI's job to catch it and move on to the next
    source/account, not this use case's.
    """

    source_name: str
    source: MarketplaceSource[T]
    repository: OrderRepository[T]
    sync_run_repository: SyncRunRepository

    def execute(self, account: str) -> SyncResult:
        run_id = self.sync_run_repository.start(self.source_name, account)
        try:
            rows = self.source.fetch(account)
            records_fetched = self.repository.upsert(account, rows)
        except Exception as exc:
            self.sync_run_repository.finish(
                run_id, status="error", records_fetched=0, error_message=str(exc)
            )
            raise
        self.sync_run_repository.finish(
            run_id, status="ok", records_fetched=records_fetched, error_message=None
        )
        return SyncResult(source=self.source_name, account=account, records_fetched=records_fetched)
