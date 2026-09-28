"""Fake port implementations for use-case tests — plain classes that
structurally satisfy the `application.ports` Protocols, no mocking
libraries, no real HTTP/Postgres."""
from __future__ import annotations

from datetime import date
from typing import Optional, Sequence


class FakeMarketplaceSource:
    def __init__(self, rows_by_account: Optional[dict] = None, error_accounts: Optional[set] = None) -> None:
        self._rows_by_account = rows_by_account or {}
        self._error_accounts = error_accounts or set()
        self.fetch_calls: list[str] = []

    def fetch(self, account: str) -> Sequence:
        self.fetch_calls.append(account)
        if account in self._error_accounts:
            raise RuntimeError(f"boom fetching {account}")
        return self._rows_by_account.get(account, [])


class FakeStockRepository:
    def __init__(self) -> None:
        self.snapshots: list[tuple[str, date, list]] = []

    def save_snapshot(self, account: str, snapshot_date: date, rows: Sequence) -> int:
        self.snapshots.append((account, snapshot_date, list(rows)))
        return len(rows)


class FakeOrderRepository:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, list]] = []

    def upsert(self, account: str, rows: Sequence) -> int:
        self.upserts.append((account, list(rows)))
        return len(rows)


class FakeSyncRunRepository:
    def __init__(self) -> None:
        self.runs: dict[int, dict] = {}
        self._next_id = 1

    def start(self, source: str, account: str) -> int:
        run_id = self._next_id
        self._next_id += 1
        self.runs[run_id] = {
            "source": source, "account": account, "status": "running",
            "records_fetched": 0, "error_message": None,
        }
        return run_id

    def finish(self, run_id: int, *, status: str, records_fetched: int, error_message: Optional[str]) -> None:
        self.runs[run_id].update(
            status=status, records_fetched=records_fetched, error_message=error_message
        )

    def list_recent(self, limit: int = 20) -> list[dict]:
        return list(self.runs.values())[:limit]
