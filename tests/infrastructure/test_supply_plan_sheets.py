from datetime import date

import pytest

from infrastructure.sources.sheets.common import SheetFormatError
from infrastructure.sources.sheets.supply_plan import (
    FabricReceiptsSheetSource,
    parse_invoices,
    parse_week,
    week_start_of,
)

START = date(2026, 9, 21)
HEADER = ["Дата вх,", "цех", "цена", "номенклатура поставщика/ № док", "метраж", "суммаподок", "накладная", "",
          "№ ткани", "ткань", "Бренд"]


def padded(*cells, size=14):
    return list(cells) + [""] * (size - len(cells))


# The 42-column layout: the supplier is on the invoice row, the fabric lines start with a blank cell.
WEEK_42 = [
    ["неделя 39", "старт номер недели", "68"],
    HEADER,
    padded("номер задания", "68", *[""] * 11, "неделя 39 №39_00068(АМ) цех Солях Анна"),
    ["24.09.2026", "Солях", "", "юниколор профтекс ТКВ-31497 от 23.09.2", 1275.9, 269151.1, "", "", "", "", "Анна Мария"],
    ["", "", 210.95, "рис 3833 вид 1", 80, 16876, "ЮНК-05956 от 23.09.2026", "", "3526", "Классика сатин", "Анна Мария"],
    ["", "", 210.95, "рис 3785 вид 6", "160,5", 33752, "ЮНК-05956 от 23.09.2026", "", "3761", "Прохоровская роза", "анна мария"],
    [],
    ["", "", "", "", 0, "", "", "", "", "", ""],  # a blank line of the block: no metres
]

# The 52-column layout: the supplier text is in column A of the fabric lines.
WEEK_52 = [
    ["неделя 33"],
    ["Дата документа", "цех", "цена", "номенклатура поставщика/ № док", "метраж", "суммаподок", "М-15", "",
     "№ ткани", "ткань", "Бренд"],
    ["13.08.2026", "Солях", "", "", 3303.9, 622986.3, "", "", "", "", "Milky Garden"],  # no task row before it
    ["люксор яковлев", "", 151, "3075Н А", 180, 27180, "", "", 2209, "Созвездия", "Milky Garden"],
]


def test_week_start_comes_from_the_sheet_title():
    assert week_start_of("21.09-27.09 26") == date(2026, 9, 21)
    assert week_start_of("01.06 - 07.06 26") == date(2026, 6, 1)
    assert week_start_of("07.07 -13.07 25") == date(2025, 7, 7)
    assert week_start_of("свод 2026") is None and week_start_of("логистика") is None


def test_receipts_carry_invoice_date_workshop_task_and_fabric():
    lines = parse_week(WEEK_42, "21.09-27.09 26", START)
    assert [(l.sheet_row, l.meters, l.fabric_no, l.fabric_name, l.brand) for l in lines] == [
        (5, 80.0, "3526", "Классика сатин", "Анна Мария"),
        (6, 160.5, "3761", "Прохоровская роза", "Анна Мария"),
    ]
    first = lines[0]
    assert (first.received_date, first.workshop, first.task_number) == (date(2026, 9, 24), "Солях", "68")
    assert first.task_text == "неделя 39 №39_00068(АМ) цех Солях Анна"
    assert (first.price, first.amount, first.document) == (210.95, 16876.0, "ЮНК-05956 от 23.09.2026")
    assert first.supplier_text == "юниколор профтекс ТКВ-31497 от 23.09.2"  # from the invoice row


def test_the_other_layout_takes_the_supplier_from_the_line_and_may_have_no_task():
    (line,) = parse_week(WEEK_52, "10.08-16.08 26", date(2026, 8, 10))
    assert line.supplier_text == "люксор яковлев" and line.task_number is None
    assert (line.received_date, line.fabric_no, line.meters) == (date(2026, 8, 13), "2209", 180.0)


