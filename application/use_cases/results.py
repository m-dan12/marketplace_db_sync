from dataclasses import dataclass


@dataclass(frozen=True)
class SyncResult:
    source: str
    account: str
    records_fetched: int
