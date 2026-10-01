from datetime import date, datetime, timezone

from infrastructure.backfill.drive_archive import (
    DriveArchiveBackfill,
    Repos,
    cabinet_account,
    select_files,
    sparse_pick,
)
from infrastructure.sources.drive.client import DriveFile
from tests.infrastructure.test_xlsx_parsers import make_xlsx

FOLDER = "application/vnd.google-apps.folder"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def utc(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 9, day, hour, 0, tzinfo=timezone.utc)


class FakeDrive:
    def __init__(self) -> None:
        self.children: dict[str, list[DriveFile]] = {}
        self.content: dict[str, bytes] = {}
        self.downloads: list[str] = []

    def folder(self, parent: str, folder_id: str, name: str) -> None:
        self.children.setdefault(parent, []).append(DriveFile(folder_id, name, FOLDER, utc(1)))
        self.children.setdefault(folder_id, [])

    def file(self, parent: str, file_id: str, name: str, data: bytes, modified: datetime) -> None:
        self.children.setdefault(parent, []).append(DriveFile(file_id, name, XLSX, modified))
        self.content[file_id] = data

    def list_children(self, folder_id: str) -> list[DriveFile]:
        return list(self.children.get(folder_id, []))

    def find_child(self, folder_id: str, name: str):
        return next((c for c in self.list_children(folder_id) if c.name.strip() == name), None)

    def download(self, file_id: str) -> bytes:
        self.downloads.append(file_id)
        return self.content[file_id]


class FakeSnapshotRepo:
    def __init__(self) -> None:
        self.saved: list[tuple[str, date, list]] = []

    def save_snapshot(self, account, snapshot_date, rows):
        self.saved.append((account, snapshot_date, list(rows)))
        return len(rows)


class FakeUpsertRepo:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, list]] = []

    def upsert(self, account, rows):
        self.upserts.append((account, list(rows)))
        return len(rows)


class FakeBackfillRepo:
    def __init__(self) -> None:
        self.loaded: dict[str, tuple] = {}

    def loaded_file_ids(self):
        return set(self.loaded)

    def mark_loaded(self, drive_file_id, kind, account, file_name, snapshot_date, rows_loaded):
        self.loaded[drive_file_id] = (kind, account, file_name, snapshot_date, rows_loaded)


def make_repos() -> Repos:
    return Repos(
        wb_stock=FakeSnapshotRepo(), wb_price=FakeSnapshotRepo(), wb_ad=FakeUpsertRepo(),
        wb_order=FakeUpsertRepo(), wb_sale=FakeUpsertRepo(), ozon_stock=FakeSnapshotRepo(),
        ozon_price=FakeSnapshotRepo(), ozon_warehouse_stock=FakeSnapshotRepo(), ozon_order=FakeUpsertRepo(), selsup_stock=FakeSnapshotRepo(),
        selsup_movement=FakeUpsertRepo(), backfill=FakeBackfillRepo(),
    )


PRICE_ROWS = [["nmID", "vendorCode", "techSizeName", "sizeID", "price", "discount", "discountedPrice"],
              [1, "PT1/0-0-0/1", "0", 10, 1000, 10, 900]]


def drive_with_wb_prices() -> FakeDrive:
    drive = FakeDrive()
    drive.folder("root", "wb", "Wildberries")
    drive.folder("wb", "cab1", "Кабинет 1 (Сказка)")
    drive.folder("wb", "cab2", "Кабинет 2 (Milky Garden)")
    drive.folder("cab1", "arch1", "Архив")
    data = make_xlsx({"Цены и скидки": PRICE_ROWS})
    drive.file("arch1", "f1", "Цены и скидки 28.09.2026.xlsx", data, utc(29, 22))  # 01:00 MSK next day
    drive.file("arch1", "f2", "Цены и скидки 29.09.2026.xlsx", data, utc(30, 1))
    drive.file("cab1", "f3", "Цены и скидки.xlsx", data, utc(30, 10))
    drive.file("cab2", "f4", "Цены и скидки.xlsx", data, utc(30, 10))
    drive.file("cab1", "f5", "Остатки.xlsx", data, utc(30, 10))  # other report, must be ignored
    return drive


def test_cabinet_account_mapping():
    assert cabinet_account("Кабинет 1 (Сказка)") == "skazka"
    assert cabinet_account("Кабинет 1 (Профтекс)") == "skazka"
    assert cabinet_account("Кабинет 2 (Milky Garden)") == "milky_garden"
    assert cabinet_account("Кабинет 3 (Timeless)") == "timeless"
    assert cabinet_account("Архив") is None


