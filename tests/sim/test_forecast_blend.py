# SPDX-License-Identifier: Apache-2.0
"""The demo's three biased PV sources, blended by their measured skill (WP5 part B).

Solcast-like (unbiased, 6 % noise), Open-Meteo-like (6 % low, 8 % noise) and Forecast.Solar-like
(4 % high, 7 % noise) forecasts of the simulator are issued at every local midnight for that day.
Fourteen days score them against the simulator's truth (clear sky x cloud factor); the fifteenth
day is blended with those scores. Deterministic: fixed forecast seed and cloud seed.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from suncore.core.forecast import AccuracyRecord
from suncore.core.series import QuantileSeries, SeriesUnit, TimeSeries
from suncore.forecast.accuracy import quarter_errors
from suncore.forecast.pv.blend import WEIGHT_BUCKETS, BlendInput, blend
from suncore.sim.forecasts import FORECAST_SEED, SimForecastModel
from suncore.sim.house import clear_sky_w
from suncore.sim.scenario import load_scenario

pytestmark = pytest.mark.simulator

SCENARIO = load_scenario("summer_day")
FIRST = SCENARIO.start.replace(hour=0, minute=0, second=0, microsecond=0)
Q = timedelta(minutes=15)
CLOUD_SEED = 4711
SOURCES = ("solcast", "open_meteo", "forecast_solar")


def cloud_at(moment: datetime) -> float:
    """A cloud factor per local day (0.45-1.0), the same for every call."""
    return random.Random(f"{CLOUD_SEED}|{moment.date().isoformat()}").uniform(0.45, 1.0)


MODEL = SimForecastModel(SCENARIO, cloud_at, seed=FORECAST_SEED)


def series(start: datetime, step: timedelta, values: list[float]) -> TimeSeries:
    return TimeSeries(start=start, step=step, values=tuple(values), unit=SeriesUnit.W)


def truth(midnight: datetime) -> TimeSeries:
    values = [
        clear_sky_w(midnight + Q * i + Q / 2, SCENARIO.house.pv) * cloud_at(midnight + Q * i)
        for i in range(96)
    ]
    return series(midnight, Q, values)


def issued(source: str, midnight: datetime) -> TimeSeries:
    """What ``source`` said for the day at its midnight, in W on the source's own grid."""
    if source == "solcast":
        slots = MODEL.solcast_day(midnight, midnight)
        return series(midnight, timedelta(minutes=30), [s["pv_estimate"] * 1000 for s in slots])
    if source == "open_meteo":
        wh = MODEL.open_meteo_day(midnight, midnight)
        return series(midnight, Q, [v * 4 for v in wh.values()])
    hours = MODEL.forecast_solar_wh_hours(midnight, 24)
    values = [hours.get((midnight + timedelta(hours=h)).isoformat(), 0.0) for h in range(24)]
    return series(midnight, timedelta(hours=1), values)


def mae(forecast: TimeSeries, actual: TimeSeries) -> float:
    errors = quarter_errors(forecast, actual)
    return sum(abs(e) for e in errors) / len(errors)


def test_the_blend_beats_the_worst_source_and_weights_follow_the_skill() -> None:
    history = [FIRST + timedelta(days=d) for d in range(14)]
    scores = {
        source: sum(mae(issued(source, day), truth(day)) for day in history) / len(history)
        for source in SOURCES
    }
    inputs = [
        BlendInput(
            source,
            QuantileSeries.deterministic(issued(source, FIRST + timedelta(days=14))),
            records=[
                AccuracyRecord(source_id=source, horizon=b, mae_w=scores[source], bias_w=0, n=1344)
                for b in WEIGHT_BUCKETS
            ],
        )
        for source in SOURCES
    ]
    target = FIRST + timedelta(days=14)
    result = blend(inputs, target, horizon=timedelta(hours=24))
    assert result is not None
    actual = truth(target)
    blended = mae(result.series.p50, actual)
    single = {i.source_id: mae(i.series.p50, actual) for i in inputs}
    assert blended < max(single.values())
    assert blended <= sorted(single.values())[1]  # at least as good as the middle source
    weights = result.weights[0].weights
    best = min(scores, key=scores.__getitem__)
    worst = max(scores, key=scores.__getitem__)
    assert weights[best] > weights[worst]
    assert sum(weights.values()) == pytest.approx(1.0)
