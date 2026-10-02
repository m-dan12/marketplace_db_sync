from datetime import date

import pytest

from domain import production as p

WEEK = date(2026, 6, 22)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("10.07.2026", [date(2026, 7, 10)]),
        ("7.9.2026", [date(2026, 9, 7)]),
        ("05.06.26", [date(2026, 6, 5)]),
        ("05/6/26", [date(2026, 6, 5)]),
        ("21/04/2026", [date(2026, 4, 21)]),
        ("12.08, 14.08", [date(2026, 8, 12), date(2026, 8, 14)]),  # no year: the week's year
        ("12.08,14.08.2026", [date(2026, 8, 12), date(2026, 8, 14)]),
        ("10.07.2026, 12.07.2026, 13.07.2026", [date(2026, 7, 10), date(2026, 7, 12), date(2026, 7, 13)]),
        ("21.04.2026, ", [date(2026, 4, 21)]),
        (46200, [date(2026, 6, 27)]),  # a real date cell comes as a serial number
        ("", []),
        (None, []),
        ("в цехе - подтверждено", []),
    ],
)
def test_parse_dates(value, expected):
    assert list(p.parse_dates(value, WEEK).dates) == expected


def test_a_year_typo_is_counted_and_dropped_not_loaded():
    parsed = p.parse_dates("10.07.0206", WEEK)
    assert parsed.dates == () and parsed.invalid == 1
    assert p.parse_dates("31.02.2026", WEEK).invalid == 1  # no such day
    assert p.parse_dates("10.07.2031", WEEK).invalid == 1  # out of range


def test_without_a_reference_a_date_without_year_is_unusable():
    parsed = p.parse_dates("12.08", None)
    assert parsed.dates == () and parsed.invalid == 1


def test_a_short_date_typed_in_a_december_week_rolls_over_to_january():
    assert list(p.parse_dates("05.01", date(2025, 12, 29)).dates) == [date(2026, 1, 5)]


def test_the_last_of_several_dates_is_the_one_to_use():
    assert p.parse_dates("14.08, 12.08", WEEK).last == date(2026, 8, 14)
    assert p.parse_dates("", WEEK).last is None


@pytest.mark.parametrize(
    "value, expected",
    [("7,28", 7.28), (" 5\xa0710 ", 5710.0), (12, 12.0), (3.5, 3.5), ("", None), (None, None),
     ("заказ вывоз фбс Неделя 25.xlsx", None), (True, None)],
)
def test_parse_number(value, expected):
    assert p.parse_number(value) == expected


def test_parse_int_rounds_and_tolerates_text():
    assert p.parse_int("1 137") == 1137
    assert p.parse_int("2,0") == 2
    assert p.parse_int("") is None


def test_brand_spellings_are_merged():
    assert p.normalize_brand("Сказка.") == "Сказка"
    assert p.normalize_brand(" timeless ") == "Timeless"
    assert p.normalize_brand("Сказка Сатин") == "Сказка Сатин"  # a different brand, not folded into Сказка
    assert p.normalize_brand("Новый бренд") == "Новый бренд"  # unknown: kept as typed
    assert p.normalize_brand("") is None


def test_workshop_spellings_are_merged():
    assert p.normalize_workshop("наш цех") == p.normalize_workshop("Наш Цех") == "Наш цех"
    assert p.normalize_workshop("Гав ям") == p.normalize_workshop("Гав ЯМ") == "Гав ЯМ"
    assert p.normalize_workshop("Солях") == "Солях"


def test_direction_and_region_spellings():
    assert [p.normalize_direction(v) for v in ("склад", "озон", "ВБ", "складквант", "СкладКвант", "??")] == [
        "sklad", "ozon", "wb", "sklad_kvant", "sklad_kvant", None,
    ]
    assert p.normalize_region("вб") == p.normalize_region("ВБ") == "ВБ"
    assert p.normalize_region("складквант") == "СкладКвант"
    assert p.normalize_region("Центральный") == "Центральный"  # a distribution region stays as is


@pytest.mark.parametrize(
    "status, group",
    [
        ("в производстве", "in_production"),
        ("в приемке на склад", "in_acceptance"),
        ("в приемке на вб", "in_acceptance"),
        ("готово,не вывезено", "ready"),
        ("выпущено", "released"),
        ("закрыт", "closed"),
        ("не определено", "unknown"),
        (None, "unknown"),
        ("что-то новое", "other"),
    ],
)
def test_status_groups(status, group):
    assert p.status_group(status) == group


def test_status_case_duplicates_collapse():
    assert p.normalize_status("В приемке на ВБ") == p.normalize_status("в приемке на вб ")
    assert p.status_group(p.normalize_status("в приемке на складКвант")) == "in_acceptance"
