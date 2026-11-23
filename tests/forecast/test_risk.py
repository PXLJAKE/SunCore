# SPDX-License-Identifier: Apache-2.0
"""Risk model: windows, net energy quantiles, morning buffer and evening target (WP7)."""

from __future__ import annotations

import itertools
import math
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.core.battery import (
    BatteryModel,
    BatterySettings,
    BatteryStrategy,
    RiskLevel,
    effective_reserve_pct,
)
from suncore.core.series import QuantileSeries, SeriesUnit, TimeSeries, planning_grid
from suncore.core.site import BatteryConfig, EntityRef
from suncore.forecast.risk import (
    EnergyQuantiles,
    ErrorModel,
    ErrorSample,
    NetWindow,
    RiskWindow,
    coverage,
    day_windows,
    daylight_spans,
    evening_target_pct,
    measured_energy,
    merge_samples,
    morning_buffer_pct,
    net_energy_quantile,
    samples_view,
    source_net_quantile,
    window_energy,
)

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 6, 22, 8, 0, tzinfo=BERLIN).astimezone(UTC)  # Monday 08:00 local
BATTERY = BatteryModel(capacity_wh=10_000, max_charge_w=5_000, max_discharge_w=5_000)
LEVELS = (RiskLevel.CAUTIOUS, RiskLevel.BALANCED, RiskLevel.BOLD)


def bell(moment: datetime, peak_w: float) -> float:
    """PV of a horizontal plant: 06:00-21:00 local, a sine bell."""
    t = moment.astimezone(BERLIN)
    hour = t.hour + t.minute / 60 + 0.125
    if not 6 <= hour < 21:
        return 0.0
    return peak_w * math.sin(math.pi * (hour - 6) / 15)


def pv_forecast(
    peaks: tuple[float, ...] = (6000.0, 6000.0, 6000.0),
    low: float = 0.85,
    high: float = 1.1,
    now: datetime = NOW,
) -> QuantileSeries:
    grid = planning_grid(now, timedelta(hours=48))
    first = now.astimezone(BERLIN).date()
    mids = [bell(t, peaks[(t.astimezone(BERLIN).date() - first).days]) for t in grid.starts()]

    def series(values: list[float]) -> TimeSeries:
        return TimeSeries(start=grid.start, step=grid.step, values=tuple(values), unit=SeriesUnit.W)

    return QuantileSeries(
        p10=series([v * low for v in mids]),
        p50=series(mids),
        p90=series([v * high for v in mids]),
    )


def load_forecast(level: float = 400.0, now: datetime = NOW, high: float = 1.3) -> QuantileSeries:
    grid = planning_grid(now, timedelta(hours=48))

    def series(value: float) -> TimeSeries:
        return TimeSeries(
            start=grid.start, step=grid.step, values=(value,) * grid.count, unit=SeriesUnit.W
        )

    return QuantileSeries(p10=series(level * 0.8), p50=series(level), p90=series(level * high))


def targets(
    pv: QuantileSeries,
    risk: RiskLevel = RiskLevel.CAUTIOUS,
    *,
    model: ErrorModel | None = None,
    user: float | None = None,
    manual: float | None = None,
    strategy: BatteryStrategy = BatteryStrategy.SELF_CONSUMPTION,
    battery: BatteryModel = BATTERY,
    load: QuantileSeries | None = None,
    now: datetime = NOW,
    site: BatteryConfig | None = None,
) -> tuple[float, float, str, str]:
    windows = day_windows(now, pv, load or load_forecast(now=now), BERLIN)
    config = BatterySettings(risk=risk, morning_buffer_pct=manual, strategy=strategy)
    evening = evening_target_pct(
        battery, config, windows, user_target_pct=user, site_battery=site, model=model
    )
    buffer = morning_buffer_pct(
        battery, config, windows, evening.soc_pct, site_battery=site, model=model
    )
    return (
        buffer.soc_pct,
        evening.soc_pct,
        str(buffer.reason.args["cause"]),
        str(evening.reason.args["cause"]),
    )


# --- windows ---------------------------------------------------------------------------------


