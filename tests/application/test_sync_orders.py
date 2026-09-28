from datetime import date

import pytest

from application.use_cases.sync_orders import SyncOrdersUseCase
from domain.models import WbOrderLine
from tests.application.fakes import FakeMarketplaceSource, FakeOrderRepository, FakeSyncRunRepository


def _order_row(srid: str = "srid-1") -> WbOrderLine:
    return WbOrderLine(
        srid=srid, order_date=date(2026, 9, 20), last_change_date=None,
        warehouse_name="Kvant", region_name="Москва", supplier_article="ART-1",
        nm_id=123, barcode="000111", subject="Постельное бельё", brand="Сказка",
        tech_size="0", total_price=1500.0, discount_percent=10.0,
        finished_price=1350.0, price_with_disc=1350.0, is_cancel=False, g_number="g-1",
    )


def test_execute_upserts_rows_and_records_ok_run():
    rows = [_order_row()]
    source = FakeMarketplaceSource(rows_by_account={"skazka": rows})
    repository = FakeOrderRepository()
    sync_run_repo = FakeSyncRunRepository()
    use_case = SyncOrdersUseCase("wb_orders", source, repository, sync_run_repo)

    result = use_case.execute("skazka")

    assert result.records_fetched == 1
    assert repository.upserts == [("skazka", rows)]
    [run] = sync_run_repo.runs.values()
    assert run == {
        "source": "wb_orders", "account": "skazka", "status": "ok",
        "records_fetched": 1, "error_message": None,
    }


def test_execute_propagates_source_error_and_records_error_run():
    source = FakeMarketplaceSource(error_accounts={"milky_garden"})
    repository = FakeOrderRepository()
    sync_run_repo = FakeSyncRunRepository()
    use_case = SyncOrdersUseCase("wb_orders", source, repository, sync_run_repo)

    with pytest.raises(RuntimeError, match="boom fetching milky_garden"):
        use_case.execute("milky_garden")

    assert repository.upserts == []
    [run] = sync_run_repo.runs.values()
    assert run["status"] == "error"
    assert run["records_fetched"] == 0
    assert "boom fetching milky_garden" in run["error_message"]


def test_repository_failure_is_recorded_and_reraised():
    class ExplodingOrderRepository:
        def upsert(self, account, rows):
            raise ValueError("db is down")

    source = FakeMarketplaceSource(rows_by_account={"skazka": [_order_row()]})
    sync_run_repo = FakeSyncRunRepository()
    use_case = SyncOrdersUseCase("wb_orders", source, ExplodingOrderRepository(), sync_run_repo)

    with pytest.raises(ValueError, match="db is down"):
        use_case.execute("skazka")

    [run] = sync_run_repo.runs.values()
    assert run["status"] == "error"
    assert "db is down" in run["error_message"]
