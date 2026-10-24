# SPDX-License-Identifier: Apache-2.0
"""Planner scenarios of the virtual house (Phase 4 WP8): inputs for tests and the benchmark.

Each scenario is a deterministic 48 h situation - PV forecast (P10/P50/P90), base load, prices,
battery, devices - turned into :class:`~suncore.planner.inputs.PlannerInputs` the way the
runtime will (WP9): through :func:`~suncore.planner.inputs.build_inputs`, with the morning
buffer and the evening target from the real risk model (``suncore.forecast.risk``, source
quantiles because the scenarios have no learnt history).

Shapes are reasoned, not measured: PV is a sine-shaped day per array (east and west arrays
peak 1.5 h apart), P10 = 0.6 x P50 and P90 = min(1.15 x P50, clear sky) unless a scenario
says otherwise; the base load is 300 W with a morning and an evening bump; "Tibber-like"
prices are 0.27/kWh with a morning and an evening peak and a midday dip.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from suncore.core.battery import (
    BatteryModel,
    BatterySettings,
    BatteryStrategy,
    GridChargePolicy,
    RiskLevel,
)
from suncore.core.config import GoalsConfig
from suncore.core.devices import (
    ComfortBand,
    DeviceConfig,
    DeviceKind,
    PowerModel,
    PowerModelKind,
    SetpointControl,
)
from suncore.core.series import (
    PLANNING_STEP,
    PriceSeries,
    QuantileSeries,
    SeriesUnit,
    TimeSeries,
    planning_grid,
)
from suncore.core.site import EntityRef
from suncore.core.tariff import WeeklySchedule
from suncore.forecast.risk import (
    RiskWindow,
    day_windows,
    evening_target_pct,
    morning_buffer_pct,
)
from suncore.planner.inputs import PLANNER_HORIZON, PlannerInputs, build_inputs

TZ: Final = ZoneInfo("Europe/Berlin")
SUMMER: Final = date(2026, 6, 20)
WINTER: Final = date(2026, 12, 8)
EEG_UP_TO_10_KWP: Final = 0.0786
EEG_ABOVE_10_KWP: Final = 0.068
"""EEG feed-in rates (currency/kWh) of the maintainer's tiers; reasoned example values, the
maintainer enters his own (plan §6 question 8)."""


def blended_eeg(kwp: float) -> float:
    """Tiered EEG rate blended by kWp share (like ``core.tariff.blended_export_price``)."""
    low = min(kwp, 10.0)
    return (low * EEG_UP_TO_10_KWP + max(0.0, kwp - 10.0) * EEG_ABOVE_10_KWP) / kwp


def _hours(at: datetime) -> float:
    local = at.astimezone(TZ)
    return local.hour + local.minute / 60


def _bell(hour: float, rise: float, set_: float) -> float:
    if not rise < hour < set_:
        return 0.0
    return float(math.sin(math.pi * (hour - rise) / (set_ - rise)) ** 1.3)


def pv_clear_w(at: datetime, kwp: float, *, summer: bool = True, east_west: bool = True) -> float:
    """Clear-sky PV (W) at the slot's middle; ``kwp`` split evenly east/west."""
    hour = _hours(at + PLANNING_STEP / 2)
    rise, set_ = (5.2, 21.6) if summer else (8.2, 16.4)
    peak = kwp * 1000 * (0.78 if summer else 0.35)
    if not east_west:
        return peak * _bell(hour, rise, set_)
    shift = 1.5 if summer else 0.6
    east = _bell(hour, rise, set_ - 2 * shift)
    west = _bell(hour, rise + 2 * shift, set_)
    return peak * 0.62 * (east + west)


def base_load_w(at: datetime, base: float = 300.0) -> float:
    hour = _hours(at)
    morning = 350 * math.exp(-(((hour - 7.5) / 1.0) ** 2))
    evening = 650 * math.exp(-(((hour - 19.5) / 1.8) ** 2))
    return base + morning + evening


def tibber_price(at: datetime) -> float:
    hour = _hours(at)
    morning = 0.05 * math.exp(-(((hour - 8.0) / 1.5) ** 2))
    evening = 0.09 * math.exp(-(((hour - 19.0) / 2.0) ** 2))
    midday = 0.07 * math.exp(-(((hour - 13.5) / 2.5) ** 2))
    night = 0.03 * math.exp(-(((hour - 3.0) / 2.0) ** 2))
    return round(0.27 + morning + evening - midday - night, 4)


def series(
    starts: Sequence[datetime], values: Sequence[float | None], unit: SeriesUnit
) -> TimeSeries:
    return TimeSeries(start=starts[0], step=PLANNING_STEP, values=tuple(values), unit=unit)