def test_windows_are_anchored_on_the_next_sunset() -> None:
    windows = day_windows(NOW, pv_forecast(), load_forecast(), BERLIN)
    rest, night = windows[RiskWindow.REST_OF_TODAY], windows[RiskWindow.NIGHT]
    nxt, bridge = windows[RiskWindow.NEXT_DAY], windows[RiskWindow.BRIDGE]
    assert rest.start == NOW
    assert windows[RiskWindow.REST_OF_NIGHT].start == windows[RiskWindow.REST_OF_NIGHT].end
    assert rest.end == night.start == bridge.start
    both = windows[RiskWindow.DAY_AND_NIGHT]
    assert (both.start, both.end) == (rest.start, night.end)
    assert night.end == nxt.start
    assert nxt.end == bridge.end
    assert rest.end.astimezone(BERLIN).hour in (20, 21)
    assert nxt.start.astimezone(BERLIN).date() == date(2026, 6, 23)
    assert night.weather == "night"
    assert night.pv.p50 == 0
    assert night.load.p50 == pytest.approx(400 * (night.end - night.start) / timedelta(hours=1))
    assert bridge.load.p50 == pytest.approx(night.load.p50 + nxt.load.p50)


def test_after_sunset_the_night_starts_now() -> None:
    late = datetime(2026, 6, 22, 23, 0, tzinfo=BERLIN).astimezone(UTC)
    pv = pv_forecast(now=late)
    windows = day_windows(late, pv, load_forecast(now=late), BERLIN)
    assert windows[RiskWindow.REST_OF_TODAY].start == windows[RiskWindow.REST_OF_TODAY].end
    assert windows[RiskWindow.NIGHT].start == late
    both, nxt = windows[RiskWindow.DAY_AND_NIGHT], windows[RiskWindow.NEXT_DAY]
    assert both.start == nxt.start  # the next daytime and the night after it
    assert both.end > nxt.end


def test_before_sunrise_the_rest_of_the_night_is_its_own_window() -> None:
    early = datetime(2026, 6, 22, 3, 0, tzinfo=BERLIN).astimezone(UTC)
    windows = day_windows(early, pv_forecast(now=early), load_forecast(now=early), BERLIN)
    rest_night, rest = windows[RiskWindow.REST_OF_NIGHT], windows[RiskWindow.REST_OF_TODAY]
    assert rest_night.start == early
    assert rest_night.end == rest.start
    assert rest.start.astimezone(BERLIN).hour == 6  # today's sunrise, not 03:00
    assert rest_night.weather == "night"
    assert rest_night.pv.p50 == 0
    assert windows[RiskWindow.DAY_AND_NIGHT].start == early


def test_without_pv_the_fallback_times_apply() -> None:
    windows = day_windows(NOW, None, load_forecast(), BERLIN)
    assert windows[RiskWindow.REST_OF_TODAY].end.astimezone(BERLIN).hour == 19
    assert windows[RiskWindow.NEXT_DAY].start.astimezone(BERLIN).hour == 7
    assert windows[RiskWindow.NEXT_DAY].weather == "overcast"
    assert daylight_spans(None, BERLIN) == {}


def test_window_energy_counts_partial_slots() -> None:
    load = load_forecast()
    start = NOW + timedelta(minutes=5)
    energy = window_energy(load, start, start + timedelta(hours=1))
    assert energy.p50 == pytest.approx(400)
    assert window_energy(None, NOW, NOW + timedelta(hours=1)) == EnergyQuantiles.zero()


# --- distribution ----------------------------------------------------------------------------


def window(pv: tuple[float, float, float], load: tuple[float, float, float]) -> NetWindow:
    return NetWindow(
        kind=RiskWindow.NEXT_DAY,
        start=NOW,
        end=NOW + timedelta(hours=10),
        pv=EnergyQuantiles(*pv),
        load=EnergyQuantiles(*load),
        weather="mixed",
    )


def test_source_quantiles_formula() -> None:
    w = window((6000, 10000, 12000), (4000, 5000, 7000))
    assert source_net_quantile(w, 0.5) == 5000
    z90 = 1.2815515655446004
    sigma = math.hypot(4000 / z90, 2000 / z90)  # PV shortfall and load excess
    assert source_net_quantile(w, 0.1) == pytest.approx(5000 - z90 * sigma)
    assert source_net_quantile(w, 0.9) > 5000
    with pytest.raises(ValueError, match="inside"):
        source_net_quantile(w, 1.0)
    result = net_energy_quantile(w, RiskLevel.CAUTIOUS)
    assert result.source == "source_quantiles"
    assert result.samples == 0


