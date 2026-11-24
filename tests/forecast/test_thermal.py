# SPDX-License-Identifier: Apache-2.0
"""The RC learner of PR 6-K: recovery of known parameters, quality, outliers, persistence."""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.core.devices import ComfortBand, DeviceConfig, DeviceKind, ThermalSpec
from suncore.core.measurements import (
    WEATHER_TEMPERATURE_C,
    device_power_key,
    device_temperature_key,
)
from suncore.core.series import SeriesUnit, TimeRange, TimeSeries
from suncore.core.thermal import GENERIC_AIR_WATER_COP, ThermalModel, model_usable
from suncore.forecast.thermal import (
    NS_THERMAL_MODEL,
    Rows,
    ThermalLearner,
    fit_rc,
    learn,
    load_models,
    replay_nrmse,
    with_learned,
)
from tests.safety.test_heatpump import sg_device

T0 = datetime(2026, 1, 1, tzinfo=UTC)
TRUE = ThermalModel(capacity_wh_per_k=350.0, loss_w_per_k=6.0, internal_gain_w=-150.0)
"""A 300 l hot-water tank: ~1.16 kWh/K incl. some structure, losses 6 W/K, draws -150 W."""


def simulate(
    model: ThermalModel = TRUE,
    *,
    days: int = 20,
    nominal_w: float = 1200.0,
    on_c: float = 45.0,
    off_c: float = 52.0,
    noise_k: float = 0.0,
    seed: int = 1,
) -> Rows:
    """A thermostat-driven heat pump, integrated per minute, reported as 15-minute means."""
    rng = random.Random(seed)
    temp, heating = 48.0, False
    t_rows: list[float | None] = []
    p_rows: list[float | None] = []
    o_rows: list[float | None] = []
    for q in range(days * 96):
        temps, powers, outs = [], [], []
        for m in range(15):
            minute = q * 15 + m
            outdoor = 2.0 + 6.0 * math.cos(2 * math.pi * (minute / 60 - 15) / 24)
            if temp <= on_c:
                heating = True
            elif temp >= off_c:
                heating = False
            power = nominal_w if heating else 0.0
            temp = model.step(temp, outdoor, power * GENERIC_AIR_WATER_COP.cop_at(outdoor), 60)
            temps.append(temp)
            powers.append(power)
            outs.append(outdoor)
        t_rows.append(sum(temps) / 15 + rng.gauss(0.0, noise_k))
        p_rows.append(sum(powers) / 15)
        o_rows.append(sum(outs) / 15)
    return Rows(tuple(t_rows), tuple(p_rows), tuple(o_rows))


def test_recovers_known_parameters() -> None:
    rows = simulate()
    model = fit_rc(rows)
    assert model is not None
    assert model.capacity_wh_per_k == pytest.approx(TRUE.capacity_wh_per_k, rel=0.15)
    assert model.loss_w_per_k == pytest.approx(TRUE.loss_w_per_k, rel=0.3)
    fit = learn(rows, now=T0)
    assert fit is not None
    assert fit.history_days >= 14
    assert fit.nrmse <= 0.2
    assert model_usable(fit)


def test_too_little_history_is_not_usable() -> None:
    fit = learn(simulate(days=5), now=T0)
    assert fit is not None
    assert not model_usable(fit)  # fewer than 14 days


def test_outliers_do_not_spoil_the_fit() -> None:
    rows = simulate()
    temps = list(rows.temp_c)
    rng = random.Random(7)
    for i in rng.sample(range(len(temps)), 40):  # hot-water draws: sudden drops
        value = temps[i]
        assert value is not None
        temps[i] = value - 8.0
    model = fit_rc(Rows(tuple(temps), rows.power_w, rows.outdoor_c))
    assert model is not None
    assert model.capacity_wh_per_k == pytest.approx(TRUE.capacity_wh_per_k, rel=0.25)


def test_noise_only_gives_no_good_model() -> None:
    rng = random.Random(3)
    n = 20 * 96
    rows = Rows(
        tuple(50.0 + rng.gauss(0, 3) for _ in range(n)),
        tuple(rng.choice([0.0, 1200.0]) for _ in range(n)),
        tuple(rng.uniform(-5, 10) for _ in range(n)),
    )
    fit = learn(rows, now=T0)
    assert fit is None or not model_usable(fit)


