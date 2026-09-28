from datetime import date

import pytest

from application.use_cases.sync_sales import SyncSalesUseCase
from domain.models import WbSaleLine
from tests.application.fakes import FakeMarketplaceSource, FakeOrderRepository, FakeSyncRunRepository


def _sale_row(sale_id: str = "sale-1") -> WbSaleLine:
    return WbSaleLine(
        sale_id=sale_id, sale_date=date(2026, 9, 20), last_change_date=None,
        warehouse_name="Kvant", region_name="Москва", supplier_article="ART-1",
        nm_id=123, barcode="000111", subject="Постельное бельё", brand="Сказка",
        tech_size="0", total_price=1500.0, discount_percent=10.0, spp=5.0, for_pay=1200.0,
        finished_price=1350.0, price_with_disc=1350.0, order_type="Клиентский", g_number="g-1",
    )


def test_execute_upserts_rows_and_records_ok_run():
    rows = [_sale_row()]
    source = FakeMarketplaceSource(rows_by_account={"timeless": rows})
    repository = FakeOrderRepository()
    sync_run_repo = FakeSyncRunRepository()
    use_case = SyncSalesUseCase("wb_sales", source, repository, sync_run_repo)

    result = use_case.execute("timeless")

    assert result.records_fetched == 1
    assert repository.upserts == [("timeless", rows)]
    [run] = sync_run_repo.runs.values()
    assert run == {
        "source": "wb_sales", "account": "timeless", "status": "ok",
        "records_fetched": 1, "error_message": None,
    }


def test_execute_propagates_source_error_and_records_error_run():
    source = FakeMarketplaceSource(error_accounts={"skazka"})
    repository = FakeOrderRepository()
    sync_run_repo = FakeSyncRunRepository()
    use_case = SyncSalesUseCase("wb_sales", source, repository, sync_run_repo)

    with pytest.raises(RuntimeError, match="boom fetching skazka"):
        use_case.execute("skazka")

    assert repository.upserts == []
    [run] = sync_run_repo.runs.values()
    assert run["status"] == "error"
    assert run["records_fetched"] == 0
    assert "boom fetching skazka" in run["error_message"]


def test_each_account_gets_its_own_sync_run():
    source = FakeMarketplaceSource(rows_by_account={"skazka": [_sale_row("s-1")], "timeless": [_sale_row("s-2")]})
    repository = FakeOrderRepository()
    sync_run_repo = FakeSyncRunRepository()
    use_case = SyncSalesUseCase("wb_sales", source, repository, sync_run_repo)

    use_case.execute("skazka")
    use_case.execute("timeless")

    assert source.fetch_calls == ["skazka", "timeless"]
    assert [account for account, _ in repository.upserts] == ["skazka", "timeless"]
    assert len(sync_run_repo.runs) == 2
