# SPDX-License-Identifier: Apache-2.0
"""Risk model of the net energy forecast; morning buffer and evening target (Phase 4 WP7).

**Net energy** of a window = PV energy - base load energy (Wh), both forecast and measured; the
**net error** is ``actual net - forecast net`` (the one sign convention of SunCore's forecast
statistics, :class:`~suncore.core.forecast.AccuracyRecord`). Taking the quantile of the *net*
error, instead of P10 PV and P90 load separately, avoids counting caution twice (plan §0).

**Windows** (:class:`RiskWindow`), anchored on today's sunset ("the evening"):

* ``rest_of_night``: now -> today's sunrise (only before sunrise, else empty);
* ``rest_of_today``: max(now, today's sunrise) -> the evening (empty after sunset);
* ``night``: the evening -> the next sunrise (after sunset: now -> the next sunrise);
* ``next_day``: that sunrise -> the following sunset;
* ``bridge``: the evening -> the following sunset (night + next day, one quantile, used for
  next-day bridging so the two parts are not each taken at the risk quantile);
* ``day_and_night``: the coming daytime and the night after it, as one window for the morning
  buffer - before the evening ``now -> the next sunrise``, after it ``next_day`` plus the
  following night.

Sunrise and sunset come from the PV forecast itself: the first and the last quarter hour of a
local day whose P50 exceeds ``max(DAYLIGHT_MIN_W, DAYLIGHT_SHARE * the day's peak)``; a day
without PV in the forecast uses ``FALLBACK_SUNRISE``/``FALLBACK_SUNSET`` local time.

**Weather class** of a daytime window: the class (:class:`~suncore.forecast.pv.clearsky
.WeatherClass`, the PV calibration's clear-sky index of the forecast) that carries the largest
share of the window's forecast PV energy; the nights are their own class ``night``; ``bridge``
and ``day_and_night`` take the class of their day part.

**Distribution** (:func:`net_energy_quantile`) for a quantile ``q`` (the risk level's: cautious
0.1, balanced 0.25, bold 0.5):

``learned``
    From ``min_history_days`` (30) days with samples on: the empirical quantile of the stored
    *relative* errors ``error / scale`` of the same window kind and weather class (at least
    ``MIN_CLASS_SAMPLES``; else the window kind over all classes, at least
    ``MIN_WINDOW_SAMPLES``), times the window's scale ``max(1 Wh, PV P50 + load P50)``, added
    to the forecast net P50. Relative errors transfer between a long and a short
    ``rest_of_today`` and between summer and winter days.
``source_quantiles``
    Until then (or for a thin cell): the sources' own quantiles, combined as a two-piece normal
    under **independence of PV and load**. Over the window the slot quantiles are summed
    (comonotone within each quantity - PV errors of one day move together, the conservative
    choice) giving ``PV10, PV50, PV90`` and ``L10, L50, L90``; with ``z = Phi^-1(q)`` and
    ``z90 = Phi^-1(0.9) = 1.2816``::

        q < 0.5:  net_q = (PV50 - L50) + z * sqrt(((PV50 - PV10) / z90)^2 + ((L90 - L50) / z90)^2)
        q > 0.5:  net_q = (PV50 - L50) + z * sqrt(((PV90 - PV50) / z90)^2 + ((L50 - L10) / z90)^2)

    and ``net_0.5 = PV50 - L50``. The lower side combines the PV shortfall with the load
    excess, the upper side the reverse.
``default_band``
    The same, but for a window whose PV forecast has **no spread** (a source without
    quantiles: P10 = P50 = P90) the PV band is ``DEFAULT_PV_BAND`` of the window's weather class
    (relative P10/P90 factors, ``UNVERIFIED`` reasoned constants) - otherwise every risk level
    would plan on the median PV until the learnt distribution takes over.

**Planner-facing targets** (all return a value and a :class:`~suncore.core.decisions.Reason`):

:func:`evening_target_pct`
    ``floor + 100 * need / capacity`` with ``floor`` =
    :func:`~suncore.core.battery.effective_reserve_pct` (the site battery's minimum SoC or
    reserve + BMS drift margin, whichever is higher) and
    ``need = max(-net_q(night), -net_q(bridge) if bridge_next_day) / discharge_efficiency``
    (Wh the battery must deliver; never negative).
    Never below the user's target SoC (``goals.battery_target``; its time is not used here, the
    timed constraint is the planner's, 4-F), never above ``max_soc_pct``; ``prefer_full`` ->
    ``max_soc_pct``.
:func:`morning_buffer_pct`
    "Battery first until X %" from **one** quantile: ``M = net_q(day_and_night)`` is the energy
    left after the coming daytime *and* the night after it are covered, at the risk quantile;
    ``N = max(1 Wh, -net_0.5(night))`` the median night need (a scale, no second quantile).
    ``share = clamp(1 - M / N, 0, 1)`` and ``X = floor + share * (target - floor)``: a margin of
    at least one more median night lets consumers go first (X = floor), no margin puts the
    battery first up to the evening target (X = target, an upper bound only). Never below the
    user's manual floor (``BatterySettings.morning_buffer_pct``), capped at ``max_soc_pct``;
    ``prefer_full`` -> ``max_soc_pct``.

Both are monotone: a more cautious risk level or a wider distribution never lowers them (a lower
``q`` lowers every net quantile, which raises the need and lowers ``M``). The default band is a
step: a source that gains a tiny spread falls back to its own (possibly narrower) band.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from enum import StrEnum, unique
from statistics import NormalDist
from typing import Any, Final, Literal

from suncore.core.battery import (
    BatteryModel,
    BatterySettings,
    BatteryStrategy,
    RiskLevel,
    effective_reserve_pct,
)
from suncore.core.decisions import Reason
from suncore.core.series import PLANNING_STEP, QuantileSeries, TimeSeries, floor_to_step
from suncore.core.site import BatteryConfig
from suncore.forecast.pv.clearsky import PlantGeometry, WeatherClass, classify_slots
from suncore.forecast.stats import weighted_quantile

DAYLIGHT_MIN_W: Final = 20.0
DAYLIGHT_SHARE: Final = 0.02
FALLBACK_SUNRISE: Final = time(7, 0)
FALLBACK_SUNSET: Final = time(19, 0)
MIN_CLASS_SAMPLES: Final = 10
MIN_WINDOW_SAMPLES: Final = 20
Z90: Final = NormalDist().inv_cdf(0.9)
NIGHT_CLASS: Final = "night"

REASON_MORNING_BUFFER: Final = "planner.battery.morning_buffer"
REASON_EVENING_TARGET: Final = "planner.battery.evening_target"

RiskClass = Literal["overcast", "mixed", "clear", "night"]
DistributionSource = Literal["learned", "source_quantiles", "default_band"]
BufferCause = Literal[
    "prefer_full", "forecast_poor", "forecast_uncertain", "forecast_good", "manual_floor"
]
TargetCause = Literal["prefer_full", "night_load", "next_day_deficit", "user_target", "max_soc"]


_RISK_CLASS: Final[dict[WeatherClass, RiskClass]] = {
    WeatherClass.OVERCAST: "overcast",
    WeatherClass.MIXED: "mixed",
    WeatherClass.CLEAR: "clear",
}
DEFAULT_PV_BAND: Final[dict[RiskClass, tuple[float, float]]] = {
    "clear": (0.85, 1.10),
    "mixed": (0.55, 1.35),
    "overcast": (0.35, 1.70),
    "night": (1.0, 1.0),
}
"""``(P10, P90)`` as factors of the PV P50 for a window without a source band (``UNVERIFIED``:
reasoned, not fitted - replaced by the learnt distribution after ``min_history_days``)."""
_CLASSES: Final[dict[str, RiskClass]] = {
    "overcast": "overcast",
    "mixed": "mixed",
    "clear": "clear",
    "night": "night",
}


@unique
class RiskWindow(StrEnum):
    REST_OF_NIGHT = "rest_of_night"
    REST_OF_TODAY = "rest_of_today"
    NIGHT = "night"
    NEXT_DAY = "next_day"
    BRIDGE = "bridge"
    DAY_AND_NIGHT = "day_and_night"


@dataclass(frozen=True, slots=True)
class EnergyQuantiles:
    """P10/P50/P90 of an energy (Wh) over a window (slot quantiles summed)."""

    p10: float
    p50: float
    p90: float

    @classmethod
    def zero(cls) -> EnergyQuantiles:
        return cls(0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class NetWindow:
    """One window with the forecast PV and load energy and its weather class."""

    kind: RiskWindow
    start: datetime
    end: datetime
    pv: EnergyQuantiles
    load: EnergyQuantiles
    weather: RiskClass

    @property
    def net_p50(self) -> float:
        return self.pv.p50 - self.load.p50

    @property
    def scale_wh(self) -> float:
        """Normalisation of the learnt relative errors (at least 1 Wh)."""
        return max(1.0, self.pv.p50 + self.load.p50)


# --- windows -------------------------------------------------------------------------------


def window_energy(series: QuantileSeries | None, start: datetime, end: datetime) -> EnergyQuantiles:
    """Energy (Wh) of ``series`` (W) in ``[start, end)``, partial slots by their overlap share.

    Unknown slots count as 0 Wh (the caller judges coverage); ``None`` series = zero energy.
    """
    if series is None or end <= start:
        return EnergyQuantiles.zero()
    totals = [0.0, 0.0, 0.0]
    hours = series.step / timedelta(hours=1)
    for i, (begin, stop, _) in enumerate(series.p50.slots()):
        overlap = (min(stop, end) - max(begin, start)) / series.step
        if overlap <= 0:
            continue
        for k, part in enumerate((series.p10, series.p50, series.p90)):
            value = part.values[i]
            if value is not None:
                totals[k] += value * hours * overlap
    lo, mid, hi = totals
    return EnergyQuantiles(min(lo, mid), mid, max(hi, mid))


def daylight_spans(pv: QuantileSeries | None, tz: tzinfo) -> dict[date, tuple[datetime, datetime]]:
    """``(sunrise, sunset)`` per local date of the PV forecast's daylight (module doc)."""
    if pv is None:
        return {}
    peaks: dict[date, float] = {}
    for begin, _, value in pv.p50.slots():
        if value is not None:
            day = begin.astimezone(tz).date()
            peaks[day] = max(peaks.get(day, 0.0), value)
    spans: dict[date, tuple[datetime, datetime]] = {}
    for begin, stop, value in pv.p50.slots():
        if value is None:
            continue
        day = begin.astimezone(tz).date()
        if value <= max(DAYLIGHT_MIN_W, DAYLIGHT_SHARE * peaks[day]):
            continue
        rise, sets = spans.get(day, (begin, stop))
        spans[day] = (min(rise, begin), max(sets, stop))
    return spans