def quantiles(
    starts: Sequence[datetime],
    p50: Sequence[float],
    low: float = 0.6,
    high: float = 1.15,
    cap: Sequence[float] | None = None,
) -> QuantileSeries:
    upper = [
        v * high if cap is None else max(v, min(v * high, c))
        for v, c in zip(p50, cap or p50, strict=True)
    ]
    return QuantileSeries(
        p10=series(starts, [v * low for v in p50], SeriesUnit.W),
        p50=series(starts, list(p50), SeriesUnit.W),
        p90=series(starts, upper, SeriesUnit.W),
    )


def prices(
    starts: Sequence[datetime],
    import_price: Sequence[float | None],
    export_price: Sequence[float | None],
    *,
    includes: str = "all_in",
) -> PriceSeries:
    return PriceSeries(
        source_id="sim.tibber",
        price_includes="all_in" if includes == "all_in" else "spot_only",
        import_price=series(starts, import_price, SeriesUnit.PRICE),
        export_price=series(starts, export_price, SeriesUnit.PRICE),
    )


def climate(device_id: str, nominal_w: float, value: float | None = None) -> DeviceConfig:
    return DeviceConfig(
        id=device_id,
        template_id="lg.thinq_climate",
        name=device_id,
        kind=DeviceKind.CLIMATE,
        entities={"climate": EntityRef(entity_id=f"climate.{device_id}")},
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=nominal_w),
        control=SetpointControl(kind="setpoint", role="climate", shift_max_c=2.0),
        comfort=ComfortBand(min_c=20.0, max_c=26.0),
        planner_value_per_kwh=value,
    )


@dataclass(frozen=True, slots=True)
class Situation:
    """One scenario before it becomes planner inputs."""

    now: datetime
    kwp: float = 8.0
    summer: bool = True
    day_factors: Sequence[float] = (1.0, 1.0, 1.0)
    """Cloud factor per local day (today, tomorrow, the day after)."""
    cloud: Callable[[datetime], float] | None = None
    """Extra factor per slot (mixed clouds)."""
    p10_share: float = 0.6
    load_base_w: float = 300.0
    import_price: Callable[[datetime], float | None] = tibber_price
    export_price: Callable[[datetime], float | None] = lambda _at: 0.08
    capacity_wh: float = 10_000.0
    max_power_w: float = 5_000.0
    soc_pct: float = 30.0
    strategy: BatteryStrategy = BatteryStrategy.SELF_CONSUMPTION
    risk: RiskLevel = RiskLevel.CAUTIOUS
    grid_charge: GridChargePolicy = field(default_factory=GridChargePolicy)
    goals: GoalsConfig = field(default_factory=GoalsConfig)
    devices: Sequence[DeviceConfig] = ()
    export_limit_w: float | None = None
    import_limit_w: float | None = None
    price_includes: str = "all_in"
    with_prices: bool = True


def battery_settings(s: Situation) -> BatterySettings:
    return BatterySettings(
        model=BatteryModel(
            capacity_wh=s.capacity_wh,
            max_charge_w=s.max_power_w,
            max_discharge_w=s.max_power_w,
        ),
        strategy=s.strategy,
        risk=s.risk,
        grid_charge=s.grid_charge,
    )


def forecasts(s: Situation) -> tuple[list[datetime], QuantileSeries, QuantileSeries]:
    grid = planning_grid(s.now, PLANNER_HORIZON)
    starts = grid.starts()
    first = s.now.astimezone(TZ).date()
    clear, p50 = [], []
    for at in starts:
        day = (at.astimezone(TZ).date() - first).days
        factor = s.day_factors[min(day, len(s.day_factors) - 1)]
        if s.cloud is not None:
            factor *= s.cloud(at)
        c = pv_clear_w(at, s.kwp, summer=s.summer)
        clear.append(c)
        p50.append(c * factor)
    pv = quantiles(starts, p50, low=s.p10_share, cap=clear)
    load = quantiles(starts, [base_load_w(at, s.load_base_w) for at in starts], 0.8, 1.3)
    return starts, pv, load


def situation_inputs(s: Situation) -> PlannerInputs:
    """The planner inputs of a situation, with the risk model's buffer and target."""
    starts, pv, load = forecasts(s)
    settings = battery_settings(s)
    assert settings.model is not None
    windows = day_windows(s.now, pv, load, TZ)
    target = s.goals.battery_target
    evening = evening_target_pct(
        settings.model,
        settings,
        windows,
        user_target_pct=None if target is None else target.soc_pct,
    )
    buffer = morning_buffer_pct(settings.model, settings, windows, evening.soc_pct)
    price = (
        prices(
            starts,
            [s.import_price(at) for at in starts],
            [s.export_price(at) for at in starts],
            includes=s.price_includes,
        )
        if s.with_prices
        else None
    )
    return build_inputs(
        s.now,
        tz=TZ,
        load=load,
        pv=pv,
        prices=price,
        battery=settings,
        soc_pct=s.soc_pct,
        goals=s.goals,
        morning_buffer=buffer,
        evening_target=evening,
        evening_at=windows[RiskWindow.NIGHT].start,
        devices=s.devices,
        import_limit_w=s.import_limit_w,
        export_limit_w=s.export_limit_w,
    )


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def _clouds(at: datetime) -> float:
    hour = _hours(at)
    return 0.35 + 0.65 * abs(math.sin(1.7 * hour) * math.cos(0.45 * hour))


