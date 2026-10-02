from datetime import date

import pytest

from infrastructure.sources.sheets.common import SheetFormatError
from infrastructure.sources.sheets.planning import (
    SheetTableSource,
    parse_article_specs,
    parse_fabric_stock,
    parse_fabrics,
    parse_quant_multiples,
)
from infrastructure.sources.sheets.production import ProductionSheetSource, parse_production_rows

HEADER = [
    "Артикул", "количество", "регион", "заказ", "размер", "метраж", "бренд", "цех", "метраж2",
    "Номер недели", "Направление", "количество всего", "Ключ", "Количество", "Факт количество",
    "Факт дата отгрузки", "Факт дата приемки", "статус", "начало недели", "конец недели", "№ приемки",
]


def row(article="151/5-0-0/1", qty=2, region="Центральный", order="заказ Задание Солях", meters="7,28",
        brand="timeless", workshop="Солях", meters2=0, week=14, direction="склад", total=1137,
        key="14соляхtimeless1137", task_qty=1137, fact=1097, ship="21.04.2026", accept="21.04.2026",
        status="выпущено", start="30.03.2026", end="05.04.2026", receipt=None):
    values = [article, qty, region, order, "", meters, brand, workshop, meters2, week, direction, total,
              key, task_qty, fact, ship, accept, status, start, end, receipt]
    while values and values[-1] in (None, ""):  # the Sheets API drops trailing empty cells
        values.pop()
    return values


def test_a_production_row_is_cleaned_and_typed():
    parsed = parse_production_rows([HEADER, row()])
    (line,) = parsed.lines
    assert (line.article, line.quantity, line.region, line.meters) == ("151/5-0-0/1", 2, "Центральный", 7.28)
    assert (line.brand, line.workshop, line.direction) == ("Timeless", "Солях", "sklad")
    assert (line.week_number, line.week_start, line.week_end) == (14, date(2026, 3, 30), date(2026, 4, 5))
    assert (line.task_total, line.task_quantity, line.fact_quantity) == (1137, 1137, 1097)
    assert (line.fact_ship_date, line.fact_accept_date) == (date(2026, 4, 21), date(2026, 4, 21))
    assert (line.status, line.status_group, line.receipt_no, line.sheet_row) == ("выпущено", "released", None, 2)
    assert parsed.issues == {}


def test_free_text_in_the_numeric_columns_does_not_break_the_row():
    parsed = parse_production_rows([HEADER, row(meters2="заказ вывоз фбс Неделя 25 задание №6.xlsx", fact="", qty="")])
    (line,) = parsed.lines
    assert line.meters2 is None and line.fact_quantity is None and line.quantity is None
    assert parsed.issues == {"row quantity missing": 1}


def test_date_problems_are_counted_and_the_good_dates_still_loaded():
    parsed = parse_production_rows([HEADER, row(ship="12.08, 14.08", accept="10.07.0206")])
    (line,) = parsed.lines
    assert line.fact_ship_date == date(2026, 8, 14) and line.fact_ship_raw == "12.08, 14.08"
    assert line.fact_accept_date is None and line.fact_accept_raw == "10.07.0206"  # the raw text is kept
    assert parsed.issues == {
        "ship date: several dates (last one used)": 1,
        "accept date: typo / out of range": 1,
    }


def test_row_key_ignores_what_people_update_and_where_the_row_sits():
    before = parse_production_rows([HEADER, row(), row(article="A/1-0-0/1")]).lines
    # the fact, status and receipt number were filled in, and a row was inserted above
    after = parse_production_rows([
        HEADER, row(article="B/1-0-0/1"), row(article="A/1-0-0/1", status="закрыт", fact=5, receipt="4126"), row(),
    ]).lines
    keys_before = {l.article: l.row_key for l in before}
    keys_after = {l.article: l.row_key for l in after}
    assert keys_before["151/5-0-0/1"] == keys_after["151/5-0-0/1"]
    assert keys_before["A/1-0-0/1"] == keys_after["A/1-0-0/1"]
    assert "B/1-0-0/1" in keys_after and keys_after["B/1-0-0/1"] not in keys_before.values()


def test_identical_rows_get_distinct_keys_and_a_changed_quantity_is_a_new_row():
    twins = parse_production_rows([HEADER, row(), row()]).lines
    assert len({l.row_key for l in twins}) == 2
    changed = parse_production_rows([HEADER, row(qty=3)]).lines
    assert changed[0].row_key not in {l.row_key for l in twins}


def test_receipt_number_floats_are_text_not_1234_dot_0():
    (line,) = parse_production_rows([HEADER, row(receipt=41268617.0)]).lines
    assert line.receipt_no == "41268617"


def test_blank_rows_are_skipped_silently_and_rows_without_article_are_counted():
    parsed = parse_production_rows([HEADER, [], ["", "", ""], row(article=""), row()])
    assert len(parsed.lines) == 1
    assert parsed.issues == {"skipped: no article": 1}


def test_a_renamed_column_stops_the_load():
    broken = [name if name != "статус" else "состояние" for name in HEADER]
    with pytest.raises(SheetFormatError, match="статус"):
        parse_production_rows([broken, row()])
    with pytest.raises(SheetFormatError):
        parse_production_rows([])


class FakeReader:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def read(self, spreadsheet_id, sheet_title, cell_range=None):
        self.calls.append((spreadsheet_id, sheet_title))
        return self.rows