def samples(
    n_days: int, rel: float, weather: str = "mixed", start: date = date(2026, 5, 1)
) -> list[ErrorSample]:
    return [
        ErrorSample(
            day=start + timedelta(days=i),
            window=RiskWindow.NEXT_DAY,
            weather=weather,  # type: ignore[arg-type]
            error_wh=rel * 10000 + (i % 5 - 2) * 100,
            scale_wh=10000,
        )
        for i in range(n_days)
    ]


def test_learned_distribution_after_min_history_days() -> None:
    w = window((8000, 10000, 11000), (4000, 5000, 6000))
    young = ErrorModel(tuple(samples(29, -0.3)))
    assert net_energy_quantile(w, RiskLevel.CAUTIOUS, young).source == "source_quantiles"
    old = ErrorModel(tuple(samples(30, -0.3)))
    learned = net_energy_quantile(w, RiskLevel.CAUTIOUS, old)
    assert learned.source == "learned"
    assert not learned.pooled
    assert learned.value_wh == pytest.approx(5000 - 0.32 * 15000, abs=200)


def test_a_thin_class_falls_back_to_the_window_pool() -> None:
    w = window((8000, 10000, 11000), (4000, 5000, 6000))
    model = ErrorModel(
        tuple(samples(25, -0.1, "clear") + samples(5, -0.5, "mixed", date(2026, 7, 1)))
    )
    result = net_energy_quantile(w, RiskLevel.BOLD, model)
    assert result.source == "learned"
    assert result.pooled
    assert result.samples == 30


def test_samples_round_trip_and_merge() -> None:
    a = samples(3, 0.1)
    assert [ErrorSample.from_json(s.to_json()) for s in a] == a
    with pytest.raises(ValueError, match="weather"):
        ErrorSample.from_json({**a[0].to_json(), "weather": "fog"})
    newer = ErrorSample(a[0].day, a[0].window, "clear", 1.0, 2.0)
    merged = merge_samples(a, [newer], keep_days=1, today=a[0].day + timedelta(days=2))
    assert [s.day for s in merged] == [a[1].day, a[2].day]
    merged = merge_samples(a, [newer], keep_days=10, today=a[0].day)
    assert merged[0] == newer
    assert samples_view(a) == {"next_day/mixed": 3}


def test_measured_energy_and_coverage() -> None:
    series = TimeSeries(
        start=NOW, step=timedelta(minutes=15), values=(400.0, None, 400.0, 400.0), unit=SeriesUnit.W
    )
    end = NOW + timedelta(hours=1)
    assert coverage(series, NOW, end) == 0.75
    assert measured_energy(series, NOW, end) == pytest.approx(400)
    assert coverage(series, end, end + timedelta(hours=1)) == 0.0
    assert coverage(series, NOW, NOW + timedelta(hours=2)) == 3 / 8  # beyond the series: unknown


# --- properties ----------------------------------------------------------------------------------

peaks = st.floats(0, 9000)
spread = st.floats(0.3, 0.99)
"""A band (P10 below P50): a source without any band uses the default band (a step)."""


@settings(max_examples=40, deadline=None)
@given(peaks, peaks, spread, st.floats(100, 1500), st.one_of(st.none(), st.floats(0, 100)))
def test_monotone_in_the_risk_level(
    today: float, tomorrow: float, low: float, load: float, user: float | None
) -> None:
    pv = pv_forecast((today, tomorrow, tomorrow), low=low)
    results = [targets(pv, level, user=user, load=load_forecast(load)) for level in LEVELS]
    for cautious, bold in itertools.pairwise(results):
        assert cautious[0] >= bold[0] - 1e-9  # buffer
        assert cautious[1] >= bold[1] - 1e-9  # evening target


@settings(max_examples=40, deadline=None)
@given(peaks, peaks, spread, st.floats(0.0, 0.5), st.sampled_from(LEVELS))
def test_monotone_in_the_uncertainty(
    today: float, tomorrow: float, low: float, wider: float, level: RiskLevel
) -> None:
    narrow = targets(pv_forecast((today, tomorrow, tomorrow), low=low), level)
    wide = targets(pv_forecast((today, tomorrow, tomorrow), low=max(0.0, low - wider)), level)
    assert wide[0] >= narrow[0] - 1e-9
    assert wide[1] >= narrow[1] - 1e-9