def test_gaps_and_short_series() -> None:
    assert fit_rc(Rows((None,) * 500, (0.0,) * 500, (5.0,) * 500)) is None
    rows = simulate(days=3)
    assert (
        replay_nrmse(TRUE, Rows(rows.temp_c[:50], rows.power_w[:50], rows.outdoor_c[:50])) is None
    )
    with pytest.raises(ValueError, match="one value per slot"):
        Rows((1.0,), (), ())


@settings(max_examples=25, deadline=None)
@given(
    capacity=st.floats(min_value=150, max_value=2000),
    loss=st.floats(min_value=1, max_value=30),
    seed=st.integers(min_value=0, max_value=1000),
)
def test_property_fit_is_physical_and_replays_its_own_data(
    capacity: float, loss: float, seed: int
) -> None:
    true = ThermalModel(capacity_wh_per_k=capacity, loss_w_per_k=loss)
    rows = simulate(true, days=15, seed=seed, nominal_w=max(800.0, loss * 60))
    fit = learn(rows, now=T0)
    if fit is None:
        return  # not identifiable (e.g. the thermostat never switched): no model, no harm
    assert fit.model.capacity_wh_per_k > 0
    assert fit.model.loss_w_per_k >= 0
    assert math.isfinite(fit.nrmse)


# --- the service ----------------------------------------------------------------------------


@dataclass
class FakeData:
    series: dict[str, tuple[float | None, ...]]

    async def read_series(
        self,
        key: str,
        window: TimeRange,
        resolution: Literal["1m", "15m"] = "15m",
        unit: SeriesUnit | None = None,
        *,
        include_recent: bool = True,
    ) -> TimeSeries:
        del resolution, include_recent
        n = int((window.end - window.start) / timedelta(minutes=15))
        values = self.series.get(key, ())[-n:]
        values = (None,) * (n - len(values)) + values
        return TimeSeries(
            start=window.start,
            step=timedelta(minutes=15),
            values=values,
            unit=unit or SeriesUnit.W,
        )


@dataclass
class Entry:
    key: str
    payload: dict[str, Any]
    schema_version: int = 1


class FakeLearned:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], Entry] = {}

    async def put(
        self, namespace: str, key: str, payload: Mapping[str, Any], *, schema_version: int = 1
    ) -> None:
        self.rows[(namespace, key)] = Entry(key, dict(payload), schema_version)

    async def in_namespace(self, namespace: str) -> Sequence[Entry]:
        return [e for (ns, _), e in self.rows.items() if ns == namespace]


def thermal_device() -> DeviceConfig:
    return sg_device().model_copy(
        update={"thermal": ThermalSpec(), "comfort": ComfortBand(min_c=45, max_c=55)}
    )


async def test_learner_fits_stores_and_reloads() -> None:
    rows = simulate()
    data = FakeData(
        {
            device_temperature_key("hp"): rows.temp_c,
            device_power_key("hp"): rows.power_w,
            WEATHER_TEMPERATURE_C: rows.outdoor_c,
        }
    )
    learned = FakeLearned()
    now = T0 + timedelta(days=20)
    learner = ThermalLearner(data, learned, clock=lambda: now)
    assert learner.due(now)
    fits = await learner.refit([thermal_device(), sg_device()])
    assert set(fits) == {"hp"}  # sg_device() without a thermal spec is not learned
    assert not learner.due(now)
    assert (NS_THERMAL_MODEL, "hp") in learned.rows
    reloaded = await load_models(learned)
    assert reloaded["hp"] == fits["hp"]
    planned = with_learned([thermal_device()], reloaded)
    assert planned[0].thermal is not None
    assert planned[0].thermal.learned == fits["hp"]
    assert planned[0].kind is DeviceKind.HEAT_PUMP


def test_outdoor_forecast_from_the_weather_points() -> None:
    from suncore.forecast.load_service import LoadForecastService  # noqa: PLC0415

    service = LoadForecastService(None, None, clock=lambda: T0)
    assert service.outdoor_forecast(T0) is None
    hour = timedelta(hours=1)
    service._weather_points = [  # the hourly forecast as the refresh stores it
        (T0 - hour, 1.0),
        (T0, 2.0),
        (T0 + 2 * hour, 4.0),
    ]
    series = service.outdoor_forecast(T0 + timedelta(minutes=30))
    assert series is not None
    assert series.start == T0
    assert series.values == (2.0, None, 4.0)
    assert series.unit is SeriesUnit.CELSIUS
