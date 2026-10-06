from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Mapping, Protocol

from domain.baseline import ArticleFacts, BaselineRow, calculate_all


class BaselineFactsReader(Protocol):
    def read(self, as_of: date) -> list[ArticleFacts]: ...

    def quants(self) -> Mapping[str, float]: ...


@dataclass
class BaselineSource:
    """Plays the `MarketplaceSource` role for the analyst's formula, so the nightly
    run reuses the generic stock-snapshot use case and its `sync_runs` bookkeeping."""

    reader: BaselineFactsReader
    as_of_provider: Callable[[], date] = field(default=date.today)

    def fetch(self, account: str) -> list[BaselineRow]:
        return calculate_all(self.reader.read(self.as_of_provider()), self.reader.quants())
