import io
from datetime import date, datetime

from openpyxl import Workbook

from infrastructure.sources.drive import xlsx


def make_xlsx(sheets: dict[str, list[list]]) -> bytes:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets.items():
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_read_rows_uses_first_row_as_header_and_skips_blank_rows():
    data = make_xlsx({"A": [["x", "y"], [1, "a"], [None, None], [2, "b"]]})
    assert xlsx.read_rows(data) == [{"x": 1, "y": "a"}, {"x": 2, "y": "b"}]


def test_read_rows_named_sheet():
    data = make_xlsx({"first": [["a"], [1]], "second": [["b"], [2]]})
    assert xlsx.read_rows(data, "second") == [{"b": 2}]


def test_scalar_helpers_normalise_excel_floats():
    assert xlsx.to_str("598863115.0") == "598863115"
    assert xlsx.to_str(12.0) == "12"
    assert xlsx.to_str("  ") is None
    assert xlsx.to_int("31217.0") == 31217
    assert xlsx.to_int("") is None and xlsx.to_int("abc") is None
    assert xlsx.to_float("1 555") is None  # never guess at formatted numbers


def test_wb_stocks_wide_to_long_keeps_only_nonzero_warehouses():
    rows = [
        {
            "nmId": 1, "vendorCode": "PT1/0-0-0/1", "barcode": "b", "techSize": "0", "volume": 4.97,
            "В пути до получателей": 9, "Всего находится на складах": 293,
            "Коледино": 129, "Тула": 0, "Казань": None,
        },
        {"nmId": None, "vendorCode": "skip", "Коледино": 5},
    ]
    lines = xlsx.parse_wb_stocks(rows)
    assert {(line.warehouse_name, line.quantity) for line in lines} == {
        ("В пути до получателей", 9), ("Всего находится на складах", 293), ("Коледино", 129),
    }
    assert all(line.nm_id == 1 and line.vendor_code == "PT1/0-0-0/1" for line in lines)


def test_wb_orders_types():
    rows = [{
        "date": "2026-08-29T23:13:37", "lastChangeDate": "2026-08-30T00:05:42", "warehouseName": "Тула",
        "supplierArticle": "PT129/0-16-0/0", "nmId": "167680885", "barcode": 4650322500571, "techSize": "0",
        "totalPrice": "3479", "discountPercent": "40", "isCancel": "False", "srid": "abc",
    }, {"srid": None}]
    [line] = xlsx.parse_wb_orders(rows)
    assert line.order_date == date(2026, 8, 29)
    assert line.last_change_date == datetime(2026, 8, 30, 0, 5, 42)
    assert line.nm_id == 167680885 and line.barcode == "4650322500571"
    assert line.total_price == 3479.0 and line.is_cancel is False


def test_ozon_orders_use_russian_column_names():
    rows = [{
        "posting_number": "69587799-0117-1", "status": "cancelled", "order_date": "2026-08-30T00:23:12Z",
        "склад_отгрузки": "Клин", "город_доставки": None, "регион": None, "offer_id": "PT5931/0-0-21/1",
        "sku": "3243950351", "quantity": "1", "price": "674.0", "currency": "RUB",
    }]
    [line] = xlsx.parse_ozon_orders(rows)
    assert line.warehouse_name == "Клин" and line.quantity == 1 and line.price == 674.0
    assert line.order_date.year == 2026 and line.order_date.tzinfo is not None


def test_selsup_stocks_split_by_account_and_skip_zero_and_unknown_org():
    def row(org, qty, sku=1):
        return {
            "warehouseId": "10001", "warehouseName": "FBS", "skuId": sku, "article": "A", "wbSize": "5.0",
            "quantity": qty, "availableQuantity": qty, "calculatedQuantity": 0,
            "modifyDate": "2026-09-18T12:12:18Z", "organizationId": org,
        }

    by_account = xlsx.parse_selsup_stocks(
        [row(100943.0, 2), row(100980, 0, sku=2), row(999, 5, sku=3), row(100980, 1, sku=4)],
        {100943: "timeless", 100980: "skazka"},
    )
    assert {a: [line.sku_id for line in lines] for a, lines in by_account.items()} == {
        "timeless": [1], "skazka": [4],
    }
    assert by_account["timeless"][0].wb_size == "5"


def test_selsup_movements_naive_datetime_and_skips_incomplete_rows():
    rows = [
        {"тип": "Приёмка", "operation": "PUT", "date": "2026-09-29T14:41:25", "warehouseId": None, "orderId": None,
         "skuId": "23808", "артикул": "PT4214/4-0-0/1", "название": "n", "cellName": "51-A", "quantity": "2",
         "userId": "31217.0"},
        {"тип": "Приёмка", "operation": "PUT", "date": None, "skuId": "1", "quantity": "1"},
    ]
    [line] = xlsx.parse_selsup_movements(rows)
    assert line.moved_at == datetime(2026, 9, 29, 14, 41, 25) and line.moved_at.tzinfo is None
    assert line.sku_id == 23808 and line.user_id == 31217 and line.quantity == 2.0


def test_wb_ads_sheet_rows():
    rows = [{
        "Дата": "2026-09-19", "ID кампании": "39742288", "Статус": "активна", "nmId": "167680989",
        "Показы": "101", "Клики": "3", "CTR%": "2.97", "CPC": "12.1", "Затраты": "36.3",
        "Заказы": "0", "Корзина": "0", "Ср.позиция": "96.0",
    }, {"Дата": None, "ID кампании": 1, "nmId": 2}]
    [line] = xlsx.parse_wb_ads(rows)
    assert line.stat_date == date(2026, 9, 19) and line.campaign_id == 39742288 and line.spend == 36.3


def test_old_selsup_stock_files_without_organization_use_the_sku_account_map():
    old_row = {
        "warehouseId": "10001", "warehouseName": "FBS", "skuId": "3177", "article": "126/41-13-26/1",
        "quantity": "2", "availableQuantity": "2", "calculatedQuantity": "0",
    }
    unknown = {**old_row, "skuId": "999"}
    zero = {**old_row, "skuId": "3178", "quantity": "0"}
    by_account = xlsx.parse_selsup_stocks(
        [old_row, unknown, zero], {100943: "timeless"}, {3177: "timeless", 3178: "timeless"}
    )
    assert {a: [line.sku_id for line in lines] for a, lines in by_account.items()} == {"timeless": [3177]}
    assert xlsx.parse_selsup_stocks([old_row], {100943: "timeless"}) == {}  # no map -> nothing guessed


def test_sku_accounts_from_rows():
    rows = [{"skuId": 1.0, "organizationId": 100943.0}, {"skuId": 2, "organizationId": 5}, {"skuId": None}]
    assert xlsx.sku_accounts_from_rows(rows, {100943: "timeless"}) == {1: "timeless"}
