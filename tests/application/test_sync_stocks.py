from datetime import date

import pytest

from application.use_cases.sync_stocks import SyncStocksUseCase
from domain.models import WbStockLine
from tests.application.fakes import FakeMarketplaceSource, FakeStockRepository, FakeSyncRunRepository


def _stock_row(warehouse: str = "Kvant", quantity: float = 5.0) -> WbStockLine:
    return WbStockLine(
        nm_id=123, vendor_code="ABC-1", barcode="000111", tech_size="M",
        volume=1.2, warehouse_name=warehouse, quantity=quantity,
    )


def test_execute_saves_snapshot_and_records_ok_run():
    rows = [_stock_row()]
    source = FakeMarketplaceSource(rows_by_account={"skazka": rows})
    repository = FakeStockRepository()
    sync_run_repo = FakeSyncRunRepository()
    fixed_date = date(2026, 9, 28)
    use_case = SyncStocksUseCase(
        "wb_stocks", source, repository, sync_run_repo, snapshot_date_provider=lambda: fixed_date
    )

    result = use_case.execute("skazka")

    assert result.records_fetched == 1
    assert repository.snapshots == [("skazka", fixed_date, rows)]
    [run] = sync_run_repo.runs.values()
    assert run == {
        "source": "wb_stocks", "account": "skazka", "status": "ok",
        "records_fetched": 1, "error_message": None,
    }


def test_execute_propagates_source_error_and_records_error_run():
    source = FakeMarketplaceSource(error_accounts={"timeless"})
    repository = FakeStockRepository()
    sync_run_repo = FakeSyncRunRepository()
    use_case = SyncStocksUseCase("wb_stocks", source, repository, sync_run_repo)

    with pytest.raises(RuntimeError, match="boom fetching timeless"):
        use_case.execute("timeless")

    assert repository.snapshots == []
    [run] = sync_run_repo.runs.values()
    assert run["status"] == "error"
    assert run["records_fetched"] == 0
    assert "boom fetching timeless" in run["error_message"]


def test_empty_result_still_records_ok_run_with_zero_records():
    source = FakeMarketplaceSource(rows_by_account={"skazka": []})
    repository = FakeStockRepository()
    sync_run_repo = FakeSyncRunRepository()
    use_case = SyncStocksUseCase("wb_stocks", source, repository, sync_run_repo)

    result = use_case.execute("skazka")

    assert result.records_fetched == 0
    assert repository.snapshots == [("skazka", date.today(), [])]
    [run] = sync_run_repo.runs.values()
    assert run["status"] == "ok"