def sun_times(
    day: date, spans: Mapping[date, tuple[datetime, datetime]], tz: tzinfo
) -> tuple[datetime, datetime]:
    if day in spans:
        return spans[day]
    rise = datetime.combine(day, FALLBACK_SUNRISE, tzinfo=tz).astimezone(UTC)
    sets = datetime.combine(day, FALLBACK_SUNSET, tzinfo=tz).astimezone(UTC)
    return rise, sets


def window_class(
    pv: QuantileSeries | None,
    start: datetime,
    end: datetime,
    *,
    plant: PlantGeometry | None,
    tz: tzinfo,
) -> RiskClass:
    """The weather class with the most forecast PV energy in ``[start, end)``; ``overcast``
    without PV in the window."""
    if pv is None:
        return "overcast"
    classes = classify_slots(pv.p50, plant=plant, tz=tz)
    energy: dict[WeatherClass, float] = {}
    for (begin, stop, value), cls in zip(pv.p50.slots(), classes, strict=True):
        if cls is None or value is None or stop <= start or begin >= end:
            continue
        energy[cls] = energy.get(cls, 0.0) + value
    if not energy:
        return "overcast"
    best = max(energy.items(), key=lambda item: item[1])[0]
    return _RISK_CLASS[best]


def day_windows(
    now: datetime,
    pv: QuantileSeries | None,
    load: QuantileSeries | None,
    tz: tzinfo,
    *,
    plant: PlantGeometry | None = None,
) -> dict[RiskWindow, NetWindow]:
    """The windows anchored on today's sunset (module doc)."""
    spans = daylight_spans(pv, tz)
    today = now.astimezone(tz).date()
    sunrise, evening = sun_times(today, spans, tz)
    # After today's sunset the evening has passed: ``rest_of_today`` is empty and the night
    # window starts now (every window is clipped to the current slot below).
    next_rise, next_set = sun_times(today + timedelta(days=1), spans, tz)
    third_rise = sun_times(today + timedelta(days=2), spans, tz)[0]
    begin = floor_to_step(now, PLANNING_STEP)
    out: dict[RiskWindow, NetWindow] = {}

    def add(kind: RiskWindow, start: datetime, end: datetime, weather: RiskClass) -> None:
        start = max(start, begin)
        end = max(end, start)
        out[kind] = NetWindow(
            kind=kind,
            start=start,
            end=end,
            pv=window_energy(pv, start, end),
            load=window_energy(load, start, end),
            weather=weather,
        )

    def daytime(start: datetime, end: datetime) -> RiskClass:
        return window_class(pv, start, end, plant=plant, tz=tz)

    add(RiskWindow.REST_OF_NIGHT, begin, sunrise, NIGHT_CLASS)
    day_start = max(begin, sunrise)
    today_class = daytime(day_start, evening)
    add(RiskWindow.REST_OF_TODAY, day_start, evening, today_class)
    add(RiskWindow.NIGHT, evening, next_rise, NIGHT_CLASS)
    next_class = daytime(next_rise, next_set)
    add(RiskWindow.NEXT_DAY, next_rise, next_set, next_class)
    add(RiskWindow.BRIDGE, evening, next_set, next_class)
    if begin < evening:
        add(RiskWindow.DAY_AND_NIGHT, begin, next_rise, today_class)
    else:
        add(RiskWindow.DAY_AND_NIGHT, next_rise, third_rise, next_class)
    return out


