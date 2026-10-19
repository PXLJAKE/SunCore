# SPDX-License-Identifier: Apache-2.0
"""Blending of PV forecast sources: weights, minimum weight, manual override, quantiles."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.core.forecast import ACCURACY_MIN_SAMPLES, AccuracyRecord, HorizonBucket
from suncore.core.series import QuantileSeries, SeriesUnit, TimeSeries
from suncore.forecast.pv.blend import (
    MIN_WEIGHT,
    WEIGHT_BUCKETS,
    BlendInput,
    blend,
    blend_slot,
    source_weights,
    weight_bucket,
)

NOW = datetime(2026, 6, 21, 4, 0, tzinfo=UTC)
Q = timedelta(minutes=15)
SLOTS = 48 * 4


def series(
    p50: list[float | None],
    p10: list[float | None] | None = None,
    p90: list[float | None] | None = None,
    *,
    start: datetime = NOW,
    step: timedelta = Q,
) -> QuantileSeries:
    def part(values: list[float | None]) -> TimeSeries:
        return TimeSeries(start=start, step=step, values=tuple(values), unit=SeriesUnit.W)

    return QuantileSeries(p10=part(p10 or p50), p50=part(p50), p90=part(p90 or p50))


def records(source: str, mae: float, n: int = ACCURACY_MIN_SAMPLES) -> list[AccuracyRecord]:
    return [
        AccuracyRecord(source_id=source, horizon=b, mae_w=mae, bias_w=0.0, n=n)
        for b in WEIGHT_BUCKETS
    ]


def flat(level: float) -> QuantileSeries:
    return series([level] * SLOTS)


# --- weights -------------------------------------------------------------------------


def test_inverse_mae_weights_favour_the_better_source_but_keep_the_worse() -> None:
    good = BlendInput("a", flat(1000), records=records("a", 100.0))
    bad = BlendInput("b", flat(1300), records=records("b", 400.0))
    weights = source_weights([good, bad], HorizonBucket.H0_6)
    assert weights.auto_basis == "inverse_mae"
    assert weights.weights["a"] > weights.weights["b"] >= MIN_WEIGHT
    assert sum(weights.weights.values()) == pytest.approx(1.0)
    # 1/100 : 1/400 -> skill 0.8 / 0.2, with the floor: 0.1 + 0.8*0.8, 0.1 + 0.8*0.2
    assert weights.weights["a"] == pytest.approx(0.74)
    assert weights.weights["b"] == pytest.approx(0.26)


def test_equal_weights_until_every_auto_source_has_enough_history() -> None:
    a = BlendInput("a", flat(1000), records=records("a", 100.0))
    b = BlendInput("b", flat(1000), records=records("b", 400.0, n=ACCURACY_MIN_SAMPLES - 1))
    weights = source_weights([a, b], HorizonBucket.H6_24)
    assert weights.auto_basis == "equal_no_history"
    assert weights.weights == {"a": pytest.approx(0.5), "b": pytest.approx(0.5)}


def test_a_hopeless_source_keeps_the_minimum_weight() -> None:
    a = BlendInput("a", flat(1000), records=records("a", 1.0))
    b = BlendInput("b", flat(1000), records=records("b", 1e6))
    weights = source_weights([a, b], HorizonBucket.H24_48)
    assert weights.weights["b"] == pytest.approx(MIN_WEIGHT, rel=1e-3)


def test_manual_weights_are_shares_and_auto_sources_split_the_rest() -> None:
    manual = BlendInput("m", flat(1000), weight_mode="manual", manual_weight=0.6)
    a = BlendInput("a", flat(1000))
    b = BlendInput("b", flat(1000))
    weights = source_weights([manual, a, b], HorizonBucket.H0_6)
    assert weights.manual
    assert weights.weights["m"] == pytest.approx(0.6)
    assert weights.weights["a"] == pytest.approx(0.2)
    assert weights.weights["b"] == pytest.approx(0.2)


def test_manual_weights_alone_are_normalised() -> None:
    a = BlendInput("a", flat(1000), weight_mode="manual", manual_weight=0.3)
    b = BlendInput("b", flat(1000), weight_mode="manual", manual_weight=0.9)
    weights = source_weights([a, b], HorizonBucket.H0_6)
    assert weights.auto_basis is None
    assert weights.weights == {"a": pytest.approx(0.25), "b": pytest.approx(0.75)}


def test_manual_weights_summing_to_one_still_leave_the_auto_floor() -> None:
    manual = BlendInput("m", flat(1000), weight_mode="manual", manual_weight=1.0)
    auto = BlendInput("a", flat(1000))
    weights = source_weights([manual, auto], HorizonBucket.H0_6)
    assert weights.weights["a"] == pytest.approx(MIN_WEIGHT)
    assert weights.weights["m"] == pytest.approx(1 - MIN_WEIGHT)


@st.composite
def inputs(draw: st.DrawFn) -> list[BlendInput]:
    count = draw(st.integers(1, 8))
    out = []
    for i in range(count):
        manual = draw(st.booleans())
        mae = draw(st.one_of(st.none(), st.floats(0.0, 5000.0)))
        n = draw(st.integers(0, 500))
        out.append(
            BlendInput(
                f"s{i}",
                flat(1000),
                weight_mode="manual" if manual else "auto",
                manual_weight=draw(st.floats(0.01, 1.0)) if manual else None,
                records=[] if mae is None else records(f"s{i}", mae, n),
            )
        )
    return out


@given(inputs(), st.sampled_from(WEIGHT_BUCKETS))
def test_weights_sum_to_one_and_auto_sources_keep_the_floor(
    items: list[BlendInput], bucket: HorizonBucket
) -> None:
    weights = source_weights(items, bucket).weights
    assert sum(weights.values()) == pytest.approx(1.0)
    floor = min(MIN_WEIGHT, 0.5 / len(items))
    for item in items:
        assert weights[item.source_id] > 0
        if not item.is_manual:
            assert weights[item.source_id] >= floor - 1e-12


def test_weight_bucket_uses_24_48_beyond_48_hours() -> None:
    assert weight_bucket(timedelta(hours=50)) is HorizonBucket.H24_48
    assert weight_bucket(timedelta(hours=1)) is HorizonBucket.H0_6


# --- quantiles --------------------------------------------------------------------------


quantile_triples = st.tuples(
    st.floats(0.0, 10_000.0), st.floats(0.0, 5_000.0), st.floats(0.0, 5_000.0)
).map(lambda t: (max(0.0, t[0] - t[1]), t[0], t[0] + t[2]))


@given(
    st.lists(
        st.tuples(st.floats(0.01, 1.0), quantile_triples, st.booleans()), min_size=1, max_size=8
    )
)
def test_blended_quantiles_are_ordered(
    rows: list[tuple[float, tuple[float, float, float], bool]],
) -> None:
    values = [(w, lo if q else mid, mid, hi if q else mid) for w, (lo, mid, hi), q in rows]
    result = blend_slot(values)
    assert result is not None
    p10, p50, p90 = result
    assert 0.0 <= p10 <= p50 <= p90
    mids = [v[2] for v in values]
    assert min(mids) - 1e-6 <= p50 <= max(mids) + 1e-6


@settings(max_examples=50)
@given(st.lists(quantile_triples, min_size=1, max_size=24))
def test_a_single_source_passes_through_unchanged(triples: list[tuple[float, float, float]]) -> None:
    source = series([t[1] for t in triples], [t[0] for t in triples], [t[2] for t in triples])
    result = blend([BlendInput("only", source)], NOW, horizon=Q * len(triples))
    assert result is not None
    assert result.method == "single_source"
    assert result.series == source


def test_agreeing_sources_keep_the_band_and_disagreeing_ones_widen_it() -> None:
    agree = blend_slot([(0.5, 800.0, 1000.0, 1100.0), (0.5, 800.0, 1000.0, 1100.0)])
    assert agree == pytest.approx((800.0, 1000.0, 1100.0))
    apart = blend_slot([(0.5, 800.0, 1000.0, 1100.0), (0.5, 1000.0, 1200.0, 1300.0)])
    assert apart is not None
    assert apart[1] == pytest.approx(1100.0)
    assert apart[1] - apart[0] > 200.0
    assert apart[2] - apart[1] > 100.0


def test_sources_without_quantiles_take_the_band_from_those_that_have_them() -> None:
    result = blend_slot([(0.5, 600.0, 1000.0, 1200.0), (0.5, 1000.0, 1000.0, 1000.0)])
    assert result == pytest.approx((600.0, 1000.0, 1200.0))


def test_without_any_quantiles_only_the_disagreement_makes_a_band() -> None:
    result = blend_slot([(0.5, 1000.0, 1000.0, 1000.0), (0.5, 1000.0, 1000.0, 1000.0)])
    assert result == pytest.approx((1000.0, 1000.0, 1000.0))


def test_weights_are_renormalised_over_the_sources_present_in_a_slot() -> None:
    a = BlendInput("a", series([1000.0] * SLOTS), records=records("a", 100.0))
    b_values: list[float | None] = [None] * (SLOTS // 2) + [2000.0] * (SLOTS // 2)
    b = BlendInput("b", series(b_values), records=records("b", 100.0))
    result = blend([a, b], NOW)
    assert result is not None
    assert result.series.p50.values[0] == pytest.approx(1000.0)
    assert result.series.p50.values[-1] == pytest.approx(1500.0)


def test_the_blend_lies_on_the_planning_grid_for_48_hours() -> None:
    half_hourly = series([500.0] * 96, step=timedelta(minutes=30), start=NOW - timedelta(hours=1))
    now = NOW + timedelta(minutes=7)
    result = blend([BlendInput("a", half_hourly), BlendInput("b", flat(700))], now)
    assert result is not None
    assert result.series.step == Q
    assert result.series.start == NOW
    assert len(result.series) == SLOTS + 1
    assert result.series.p50.values[0] == pytest.approx(600.0)


def test_a_source_biased_30_percent_gets_the_lower_weight() -> None:
    truth = 1000.0
    unbiased = BlendInput("a", flat(truth), records=records("a", 60.0))
    biased = BlendInput("b", flat(truth * 1.3), records=records("b", 300.0))
    result = blend([unbiased, biased], NOW)
    assert result is not None
    first = result.weights[0].weights
    assert first["a"] > 3 * first["b"]
    blended = result.series.p50.values[0]
    assert blended is not None
    assert abs(blended - truth) < abs(truth * 1.3 - truth) / 3