@settings(max_examples=60, deadline=None)
@given(
    peaks,
    peaks,
    st.sampled_from(LEVELS),
    st.one_of(st.none(), st.floats(0, 100)),
    st.one_of(st.none(), st.floats(0, 100)),
    st.floats(60, 100),
    st.sampled_from(list(BatteryStrategy)),
)
def test_limits_always_win(  # noqa: PLR0917 - hypothesis passes the drawn values positionally
    today: float,
    tomorrow: float,
    level: RiskLevel,
    user: float | None,
    manual: float | None,
    max_soc: float,
    strategy: BatteryStrategy,
) -> None:
    battery = BATTERY.model_copy(update={"max_soc_pct": max_soc})
    buffer, evening, _, _ = targets(
        pv_forecast((today, tomorrow, tomorrow)),
        level,
        user=user,
        manual=manual,
        strategy=strategy,
        battery=battery,
    )
    for value in (buffer, evening):
        assert battery.min_soc_pct - 1e-9 <= value <= max_soc + 1e-9
    assert buffer >= min(max_soc, manual or 0) - 1e-9
    assert evening >= min(max_soc, user or 0) - 1e-9


def test_prefer_full_means_max_soc() -> None:
    battery = BATTERY.model_copy(update={"max_soc_pct": 90})
    buffer, evening, cause, evening_cause = targets(
        pv_forecast(), strategy=BatteryStrategy.PREFER_FULL, battery=battery
    )
    assert (buffer, evening, cause, evening_cause) == (90, 90, "prefer_full", "prefer_full")


def test_reasons_carry_the_value_and_the_source() -> None:
    windows = day_windows(NOW, pv_forecast(), load_forecast(), BERLIN)
    config = BatterySettings()
    evening = evening_target_pct(BATTERY, config, windows)
    assert evening.reason.key == "planner.battery.evening_target"
    assert evening.reason.args["soc_pct"] == round(evening.soc_pct, 1)
    assert evening.reason.args["source"] == "source_quantiles"
    assert evening.reason.args["risk"] == "cautious"
    buffer = morning_buffer_pct(BATTERY, config, windows, evening.soc_pct)
    assert buffer.reason.key == "planner.battery.morning_buffer"


# --- scenarios ----------------------------------------------------------------------------------


def test_scenario_sunny_day_gives_a_low_buffer() -> None:
    buffer, _, cause, _ = targets(pv_forecast((7000, 7000, 7000), low=0.9))
    assert buffer == BATTERY.min_soc_pct
    assert cause == "forecast_good"


def test_scenario_mixed_clouds_give_a_higher_buffer() -> None:
    sunny = targets(pv_forecast((7000, 7000, 7000), low=0.9))[0]
    mixed, _, cause, _ = targets(pv_forecast((2500, 2500, 2500), low=0.4))
    assert mixed > sunny
    assert cause in ("forecast_uncertain", "forecast_poor")


def test_scenario_two_poor_days_raise_the_evening_target_to_max() -> None:
    good = targets(pv_forecast((7000, 7000, 7000)))[1]
    _, poor, _, cause = targets(pv_forecast((800, 600, 600), low=0.5), load=load_forecast(600))
    assert poor == BATTERY.max_soc_pct
    assert cause == "max_soc"
    assert good < poor
    without_bridge = evening_target_pct(
        BATTERY,
        BatterySettings(bridge_next_day=False),
        day_windows(NOW, pv_forecast((800, 600, 600), low=0.5), load_forecast(600), BERLIN),
    )
    assert without_bridge.soc_pct < poor  # only the night


def test_scenario_a_too_optimistic_forecast_raises_the_target_after_30_days() -> None:
    # a poor next day the source calls narrow (P10 = 0.9 x P50): the bridge decides the target
    pv = pv_forecast((800, 800, 800), low=0.9)
    load = load_forecast(400)
    windows = day_windows(NOW, pv, load, BERLIN)
    # the PV forecast was 50 % too high for weeks: every daytime net came in lower
    bridge = windows[RiskWindow.BRIDGE]
    history = [
        ErrorSample(
            day=date(2026, 5, 1) + timedelta(days=i),
            window=kind,
            weather=windows[kind].weather,
            error_wh=-0.5 * windows[kind].pv.p50 + (i % 3 - 1) * 50,
            scale_wh=windows[kind].scale_wh,
        )
        for i in range(40)
        for kind in (RiskWindow.NIGHT, RiskWindow.BRIDGE, RiskWindow.REST_OF_TODAY)
    ]
    before = targets(pv, load=load, model=ErrorModel(tuple(history[: 29 * 3])))
    after = targets(pv, load=load, model=ErrorModel(tuple(history)))
    assert bridge.pv.p50 > 0
    assert before[3] == "next_day_deficit"
    assert after[1] > before[1]


