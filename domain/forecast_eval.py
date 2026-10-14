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


# Sales of the last 30 days on the channel, known on the planning date, decide the group. Article-level
# errors are huge for the long tail and small for the few articles that sell the bulk, and one number
# for all of them hides both.
VOLUME_BUCKETS = (
    ("0", 0, 0),
    ("1-2", 1, 2),
    ("3-9", 3, 9),
    ("10-29", 10, 29),
    ("30-99", 30, 99),
    ("100+", 100, None),
)


def volume_bucket(sales_30d: float) -> str:
    units = int(round(sales_30d))
    for name, low, high in VOLUME_BUCKETS:
        if units >= low and (high is None or units <= high):
            return name
    return VOLUME_BUCKETS[0][0]


def evaluate_by_volume(
    samples: Iterable[DemandSample], horizon: int, forecasters: Optional[dict[str, Forecaster]] = None
) -> dict[str, dict[str, Metrics]]:
    """forecaster name -> volume group -> metrics (only the groups that have samples), in the group order."""
    items = list(samples)
    report: dict[str, dict[str, Metrics]] = {}
    for name, forecast in (forecasters or FORECASTERS).items():
        by_bucket: dict[str, Metrics] = {}
        for bucket, _, _ in VOLUME_BUCKETS:
            chosen = [s for s in items if volume_bucket(s.sales_30d) == bucket]
            if chosen:
                by_bucket[bucket] = metrics((s.actual for s in chosen), (forecast(s, horizon) for s in chosen))
        report[name] = by_bucket
    return report