def test_select_files_matches_exact_report_name_oldest_first():
    files = [
        DriveFile("a", "Заказы (30 дней) 29.09.2026.xlsx", XLSX, utc(30)),
        DriveFile("b", "Заказы (7 дней) 29.09.2026.xlsx", XLSX, utc(30)),
        DriveFile("c", "Заказы (30 дней).xlsx", XLSX, utc(30, 20)),
        DriveFile("d", "Заказы (30 дней) 09.09.2026.xlsx", XLSX, utc(10)),
        DriveFile("e", "Заказы (предыдущие 30 дней) 09.09.2026.xlsx", XLSX, utc(10)),
    ]
    assert [f.id for f in select_files(files, "Заказы (30 дней)")] == ["d", "a", "c"]


def test_sparse_pick_keeps_first_and_last_three():
    files = [DriveFile(str(i), f"f{i}", XLSX, utc(1 + i)) for i in range(10)]
    assert [f.id for f in sparse_pick(files)] == ["0", "7", "8", "9"]
    assert sparse_pick(files[:3]) == files[:3]


def test_snapshot_files_get_the_moscow_modification_date_and_other_reports_are_ignored():
    drive, repos = drive_with_wb_prices(), make_repos()
    report = DriveArchiveBackfill(drive, repos, "root", {}).run(kinds={"wb_prices"})

    saved = {(account, day) for account, day, _ in repos.wb_price.saved}
    assert saved == {
        ("skazka", date(2026, 9, 30)),  # 22:00 UTC = 01:00 MSK next day; 01:00 UTC same day is 04:00 MSK
        ("milky_garden", date(2026, 9, 30)),
    }
    assert len(repos.wb_price.saved) == 4
    assert report.totals()["wb_prices"]["files"] == 4
    assert "f5" not in drive.downloads


def test_second_run_skips_loaded_files_and_force_reloads():
    drive, repos = drive_with_wb_prices(), make_repos()
    backfill = DriveArchiveBackfill(drive, repos, "root", {})
    backfill.run(kinds={"wb_prices"})
    second = backfill.run(kinds={"wb_prices"})
    assert second.totals()["wb_prices"] == {"files": 0, "rows": 0, "skipped": 4, "errors": 0}
    assert len(repos.wb_price.saved) == 4

    backfill.run(kinds={"wb_prices"}, force=True)
    assert len(repos.wb_price.saved) == 8


def test_a_modified_file_is_loaded_again():
    drive, repos = drive_with_wb_prices(), make_repos()
    backfill = DriveArchiveBackfill(drive, repos, "root", {})
    backfill.run(kinds={"wb_prices"})
    drive.children["cab2"][0] = DriveFile("f4", "Цены и скидки.xlsx", XLSX, utc(30, 18))
    again = backfill.run(kinds={"wb_prices"})
    assert again.totals()["wb_prices"]["files"] == 1


def test_account_filter_and_max_files():
    drive, repos = drive_with_wb_prices(), make_repos()
    DriveArchiveBackfill(drive, repos, "root", {}).run(
        kinds={"wb_prices"}, accounts={"skazka"}, max_files_per_kind=1
    )
    assert [(a, d) for a, d, _ in repos.wb_price.saved] == [("skazka", date(2026, 9, 30))]


def test_one_broken_file_does_not_stop_the_rest():
    drive, repos = drive_with_wb_prices(), make_repos()
    drive.content["f2"] = b"not an xlsx"
    report = DriveArchiveBackfill(drive, repos, "root", {}).run(kinds={"wb_prices"})
    statuses = sorted(r.status for r in report.results)
    assert statuses == ["error", "loaded", "loaded", "loaded"]
    assert "f2" not in " ".join(repos.backfill.loaded)