def _negative_midday(at: datetime) -> float:
    hour, day = _hours(at), at.astimezone(TZ).date()
    if day == SUMMER and 12 <= hour < 15:
        return -0.05
    return tibber_price(at)


def _rule_export(at: datetime) -> float:
    price = _negative_midday(at)
    return 0.0 if price < 0 else 0.08


def _spike(at: datetime) -> float:
    """0.30/kWh, but 1.50/kWh from 01:00 to 02:00 the night after the summer day."""
    local = at.astimezone(TZ)
    after = local.date() == SUMMER + timedelta(days=1)
    return 1.5 if after and 1 <= _hours(at) < 2 else 0.3


def _night_cheap(at: datetime) -> float:
    hour = _hours(at)
    return 0.15 if 1 <= hour < 5 else 0.35


DEVICES: Final = (climate("ac_living", 1000.0), climate("ac_office", 800.0))


def situation(name: str) -> Situation:  # noqa: PLR0911 - one scenario per branch
    """The named scenario (see :data:`SCENARIOS`)."""
    morning = _at(SUMMER, 6)
    if name == "sunny_day":
        return Situation(now=morning, devices=DEVICES)
    if name == "mixed_clouds":
        return Situation(now=morning, cloud=_clouds, devices=DEVICES)
    if name == "two_poor_days":
        return Situation(
            now=_at(WINTER, 9),
            summer=False,
            day_factors=(0.25, 0.15, 0.15),
            soc_pct=85.0,
            devices=DEVICES,
            import_price=lambda _at: 0.30,
        )
    if name == "forecast_too_optimistic":
        return Situation(now=morning, p10_share=0.25, devices=DEVICES, soc_pct=15.0)
    if name == "negative_prices":
        return Situation(
            now=morning,
            soc_pct=20.0,
            capacity_wh=8_000.0,
            import_price=_negative_midday,
            export_price=_rule_export,
            devices=DEVICES,
        )
    if name == "price_spike":
        return Situation(now=_at(SUMMER, 22), soc_pct=20.0, import_price=_spike)
    if name == "grid_charge_price_level":
        return Situation(
            now=_at(WINTER, 22),
            summer=False,
            day_factors=(0.05, 0.05, 0.05),
            soc_pct=20.0,
            import_price=_night_cheap,
            grid_charge=GridChargePolicy(
                enabled=True, max_price=0.2, schedule=WeeklySchedule.daily("00:00", "06:00")
            ),
        )
    if name == "prefer_full":
        return Situation(
            now=morning,
            strategy=BatteryStrategy.PREFER_FULL,
            devices=(climate("ac_living", 1000.0, value=0.3),),
        )
    if name == "reference_house":
        return reference_situation()
    msg = f"unknown planner scenario {name!r}"
    raise KeyError(msg)


def reference_situation(now: datetime | None = None) -> Situation:
    """The maintainer's house (plan WP8): 17 kWp east-west, 29.6 kWh, 13.5 kW export limit,
    Tibber-like 15-minute prices for 48 h, tiered EEG export price blended, two climate
    devices with a value."""
    return Situation(
        now=now or _at(SUMMER, 5, 7),
        kwp=17.0,
        capacity_wh=29_600.0,
        max_power_w=10_000.0,
        soc_pct=35.0,
        load_base_w=450.0,
        strategy=BatteryStrategy.PREFER_FULL,
        export_price=lambda _at: round(blended_eeg(17.0), 4),
        export_limit_w=13_500.0,
        devices=(climate("ac_living", 1500.0, value=0.12), climate("ac_bedroom", 1200.0, 0.1)),
        day_factors=(1.0, 0.7, 0.9),
        cloud=_clouds,
    )


SCENARIOS: Final = (
    "sunny_day",
    "mixed_clouds",
    "two_poor_days",
    "forecast_too_optimistic",
    "negative_prices",
    "price_spike",
    "grid_charge_price_level",
    "prefer_full",
    "reference_house",
)


def scenario_inputs(name: str) -> PlannerInputs:
    return situation_inputs(situation(name))


__all__ = [
    "SCENARIOS",
    "TZ",
    "Situation",
    "blended_eeg",
    "forecasts",
    "reference_situation",
    "scenario_inputs",
    "situation",
    "situation_inputs",
]
