"""Evaluating demand forecasts against what was really sold, on a held-out stretch of time.

A forecast says how many pieces an article will sell on a marketplace in the next `horizon` days.
The analyst's speed (`max_speed`, see `domain.baseline`) turned into units is the baseline; any
model has to beat it on days it has not seen. Pure functions on plain values.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Iterable, Optional

CHANNELS = ("wb", "ozon")
HORIZONS = (7, 30)


@dataclass(frozen=True)
class DemandSample:
    """One article on one marketplace as seen on `as_of`, with what happened afterwards."""

    as_of: date
    article: str
    channel: str  # 'wb' | 'ozon'
    sales_7d: float
    sales_30d: float
    sales_prev_30d: float
    max_speed: float  # the analyst's speed, pieces a day
    actual: float  # pieces ordered from `as_of` over the horizon


@dataclass(frozen=True)
class Metrics:
    samples: int
    actual_total: float
    predicted_total: float
    wape: Optional[float]  # sum |actual - predicted| / sum actual; None when nothing was sold
    bias: Optional[float]  # (sum predicted - sum actual) / sum actual; > 0 means over-forecast


Forecaster = Callable[[DemandSample, int], float]

FORECASTERS: dict[str, Forecaster] = {
    "analyst_speed": lambda s, horizon: s.max_speed * horizon,
    "last_30_days": lambda s, horizon: s.sales_30d / 30 * horizon,
    "last_7_days": lambda s, horizon: s.sales_7d / 7 * horizon,
}


def metrics(actual: Iterable[float], predicted: Iterable[float]) -> Metrics:
    pairs = list(zip(actual, predicted))
    actual_total = sum(a for a, _ in pairs)
    predicted_total = sum(p for _, p in pairs)
    if actual_total <= 0:
        return Metrics(len(pairs), actual_total, predicted_total, None, None)
    return Metrics(
        samples=len(pairs),
        actual_total=actual_total,
        predicted_total=predicted_total,
        wape=sum(abs(a - p) for a, p in pairs) / actual_total,
        bias=(predicted_total - actual_total) / actual_total,
    )


def split_by_time(
    samples: Iterable[DemandSample], test_from: date, horizon: int
) -> tuple[list[DemandSample], list[DemandSample]]:
    """Train before `test_from`, test from it on, never shuffled. A training sample's outcome window
    has to be over before the test period starts, otherwise the model would learn from the future."""
    train, test = [], []
    last_train_day = test_from - timedelta(days=horizon)
    for sample in samples:
        if sample.as_of >= test_from:
            test.append(sample)
        elif sample.as_of <= last_train_day:
            train.append(sample)
    return train, test


def evaluate(
    samples: Iterable[DemandSample], horizon: int, forecasters: Optional[dict[str, Forecaster]] = None
) -> dict[str, dict[str, Metrics]]:
    """forecaster name -> channel ('wb' | 'ozon' | 'all') -> metrics."""
    items = list(samples)
    report: dict[str, dict[str, Metrics]] = {}
    for name, forecast in (forecasters or FORECASTERS).items():
        by_channel: dict[str, Metrics] = {}
        for channel in (*CHANNELS, "all"):
            chosen = [s for s in items if channel == "all" or s.channel == channel]
            by_channel[channel] = metrics((s.actual for s in chosen), (forecast(s, horizon) for s in chosen))
        report[name] = by_channel
    return report