def test_a_date_inside_a_text_is_not_an_invoice_row():
    rows = [WEEK_42[0]] + [WEEK_42[1]] + [
        ["24.09.2026", "Солях", "", "док", 100, 1, "", "", "", "", ""],
        ["упд от 23.09.2026", "", 10, "рис", 50, 1, "", "", "1", "ткань", ""],
    ]
    (line,) = parse_week(rows, "t", START)
    assert line.supplier_text == "упд от 23.09.2026" and line.meters == 50.0


def test_a_renamed_column_is_refused():
    broken = [WEEK_42[0], [*HEADER[:4], "длина", *HEADER[5:]]]
    with pytest.raises(SheetFormatError):
        parse_week(broken, "21.09-27.09 26", START)


class FakeBook:
    def __init__(self, sheets):
        self.sheets = sheets
        self.reads = []

    def titles(self, spreadsheet_id):
        return list(self.sheets)

    def read(self, spreadsheet_id, sheet_title, cell_range=None):
        self.reads.append(sheet_title)
        return self.sheets[sheet_title]


def test_source_reads_only_weekly_sheets():
    book = FakeBook({"свод 2026": [["x"]], "21.09-27.09 26": WEEK_42, "10.08-16.08 26": WEEK_52})
    lines = FabricReceiptsSheetSource(book, "book", pause_seconds=0).fetch("all")
    assert book.reads == ["21.09-27.09 26", "10.08-16.08 26"] and len(lines) == 3


def test_source_refuses_a_workbook_without_weekly_sheets():
    with pytest.raises(SheetFormatError):
        FabricReceiptsSheetSource(FakeBook({"свод 2026": []}), "book", pause_seconds=0).fetch("all")


CLAIM_HEADER = HEADER + [""] * 8 + ["статус", "претензия", "метраж претензии", "сумма претензии", "статус претензии",
                                    "дата компенсации"]


def claim_row(first_cells, **cells):
    row = padded(*first_cells, size=25)
    for column, value in cells.items():
        row[int(column[1:])] = value
    return row


WEEK_WITH_CLAIMS = [
    ["неделя 25"],
    CLAIM_HEADER,
    padded("номер задания", "18"),
    claim_row(["15.06.2026", "Солях", "", "белтекс профтекс УПД 5", 480, 65000], c19="брак", c20="№178", c21=63,
              c22=9954, c23="пр. отправлена 19.06", c24="25.06.2026"),
    claim_row(["", "", 135.58, "рис 1", 480, 65000, "", "", "1", "ткань", "Сказка"], c19="не брать", c20="не брать"),
    claim_row(["17.06.2026", "Инна", "", "протекс", 60, 9000], c19="без брака", c20=0, c21=0, c22=0, c23=0),
]


def test_invoices_carry_the_defect_and_claim_cells():
    invoices = parse_invoices(WEEK_WITH_CLAIMS, "15.06-21.06 26", date(2026, 6, 15))
    assert [(i.sheet_row, i.workshop, i.task_number, i.meters) for i in invoices] == [
        (4, "Солях", "18", 480.0), (6, "Инна", "18", 60.0),
    ]
    first, second = invoices
    assert (first.defect_status, first.claim_no, first.claim_meters, first.claim_amount) == ("брак", "№178", 63.0, 9954.0)
    assert (first.claim_status, first.compensation_date) == ("пр. отправлена 19.06", date(2026, 6, 25))
    # zeros are "nothing recorded", and a fabric line's own cells in the same columns are not read
    assert (second.defect_status, second.claim_no, second.claim_status, second.compensation_date) == ("без брака", None, None, None)


def test_a_week_without_the_claim_columns_gives_invoices_without_claim_data():
    (invoice,) = parse_invoices(WEEK_52, "10.08-16.08 26", date(2026, 8, 10))
    assert invoice.meters == 3303.9 and invoice.defect_status is None and invoice.claim_no is None


def test_both_sources_share_one_read_of_the_sheets():
    book = FakeBook({"15.06-21.06 26": WEEK_WITH_CLAIMS})
    receipts = FabricReceiptsSheetSource(book, "book", pause_seconds=0)
    assert len(receipts.fetch("all")) == 1 and len(receipts.invoices().fetch("all")) == 2
    assert book.reads == ["15.06-21.06 26"]