# --- the error distribution --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ErrorSample:
    """One measured net energy error of a past window (``error = actual - forecast``)."""

    day: date
    """The anchor day (the day of the evening; the ``rest_of_today`` day itself)."""
    window: RiskWindow
    weather: RiskClass
    error_wh: float
    scale_wh: float

    @property
    def relative(self) -> float:
        return self.error_wh / max(1.0, self.scale_wh)

    def to_json(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "window": self.window.value,
            "weather": self.weather,
            "error_wh": round(self.error_wh, 3),
            "scale_wh": round(self.scale_wh, 3),
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> ErrorSample:
        weather = _CLASSES.get(str(raw["weather"]))
        if weather is None:
            msg = f"unknown weather class {raw['weather']!r}"
            raise ValueError(msg)
        return cls(
            day=date.fromisoformat(str(raw["day"])),
            window=RiskWindow(str(raw["window"])),
            weather=weather,
            error_wh=float(raw["error_wh"]),
            scale_wh=float(raw["scale_wh"]),
        )


@dataclass(frozen=True, slots=True)
class ErrorModel:
    """The learnt samples (all window kinds) and how many distinct days they cover."""

    samples: tuple[ErrorSample, ...] = ()

    @property
    def days(self) -> int:
        return len({s.day for s in self.samples})

    def cell(self, window: RiskWindow, weather: RiskClass) -> list[float]:
        return [s.relative for s in self.samples if s.window is window and s.weather == weather]

    def pooled(self, window: RiskWindow) -> list[float]:
        return [s.relative for s in self.samples if s.window is window]


@dataclass(frozen=True, slots=True)
class NetQuantile:
    """``value_wh`` = the net energy of a window at quantile ``q``."""

    value_wh: float
    q: float
    source: DistributionSource
    samples: int
    """Learnt samples used (0 for ``source_quantiles``)."""
    pooled: bool = False
    """``learned`` from the window kind over all weather classes (the class cell was thin)."""


def has_pv_band(window: NetWindow) -> bool:
    """Whether the window's PV forecast carries a spread (or has no PV at all)."""
    pv = window.pv
    tiny = 1e-6 * max(1.0, pv.p50)
    return pv.p50 <= tiny or pv.p50 - pv.p10 > tiny or pv.p90 - pv.p50 > tiny


def with_default_band(window: NetWindow) -> NetWindow:
    """The window with ``DEFAULT_PV_BAND`` of its weather class as PV band."""
    low, high = DEFAULT_PV_BAND[window.weather]
    p50 = window.pv.p50
    band = EnergyQuantiles(p50 * low, p50, p50 * high)
    return NetWindow(window.kind, window.start, window.end, band, window.load, window.weather)


def source_net_quantile(window: NetWindow, q: float) -> float:
    """The two-piece normal of the module doc (``source_quantiles``)."""
    if not 0 < q < 1:
        msg = "q must be inside (0, 1)"
        raise ValueError(msg)
    median = window.net_p50
    if math.isclose(q, 0.5):
        return median
    z = NormalDist().inv_cdf(q)
    pv, load = window.pv, window.load
    if q < 0.5:
        sigma = math.hypot((pv.p50 - pv.p10) / Z90, (load.p90 - load.p50) / Z90)
    else:
        sigma = math.hypot((pv.p90 - pv.p50) / Z90, (load.p50 - load.p10) / Z90)
    return median + z * sigma


def net_energy_quantile(
    window: NetWindow,
    risk: RiskLevel | float,
    model: ErrorModel | None = None,
    *,
    min_history_days: int = 30,
) -> NetQuantile:
    """The net energy (Wh) of ``window`` that is exceeded with the risk level's probability."""
    q = risk.quantile if isinstance(risk, RiskLevel) else float(risk)
    if model is not None and model.days >= min_history_days:
        values = model.cell(window.kind, window.weather)
        pooled = False
        if len(values) < MIN_CLASS_SAMPLES:
            values, pooled = model.pooled(window.kind), True
        if len(values) >= (MIN_WINDOW_SAMPLES if pooled else MIN_CLASS_SAMPLES):
            rel = weighted_quantile(values, [1.0] * len(values), q)
            return NetQuantile(
                value_wh=window.net_p50 + rel * window.scale_wh,
                q=q,
                source="learned",
                samples=len(values),
                pooled=pooled,
            )
    if not has_pv_band(window):
        return NetQuantile(
            value_wh=source_net_quantile(with_default_band(window), q),
            q=q,
            source="default_band",
            samples=0,
        )
    return NetQuantile(
        value_wh=source_net_quantile(window, q), q=q, source="source_quantiles", samples=0
    )


# --- targets ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SocTarget:
    """A computed SoC (%) and why."""

    soc_pct: float
    reason: Reason
    source: DistributionSource


def _round(value: float) -> float:
    return round(value, 1)


def evening_target_pct(
    battery: BatteryModel,
    settings: BatterySettings,
    windows: Mapping[RiskWindow, NetWindow],
    *,
    user_target_pct: float | None = None,
    site_battery: BatteryConfig | None = None,
    model: ErrorModel | None = None,
    min_history_days: int = 30,
) -> SocTarget:
    """The SoC (%) the battery should hold at the evening (module doc)."""
    cap = battery.max_soc_pct
    floor = min(cap, effective_reserve_pct(site_battery, battery))
    risk = settings.risk
    night = net_energy_quantile(
        windows[RiskWindow.NIGHT], risk, model, min_history_days=min_history_days
    )
    night_need = max(0.0, -night.value_wh) / battery.discharge_efficiency
    bridge_need = 0.0
    source = night.source
    if settings.bridge_next_day and RiskWindow.BRIDGE in windows:
        bridge = net_energy_quantile(
            windows[RiskWindow.BRIDGE], risk, model, min_history_days=min_history_days
        )
        bridge_need = max(0.0, -bridge.value_wh) / battery.discharge_efficiency
        if bridge.source != "learned":
            source = bridge.source
    computed = floor + battery.wh_to_soc(max(night_need, bridge_need))
    user = 0.0 if user_target_pct is None else user_target_pct
    cause: TargetCause
    if settings.strategy is BatteryStrategy.PREFER_FULL:
        value, cause = cap, "prefer_full"
    elif max(computed, user) > cap:
        value, cause = cap, "max_soc"
    elif user >= computed:
        value, cause = max(user, floor), "user_target"
    else:
        value = computed
        cause = "next_day_deficit" if bridge_need > night_need else "night_load"
    value = min(cap, max(floor, value))
    reason = Reason(
        key="planner.battery.evening_target",
        args={
            "soc_pct": _round(value),
            "cause": cause,
            "night_kwh": _round(night_need / 1000),
            "deficit_kwh": _round(max(0.0, bridge_need - night_need) / 1000),
            "risk": risk.value,
            "source": source,
        },
    )
    return SocTarget(soc_pct=value, reason=reason, source=source)


def morning_buffer_pct(
    battery: BatteryModel,
    settings: BatterySettings,
    windows: Mapping[RiskWindow, NetWindow],
    evening_target: float,
    *,
    site_battery: BatteryConfig | None = None,
    model: ErrorModel | None = None,
    min_history_days: int = 30,
) -> SocTarget:
    """ "Battery first until X %" for the coming daytime (module doc)."""
    cap = battery.max_soc_pct
    manual = settings.morning_buffer_pct
    auto_floor = min(cap, effective_reserve_pct(site_battery, battery))
    floor = min(cap, max(auto_floor, manual or 0.0))
    margin = net_energy_quantile(
        windows[RiskWindow.DAY_AND_NIGHT],
        settings.risk,
        model,
        min_history_days=min_history_days,
    )
    night_need = max(1.0, -windows[RiskWindow.NIGHT].net_p50)
    cause: BufferCause
    if settings.strategy is BatteryStrategy.PREFER_FULL:
        value, cause = cap, "prefer_full"
    else:
        target = min(cap, max(floor, evening_target))
        share = min(1.0, max(0.0, 1.0 - margin.value_wh / night_need))
        value = floor + share * (target - floor)
        if share >= 1.0:
            cause = "forecast_poor"
        elif share > 0.0:
            cause = "forecast_uncertain"
        elif manual is not None and manual > auto_floor:
            cause = "manual_floor"
        else:
            cause = "forecast_good"
    value = min(cap, max(floor, value))
    reason = Reason(
        key="planner.battery.morning_buffer",
        args={
            "soc_pct": _round(value),
            "cause": cause,
            "surplus_kwh": _round(margin.value_wh / 1000),
            "risk": settings.risk.value,
            "source": margin.source,
        },
    )
    return SocTarget(soc_pct=value, reason=reason, source=margin.source)


# --- learning samples -------------------------------------------------------------------------


def _window_slots(series: TimeSeries, start: datetime, end: datetime) -> tuple[int, int, float]:
    """``(slots of the window, known slots, energy Wh of the known ones)`` on the series' grid;
    a window slot the series does not reach counts as unknown."""
    total = max(0, -(-(end - start) // series.step))
    hours = series.step / timedelta(hours=1)
    known = 0
    energy = 0.0
    for begin, _, value in series.slots():
        if start <= begin < end and value is not None:
            known += 1
            energy += value * hours
    return total, known, energy


def coverage(series: TimeSeries, start: datetime, end: datetime) -> float:
    """Share of the window ``[start, end)`` whose slots ``series`` knows (a slot outside the
    series counts as unknown)."""
    total, known, _ = _window_slots(series, start, end)
    return known / total if total else 0.0


def measured_energy(series: TimeSeries, start: datetime, end: datetime) -> float:
    """Energy (Wh) of the known slots of a W series in ``[start, end)``, scaled up to the whole
    window from the known share (the caller requires a high coverage)."""
    total, known, energy = _window_slots(series, start, end)
    return energy * total / known if known else 0.0


def merge_samples(
    old: Iterable[ErrorSample], new: Iterable[ErrorSample], *, keep_days: int, today: date
) -> tuple[ErrorSample, ...]:
    """``old`` plus ``new`` (a new sample replaces one of the same day and window), only the
    last ``keep_days`` days, ordered by day."""
    merged: dict[tuple[date, RiskWindow], ErrorSample] = {(s.day, s.window): s for s in old}
    merged.update({(s.day, s.window): s for s in new})
    oldest = today - timedelta(days=keep_days)
    return tuple(
        sorted(
            (s for s in merged.values() if s.day >= oldest), key=lambda s: (s.day, s.window.value)
        )
    )


def samples_view(samples: Sequence[ErrorSample]) -> dict[str, int]:
    """Sample count per ``window/weather`` cell (for the API)."""
    out: dict[str, int] = {}
    for s in samples:
        key = f"{s.window.value}/{s.weather}"
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


__all__ = [
    "DEFAULT_PV_BAND",
    "MIN_CLASS_SAMPLES",
    "MIN_WINDOW_SAMPLES",
    "REASON_EVENING_TARGET",
    "REASON_MORNING_BUFFER",
    "DistributionSource",
    "EnergyQuantiles",
    "ErrorModel",
    "ErrorSample",
    "NetQuantile",
    "NetWindow",
    "RiskClass",
    "RiskWindow",
    "SocTarget",
    "coverage",
    "day_windows",
    "daylight_spans",
    "evening_target_pct",
    "has_pv_band",
    "measured_energy",
    "merge_samples",
    "morning_buffer_pct",
    "net_energy_quantile",
    "samples_view",
    "source_net_quantile",
    "sun_times",
    "window_class",
    "window_energy",
    "with_default_band",
]
