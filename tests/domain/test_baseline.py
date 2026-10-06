import json
from pathlib import Path

import pytest

from domain.baseline import BaselineInput, ChannelFacts, calculate, quant_key, round_half_away

GOLDEN = json.loads((Path(__file__).parent.parent / "fixtures" / "planning_golden.json").read_text(encoding="utf-8"))


def _input(row: dict) -> BaselineInput:
    v = {k: (x or 0) for k, x in row["in"].items()}
    return BaselineInput(
        wb=ChannelFacts(v["AF"], v["AG"], v["AH"], v["AL"], v["AO"], v["AS"]),
        ozon=ChannelFacts(v["AI"], v["AJ"], v["AK"], v["AM"], v["AP"], v["AT"]),
        stock_fbs=v["AQ"],
        stock_kvant=v["AR"],
        in_production_sklad=v["AU"],
        in_production_kvant=v["AV"],
        quant=row["in"]["BY"],
        category=row["category"],
        days_to_arrival=v["BE"],
        target_turnover_days=v["BF"],
    )


@pytest.mark.parametrize("row", GOLDEN, ids=lambda r: f"row{r['sheet_row']}-{r['case']}")
def test_matches_planning_sheet(row):
    """Rows copied from the analyst's sheet with the values it computed (02.10.2026)."""
    r = calculate(_input(row))
    got = {
        "X": r.wb.max_speed, "Y": r.ozon.max_speed,
        "BO": r.wb.stock_norm, "BP": r.wb.days_stock_lasts, "BQ": r.wb.sold_from_production,
        "BR": r.wb.stock_on_arrival, "BS": r.wb.need,
        "BT": r.ozon.stock_norm, "BU": r.ozon.days_stock_lasts, "BV": r.ozon.sold_from_production,
        "BW": r.ozon.stock_on_arrival, "BX": r.ozon.need,
        "BI": r.need, "BJ": r.need_in_quants,
    }
    for cell, expected in row["out"].items():
        assert got[cell] == pytest.approx(expected, rel=1e-9, abs=1e-9), cell


def test_round_half_away_from_zero():
    assert [round_half_away(x) for x in (0.5, 1.5, 2.5, -0.5, -2.5, 2.4)] == [1, 2, 3, -1, -3, 2]


def _simple(**overrides) -> BaselineInput:
    base = dict(
        wb=ChannelFacts(7, 30, 30, 0, 0, 0),
        ozon=ChannelFacts(0, 0, 0, 0, 0, 0),
        stock_fbs=0, stock_kvant=0, in_production_sklad=0, in_production_kvant=0,
        quant=None, category="",
    )
    base.update(overrides)
    return BaselineInput(**base)


def test_need_is_reduced_by_own_warehouses_and_floored_at_zero():
    assert calculate(_simple()).need == 30
    assert calculate(_simple(stock_fbs=10)).need == 20
    assert calculate(_simple(stock_fbs=100)).need == 0


def test_need_rounded_to_quant_only_for_listed_categories():
    assert calculate(_simple(quant=8, category="постельное")).need == 32
    assert calculate(_simple(quant=8, category="другое")).need == 30


@pytest.mark.parametrize("article, key", [
    ("PT5930/6-17-17/1", "/6-17-17/"),
    ("5930/6-17-17/01", "/6-17-17/0"),
    ("5930/6-17/01", "/6-17/"),
    ("PT5930/6-17", "/6-17"),
    ("PT140", "PT140"),
    ("PT5930/6-17-17/1GIFT", "/6-17-17/GIFT"),
])
def test_quant_key(article, key):
    assert quant_key(article) == key