# --- review findings: reserve floor, default band, learned path, load band -------------------


def site_battery(min_soc: float | None) -> BatteryConfig:
    return BatteryConfig(
        power=EntityRef(entity_id="sensor.bat_power"),
        soc=EntityRef(entity_id="sensor.bat_soc"),
        min_soc_pct=min_soc,
    )


def test_effective_reserve_takes_the_higher_floor() -> None:
    assert effective_reserve_pct(None, BATTERY) == 15  # reserve 10 + drift 5
    assert effective_reserve_pct(site_battery(None), BATTERY) == 15
    assert effective_reserve_pct(site_battery(30), BATTERY) == 30
    assert effective_reserve_pct(site_battery(5), BATTERY) == 15


@settings(max_examples=40, deadline=None)
@given(peaks, peaks, st.sampled_from(LEVELS), st.floats(0, 59))
def test_never_below_the_site_min_soc(
    today: float, tomorrow: float, level: RiskLevel, site_min: float
) -> None:
    battery = BATTERY.model_copy(update={"max_soc_pct": 60})
    buffer, evening, _, _ = targets(
        pv_forecast((today, tomorrow, tomorrow)),
        level,
        battery=battery,
        site=site_battery(site_min),
    )
    assert buffer >= site_min - 1e-9
    assert evening >= site_min - 1e-9


def test_a_source_without_a_band_gets_the_default_band() -> None:
    flat = pv_forecast((1500, 1500, 1500), low=1.0, high=1.0)
    windows = day_windows(NOW, flat, load_forecast(), BERLIN)
    day = windows[RiskWindow.REST_OF_TODAY]
    cautious = net_energy_quantile(day, RiskLevel.CAUTIOUS)
    bold = net_energy_quantile(day, RiskLevel.BOLD)
    assert cautious.source == "default_band"
    assert cautious.value_wh < bold.value_wh
    assert targets(flat, RiskLevel.CAUTIOUS)[0] > targets(flat, RiskLevel.BOLD)[0]
    reason = evening_target_pct(BATTERY, BatterySettings(), windows).reason
    assert reason.args["source"] == "default_band"
    # the night has no PV: its load band is the source's own
    assert net_energy_quantile(windows[RiskWindow.NIGHT], 0.1).source == "source_quantiles"


def learned_model(seed: int) -> ErrorModel:
    rng = __import__("random").Random(seed)
    samples = [
        ErrorSample(
            day=date(2026, 4, 1) + timedelta(days=i),
            window=kind,
            weather=weather,  # type: ignore[arg-type]
            error_wh=rng.gauss(-0.1, 0.2) * 10_000,
            scale_wh=10_000,
        )
        for i in range(40)
        for kind in RiskWindow
        for weather in ("clear", "mixed", "overcast", "night")
    ]
    return ErrorModel(tuple(samples))


@settings(max_examples=25, deadline=None)
@given(peaks, peaks, st.integers(0, 1000))
def test_monotone_in_the_risk_level_on_the_learned_path(
    today: float, tomorrow: float, seed: int
) -> None:
    model = learned_model(seed)
    pv = pv_forecast((today, tomorrow, tomorrow))
    results = [targets(pv, level, model=model) for level in LEVELS]
    for cautious, bold in itertools.pairwise(results):
        assert cautious[0] >= bold[0] - 1e-9
        assert cautious[1] >= bold[1] - 1e-9
    windows = day_windows(NOW, pv, load_forecast(), BERLIN)
    assert net_energy_quantile(windows[RiskWindow.NIGHT], 0.1, model).source == "learned"


@settings(max_examples=30, deadline=None)
@given(peaks, peaks, st.floats(1.0, 1.5), st.floats(0.0, 1.0), st.sampled_from(LEVELS))
def test_monotone_in_the_load_band(
    today: float, tomorrow: float, high: float, wider: float, level: RiskLevel
) -> None:
    pv = pv_forecast((today, tomorrow, tomorrow))
    narrow = targets(pv, level, load=load_forecast(high=high))
    wide = targets(pv, level, load=load_forecast(high=high + wider))
    assert wide[0] >= narrow[0] - 1e-9
    assert wide[1] >= narrow[1] - 1e-9
