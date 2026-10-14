from datetime import date

import pytest

from domain.forecast_eval import DemandSample, evaluate, evaluate_by_volume, metrics, split_by_time, volume_bucket


def sample(day=date(2026, 9, 1), channel="wb", s7=7.0, s30=60.0, speed=2.0, actual=60.0, article="A"):
    return DemandSample(day, article, channel, s7, s30, 0.0, speed, actual)


def test_wape_and_bias_are_relative_to_what_was_sold():
    result = metrics([10, 30], [20, 20])  # off by 10 and 10 of 40 sold; forecast total equals actual
    assert result.wape == pytest.approx(0.5) and result.bias == pytest.approx(0.0)
    assert metrics([10], [15]).bias == pytest.approx(0.5)  # over-forecast


def test_nothing_sold_gives_no_ratio_instead_of_dividing_by_zero():
    result = metrics([0, 0], [3, 0])
    assert result.wape is None and result.bias is None and result.predicted_total == 3


def test_the_split_keeps_time_order_and_leaves_a_gap_of_the_horizon():
    samples = [sample(date(2026, 8, d)) for d in (1, 5, 10, 14, 20)]
    train, test = split_by_time(samples, test_from=date(2026, 8, 15), horizon=7)
    assert [s.as_of.day for s in train] == [1, 5]   # 10 + 7 days reaches into the test period
    assert [s.as_of.day for s in test] == [20]       # 14 is in neither: outcome overlaps the border
    assert all(t.as_of >= date(2026, 8, 15) for t in test)


def test_evaluate_compares_forecasters_per_channel():
    samples = [
        sample(channel="wb", speed=3.0, s30=60.0, actual=90.0),    # analyst 90 (exact), last 30 days: 60
        sample(channel="ozon", speed=1.0, s30=30.0, actual=30.0),  # analyst 30 (exact), last 30 days: 30
    ]
    report = evaluate(samples, horizon=30)
    assert report["analyst_speed"]["all"].wape == pytest.approx(0.0)
    assert report["last_30_days"]["wb"].wape == pytest.approx(30 / 90)
    assert report["last_30_days"]["ozon"].wape == pytest.approx(0.0)
    assert report["last_30_days"]["wb"].bias < 0  # it under-forecasts a growing article


def test_volume_buckets_cover_every_count_without_gaps():
    assert [volume_bucket(v) for v in (0, 0.4, 1, 2, 3, 9, 10, 29, 30, 99, 100, 5000)] == [
        "0", "0", "1-2", "1-2", "3-9", "3-9", "10-29", "10-29", "30-99", "30-99", "100+", "100+",
    ]


def test_the_evaluation_by_volume_separates_the_long_tail_from_the_leaders():
    samples = [
        sample(s30=0.0, s7=0.0, speed=0.0, actual=3.0),       # sold nothing before, then sold: missed
        sample(s30=300.0, s7=70.0, speed=10.0, actual=300.0),  # a leader, forecast right
    ]
    report = evaluate_by_volume(samples, horizon=30)["analyst_speed"]
    assert list(report) == ["0", "100+"]
    assert report["0"].wape == pytest.approx(1.0) and report["100+"].wape == pytest.approx(0.0)