def test_window_reports_are_upserted_and_selsup_year_file_goes_first():
    drive, repos = FakeDrive(), make_repos()
    drive.folder("root", "sel", "Selsup ")  # the real folder name has a trailing space
    header = ["тип", "operation", "date", "warehouseId", "orderId", "orderType", "skuId", "артикул",
              "название", "cellName", "quantity", "userId"]
    year = make_xlsx({"x": [header, ["Приёмка", "PUT", "2026-01-07T15:02:02", 10001, 1, "INCOME", 5, "A", "n", "c", 1, 3]]})
    daily = make_xlsx({"x": [header, ["Отгрузка", "TAKE", "2026-09-29T10:00:00", 10001, 2, "OUTCOME", 5, "A", "n", "c", 2, 3]]})
    drive.file("sel", "year", "Приёмки и отгрузки (с начала года).xlsx", year, utc(30))
    drive.file("sel", "d1", "Приёмки и отгрузки 29.09.2026.xlsx", daily, utc(30, 1))

    report = DriveArchiveBackfill(drive, repos, "root", {}).run(kinds={"selsup_movements"})

    assert [len(rows) for _, rows in repos.selsup_movement.upserts] == [1, 1]
    assert repos.selsup_movement.upserts[0][1][0].moved_at.year == 2026
    assert repos.selsup_movement.upserts[0][1][0].movement_type == "Приёмка"
    assert report.totals()["selsup_movements"]["rows"] == 2


def test_a_non_empty_file_that_parses_to_nothing_is_an_error_not_a_loaded_file():
    drive, repos = FakeDrive(), make_repos()
    drive.folder("root", "wb", "Wildberries")
    drive.folder("wb", "cab1", "Кабинет 1 (Сказка)")
    drive.file("cab1", "f1", "Цены и скидки.xlsx", make_xlsx({"x": [["unexpected", "columns"], [1, 2]]}), utc(30))
    report = DriveArchiveBackfill(drive, repos, "root", {}).run(kinds={"wb_prices"})
    assert [r.status for r in report.results] == ["error"]
    assert "format changed" in report.results[0].error
    assert repos.backfill.loaded == {} and repos.wb_price.saved == []


def test_selsup_old_stock_files_are_attributed_through_the_newest_file_with_organization():
    drive, repos = FakeDrive(), make_repos()
    drive.folder("root", "sel", "Selsup")
    base = ["warehouseId", "warehouseName", "skuId", "article", "quantity", "availableQuantity", "calculatedQuantity"]
    old = make_xlsx({"x": [base, [10001, "FBS", 3177, "A", 2, 2, 0]]})
    new = make_xlsx({"x": [base + ["organizationId"], [10001, "FBS", 3177, "A", 4, 4, 0, 100943]]})
    drive.file("sel", "o", "Остатки 06.08.2026.xlsx", old, utc(6))
    drive.file("sel", "n", "Остатки 21.08.2026.xlsx", new, utc(21))

    DriveArchiveBackfill(drive, repos, "root", {100943: "timeless"}).run(kinds={"selsup_stocks"})

    saved = {(account, day): [line.quantity for line in lines] for account, day, lines in repos.selsup_stock.saved}
    assert saved == {("timeless", date(2026, 9, 6)): [2.0], ("timeless", date(2026, 9, 21)): [4.0]}


def test_a_file_can_feed_two_kinds_the_second_is_not_mistaken_for_loaded():
    """`Остатки` of Ozon feeds ozon_stocks (legacy key) and ozon_warehouse_stocks
    (prefixed key): loading one must not mark the other as done."""
    drive, repos = FakeDrive(), make_repos()
    drive.folder("root", "oz", "Ozon")
    drive.folder("oz", "cab1", "Кабинет 1 (Профтекс)")
    data = make_xlsx({
        "Остатки": [["offer_id", "product_id", "type", "present", "reserved", "sku"], ["A", 1, "fbo", 3, 0, 9]],
        "Остатки по складам FBO": [
            ["offer_id", "sku", "товар", "склад", "доступно", "резерв", "в пути"], ["A", 9, "n", "ХОРУГВИНО_РФЦ", 3, 1, 2],
        ],
    })
    drive.file("cab1", "f1", "Остатки.xlsx", data, utc(30))
    backfill = DriveArchiveBackfill(drive, repos, "root", {})

    backfill.run(kinds={"ozon_stocks"})
    assert len(repos.ozon_stock.saved) == 1 and repos.ozon_warehouse_stock.saved == []

    second = backfill.run(kinds={"ozon_stocks", "ozon_warehouse_stocks"})
    totals = second.totals()
    assert totals["ozon_stocks"]["skipped"] == 1  # already loaded under its legacy key
    assert totals["ozon_warehouse_stocks"]["files"] == 1
    [(account, day, lines)] = repos.ozon_warehouse_stock.saved
    assert (account, lines[0].warehouse_name, lines[0].free_to_sell, lines[0].promised) == ("skazka", "ХОРУГВИНО_РФЦ", 3, 2)