def test_production_source_reads_the_configured_sheet_and_rejects_an_empty_one():
    reader = FakeReader([HEADER, row()])
    assert len(ProductionSheetSource(reader, "book1").fetch("all")) == 1
    assert reader.calls == [("book1", "база данных")]
    with pytest.raises(SheetFormatError):
        ProductionSheetSource(FakeReader([HEADER]), "book1").fetch("all")


QUANT_HEADER = ["", "ключ", "Название", "размер", "Объем Л", "в коробке", "расчет", "желаемое", "финальное",
                "Финальное V4", "Финальное V5"]


def test_quant_multiples_use_the_v5_column_and_skip_rows_without_a_key():
    rows = [
        QUANT_HEADER,
        [1, "/0-0-13/", "Декоративная наволочка 40х60", 0, 0.5, 60, 65, "", "", "", 60],
        [4, "", "", "", "", "", "", "", "", "", "", "", "протекс", 60],  # the supplier table to the right
        [11, "/0-13-0/", "Простыня 90х200", 0, "1,51", 19, 21, 15, 15, 15, 15],
        [12, "/0-11-0/", "Простыня малышам", 0, 2.44, 36, 13],  # no final value yet
    ]
    by_key = {q.size_key: q for q in parse_quant_multiples(rows)}
    assert set(by_key) == {"/0-0-13/", "/0-13-0/", "/0-11-0/"}
    assert by_key["/0-0-13/"].quant == 60 and by_key["/0-13-0/"].volume_liters == 1.51
    assert by_key["/0-11-0/"].quant is None and by_key["/0-11-0/"].calculated_in_box == 13


def test_quant_sheet_with_moved_columns_is_rejected():
    with pytest.raises(SheetFormatError):
        parse_quant_multiples([["", "ключ", "Название"], [1, "/0-0-13/"]])


SPEC_HEADER = ["бренд", "ткань1", "ткань2", "остаток", "ВБ", "x", "y", "z", "t", "u", "v", "w", "регион", "назначение",
               "артикул", "размер", "м/изд1", "м/изд2"]


def spec_row(brand, fabric1, fabric2, region, purpose, article, size, m1, m2):
    return [brand, fabric1, fabric2, "", "", "", "", "", "", "", "", "", region, purpose, article, size, m1, m2]


def test_article_specs_take_the_first_non_empty_value_across_marketplace_rows():
    rows = [
        SPEC_HEADER,
        spec_row("Анна Мария", 6274.0, "", "ВБ", "постельное", "PA6274/4-18-28/0D", "", "4,17", ""),
        spec_row("Анна Мария", "", "", "ОЗОН", "постельное", "PA6274/4-18-28/0D", "", "", ""),  # sparse duplicate
        spec_row("", "", "", "ВБ", "", "", "", "", ""),  # no article
    ]
    (spec,) = parse_article_specs(rows)
    assert spec.article == "PA6274/4-18-28/0D" and spec.brand_name == "Анна Мария"
    assert spec.fabric_no_1 == "6274" and spec.fabric_no_2 is None  # 6274.0 is not "6274.0"
    assert spec.meters_per_item_1 == 4.17 and spec.meters_per_item_2 is None and spec.purpose == "постельное"


FABRIC_HEADER = ["ткань", "материал", "ценовая категория", "Длина рулона,м", "Ширина рулона, см", "детям / взрослый",
                 "дубликат", "Цвет", "Рисунок для WB", "Тип плетения ткани", "Название для Профтекс", "Название ИП",
                 "ткань", "краткое название", "артикул поставщика", "Код поставщика2", "Поставщик", "Изделия"]


def test_fabrics_are_keyed_by_the_second_ткань_column_and_deduplicated():
    rows = [
        FABRIC_HEADER,
        ["110", "перкаль", "1 - перкаль", 40, 220, "взрослым", "1", "бежевый", "орнамент", "перкаль", "", "",
         110, "Пэчворк (теплый)", "рис 3821", "TKV", "Тейково", ""],
        ["", "", "", "", "", "", "", "", "", "", "", "", 110, "повтор"],  # the same fabric again: first wins
        ["35", "тдр", "тдр", "", "", "", "", "", "", "", "", "", "", "лоскуты"],  # no key in M: column A is used
    ]
    fabrics = {f.fabric_no: f for f in parse_fabrics(rows)}
    assert set(fabrics) == {"110", "35"}
    assert fabrics["110"].short_name == "Пэчворк (теплый)" and fabrics["110"].roll_length_m == 40
    assert fabrics["110"].supplier == "Тейково"


def test_fabric_stock_rows():
    rows = [
        ["номер ткани", "количество", "поставщик", "название", "Есть в вывозе"],
        [4043, "480", "тетрагон", "Loki", "Сказка."],
        ["", 5, "x", "no number", ""],
        ["110", 1100.5, "тетрагон", "Пэчворк", "Timeless"],
    ]
    stock = parse_fabric_stock(rows)
    assert [(s.fabric_no, s.quantity_m, s.brand) for s in stock] == [("4043", 480.0, "Сказка"), ("110", 1100.5, "Timeless")]


def test_a_sheet_that_parses_to_nothing_is_an_error_not_an_empty_load():
    source = SheetTableSource(FakeReader([["номер ткани", "количество", "поставщик", "название", "Есть в вывозе"]]),
                              "book", "наличие ткани", parse_fabric_stock)
    with pytest.raises(SheetFormatError, match="nothing recognised"):
        source.fetch("all")
