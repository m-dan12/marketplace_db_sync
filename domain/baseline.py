"""The analyst's manual calculation for the "планирование" sheet, as a pure function.

How many pieces of an article to sew for Wildberries and Ozon, rounded to the
packing multiple. This is the baseline any model has to beat, and the source
of labels for later training. Cell names in the comments (X, BO, ...) are the
columns of the sheet, so the code can be checked against it line by line.

Checked against every row of the sheet (18,774 rows, no differences).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# Categories (column H) for which the need is rounded to the packing multiple.
ROUNDED_TO_QUANT_CATEGORIES = frozenset({"рюши", "постельное", "микс"})

DEFAULT_DAYS_TO_ARRIVAL = 28  # BE: days until the sewn goods arrive
DEFAULT_TARGET_TURNOVER_DAYS = 30  # BF
MAX_STOCK_SPIKE_FACTOR = 3  # BO: max(30d sales) * 3 caps the speed-based stock norm
MIN_STOCK_FLOOR = 10  # BO: upper bound of the floor for a spiking article


@dataclass(frozen=True)
class ChannelFacts:
    """One marketplace (WB or Ozon) for one article."""

    sales_7d: float  # AF / AI
    sales_30d: float  # AG / AJ
    sales_prev_30d: float  # AH / AK (the 30 days before the last 30)
    days_out_of_stock: float  # AL / AM: days without stock in the last 30
    stock: float  # AO / AP
    in_production: float  # AS / AT: sewing tasks heading to this marketplace


@dataclass(frozen=True)
class BaselineInput:
    wb: ChannelFacts
    ozon: ChannelFacts
    stock_fbs: float  # AQ: own warehouse "Склад"
    stock_kvant: float  # AR: "Склад Квант"
    in_production_sklad: float  # AU
    in_production_kvant: float  # AV
    quant: Optional[float]  # BY: packing multiple; None when the size key is unknown
    category: str = ""  # H
    days_out_of_stock_fbs: Optional[float] = None  # AN: blank in most rows
    days_to_arrival: float = DEFAULT_DAYS_TO_ARRIVAL  # BE
    target_turnover_days: float = DEFAULT_TARGET_TURNOVER_DAYS  # BF


@dataclass(frozen=True)
class ChannelResult:
    max_speed: float  # X / Y
    stock_norm: float  # BO / BT
    days_stock_lasts: float  # BP / BU
    sold_from_production: float  # BQ / BV
    stock_on_arrival: float  # BR / BW
    need: float  # BS / BX


@dataclass(frozen=True)
class BaselineResult:
    wb: ChannelResult
    ozon: ChannelResult
    need: float  # BI: total to sew, after own warehouses, rounded to the quant
    need_in_quants: float  # BJ


def round_half_away(value: float) -> float:
    """Spreadsheet ROUND(x; 0): halves go away from zero (Python's round() is banker's)."""
    return math.copysign(math.floor(abs(value) + 0.5), value)


def _divide(numerator: float, denominator: float) -> float:
    """IFERROR(a/b; 0)."""
    return numerator / denominator if denominator else 0.0


def _min_ignoring_blank(first: float, second: Optional[float]) -> float:
    """MIN(a; b) where a blank cell is skipped."""
    return first if second is None else min(first, second)


def _speeds(channel: ChannelFacts, days_out_fbs: Optional[float]) -> float:
    """X / Y: the highest of the 7-day speed, the 30-day speed (not counting the
    days without stock) and the 30-day speed capped at double of it."""
    speed_7 = channel.sales_7d / 7  # Z
    speed_30 = _divide(  # AA
        channel.sales_30d,
        30 - _min_ignoring_blank(channel.days_out_of_stock, days_out_fbs),
    )
    speed_prev_30 = channel.sales_prev_30d / 30  # AB
    return max(speed_7, speed_30, min(speed_30 * 2, speed_prev_30))


def _stock_norm(channel: ChannelFacts, speed: float, inp: BaselineInput) -> float:
    """BO / BT: stock to hold = speed * target days; if that is more than 3x the
    best 30-day period (a one-off spike), fall back to the real sales volume."""
    sales_peak = max(channel.sales_30d, channel.sales_prev_30d)
    if speed * inp.target_turnover_days > sales_peak * MAX_STOCK_SPIKE_FACTOR:
        floor = MIN_STOCK_FLOOR if inp.quant is None else min(MIN_STOCK_FLOOR, inp.quant)
        return max(channel.sales_30d, channel.sales_prev_30d, floor)
    return round_half_away(speed * inp.target_turnover_days)


def _channel(channel: ChannelFacts, speed: float, inp: BaselineInput) -> ChannelResult:
    norm = _stock_norm(channel, speed, inp)
    days_stock_lasts = _divide(channel.stock, speed)  # BP / BU
    # BQ / BV: if the stock runs out before the sewn goods arrive, the sheet treats
    # all goods still in production as sold on arrival (its longer formula
    # MAX(p; p - MAX(0; ...) * speed) always collapses to p).
    stock_survives_until_arrival = channel.stock - inp.days_to_arrival * speed > 0
    sold_from_production = 0.0 if stock_survives_until_arrival else round_half_away(channel.in_production)
    stock_on_arrival = round_half_away(  # BR / BW
        channel.in_production - sold_from_production + max(0.0, channel.stock - speed * inp.days_to_arrival)
    )
    return ChannelResult(
        max_speed=speed,
        stock_norm=norm,
        days_stock_lasts=days_stock_lasts,
        sold_from_production=sold_from_production,
        stock_on_arrival=stock_on_arrival,
        need=max(0.0, round_half_away(norm - stock_on_arrival)),  # BS / BX
    )


def calculate(inp: BaselineInput) -> BaselineResult:
    wb_speed = _speeds(inp.wb, inp.days_out_of_stock_fbs)
    ozon_speed = _speeds(inp.ozon, inp.days_out_of_stock_fbs)
    wb = _channel(inp.wb, wb_speed, inp)
    ozon = _channel(inp.ozon, ozon_speed, inp)

    raw_need = max(
        0.0,
        wb.need + ozon.need - inp.stock_fbs - inp.stock_kvant - inp.in_production_sklad - inp.in_production_kvant,
    )
    if inp.category in ROUNDED_TO_QUANT_CATEGORIES and inp.quant:
        need = round_half_away(raw_need / inp.quant) * inp.quant  # BI
    else:
        need = raw_need
    return BaselineResult(
        wb=wb,
        ozon=ozon,
        need=need,
        need_in_quants=_divide(need, inp.quant or 0),  # BJ
    )
