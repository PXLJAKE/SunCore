# SPDX-License-Identifier: Apache-2.0
"""Load forecast: base load profiles per day type, temperature term, residual quantiles
(Phase 4 WP6). Pure functions; the service (:mod:`suncore.forecast.load_service`) reads the
history and writes the result.

**Base load** = the house consumption (``site.house_w``, which *includes* every device in the
house: ``house = pv + grid - battery``) minus the draw of every configured (controllable) device
(``device.<id>.power_w``). A device is subtracted exactly once; a quarter hour without a device
measurement counts that device as 0 W (the base load then still contains its draw) and is
counted in ``unmeasured_slots`` - the rebuilt history from Home Assistant's statistics has no
device rows at all. A negative difference (sensor mismatch) is clipped to 0 W and counted.
Controlled devices are forecast by the planner from their own models, never here.

**Profile** per day type (:class:`DayType`) and local quarter hour of the day (96 slots, wall
clock: on a 25-hour day the repeated hour is averaged, on a 23-hour day the skipped slots simply
get no sample). Every complete local day with at least ``DAY_COVERAGE`` of its quarter hours
known contributes one sample per slot, weighted by exponential smoothing
(``0.5 ** (age_days / HALF_LIFE_DAYS)``, half-life 21 days). Robust against outliers: the
samples of one cell are clipped into ``median +- OUTLIER_Z * scale`` with the robust scale
``1.4826 * MAD``, at least half the median (clipped z-score), before the weighted mean. A day
type with few days is shrunk towards a fallback with
``(n * own + SHRINK_DAYS * fallback) / (n + SHRINK_DAYS)``: workday and weekend towards the
profile of all non-holiday days, holiday towards the (shrunk) weekend profile - a holiday looks
like a Sunday until enough holidays were seen. The profile is finally smoothed along the day
with the kernel ``(1/4, 1/2, 1/4)``.

**Temperature term** (only with a configured ``weather`` entity): the daily residual (mean base
load of the day minus the mean of its profile) is regressed on heating and cooling degrees of
the day's mean outside temperature, ``HDD = max(0, 15 °C - T)`` and ``CDD = max(0, T - 22 °C)``,
by recency-weighted least squares, centred on the weighted mean degrees of the fitting days (so
an average day gets no offset), coefficients bounded to ``[0, MAX_W_PER_K]``. It is active from
``TEMPERATURE_MIN_DAYS`` days with a temperature and a spread of at least
``TEMPERATURE_MIN_SPREAD_K`` in one of the two regressors; the offset of a forecast day is
bounded to ``TEMPERATURE_MAX_SHARE`` of that day's mean profile.

**Quantiles**: the in-sample residuals (actual - profile - temperature offset) per time-of-day
block (:class:`SlotBlock`) give the 10 % and 90 % quantiles, added to the P50 (pooled over all
blocks when a block has fewer than ``RESIDUAL_MIN_SAMPLES``). In-sample residuals are slightly
optimistic; the risk model learns the real error of the net energy separately (WP7).

**Cold start**: with fewer than ``COLD_START_MIN_DAYS`` usable days the forecast is a flat line
at the mean of the last ``COLD_START_RECENT_DAYS`` days' known values with a wide band
(P10 = 0.5 x, P90 = 2 x), quality ``cold_start``; without any value there is no forecast
(``no_history``). The 28 days rebuilt from Home Assistant's statistics (PR 4-B) make the
profile usable from the first day.

**Day types**: ``workday`` binary sensor ``on`` = workday; ``off`` on Monday-Friday = holiday,
on Saturday/Sunday = weekend (the sensor cannot tell the two apart, research note "Workday");
without the sensor the weekday rule applies (Mon-Fri workday, Sat/Sun weekend, never holiday).
A sensor whose ``days_offset`` is not 0 describes another day and is ignored.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from enum import StrEnum, unique
from typing import Any, Final, Literal

from suncore.core.series import (
    PLANNING_STEP,
    Grid,
    QuantileSeries,
    SeriesUnit,
    TimeSeries,
)
from suncore.forecast.days import local_days, local_midnight
from suncore.forecast.stats import (
    clip_outliers,
    median,
    recency_weight,
    weighted_mean,
    weighted_quantile,
)

LOAD_SOURCE_ID: Final = "load_profile"
"""Source id of the profile forecast in ``timeseries.db`` and the accuracy statistics."""

SLOTS_PER_DAY: Final = 96
HALF_LIFE_DAYS: Final = 21.0
HISTORY_DAYS: Final = 56
"""Look-back of the fit (eight weeks; older days weigh less than 16 %)."""
DAY_COVERAGE: Final = 0.75
OUTLIER_Z: Final = 3.0
OUTLIER_FLOOR_W: Final = 20.0
OUTLIER_FLOOR_SHARE: Final = 0.5
"""Lowest robust scale of a cell: ``max(OUTLIER_FLOOR_W, OUTLIER_FLOOR_SHARE * |median|)``. A
cell of near-identical values (MAD about 0) would otherwise clip a real level change (a new
fridge, a heating season) onto the old median for weeks."""
SHRINK_DAYS: Final = 3.0
SMOOTHING_KERNEL: Final = (0.25, 0.5, 0.25)
COLD_START_MIN_DAYS: Final = 3
COLD_START_RECENT_DAYS: Final = 7
COLD_START_LOW: Final = 0.5
COLD_START_HIGH: Final = 2.0
RESIDUAL_MIN_SAMPLES: Final = 48
LOW_QUANTILE: Final = 0.1
HIGH_QUANTILE: Final = 0.9
HEATING_BASE_C: Final = 15.0
COOLING_BASE_C: Final = 22.0
TEMPERATURE_MIN_DAYS: Final = 14
TEMPERATURE_MIN_SPREAD_K: Final = 1.0
TEMPERATURE_DAY_COVERAGE: Final = 0.5
MAX_W_PER_K: Final = 300.0
TEMPERATURE_MAX_SHARE: Final = 0.5
MIN_TEMPERATURE_HOURS: Final = 6
"""Hours with a temperature a day needs for its forecast daily mean."""


@unique
class DayType(StrEnum):
    WORKDAY = "workday"
    WEEKEND = "weekend"
    HOLIDAY = "holiday"


@unique
class DayTypeSource(StrEnum):
    """Where the day type of a date came from."""

    WORKDAY_SENSOR = "workday_sensor"
    """The state of the ``workday`` binary sensor (today only)."""
    CHECK_DATE = "check_date"
    """The read-only action ``workday.check_date``."""
    WEEKDAY_RULE = "weekday_rule"
    """Mon-Fri workday, Sat/Sun weekend (no sensor, or it did not answer)."""


@dataclass(frozen=True, slots=True)
class DayInfo:
    day_type: DayType
    source: DayTypeSource


@unique
class LoadQuality(StrEnum):
    OK = "ok"
    COLD_START = "cold_start"
    NO_HISTORY = "no_history"


@unique
class SlotBlock(StrEnum):
    """Time-of-day block the residual quantiles are pooled in (local wall clock)."""

    NIGHT = "night"
    """00:00-06:00"""
    MORNING = "morning"
    """06:00-10:00"""
    MIDDAY = "midday"
    """10:00-16:00"""
    EVENING = "evening"
    """16:00-22:00"""
    LATE = "late"
    """22:00-24:00"""


_BLOCK_EDGES: Final = ((24, SlotBlock.NIGHT), (40, SlotBlock.MORNING), (64, SlotBlock.MIDDAY))


def slot_block(slot: int) -> SlotBlock:
    """Block of a quarter-hour index (0 = 00:00-00:15 local)."""
    for edge, block in _BLOCK_EDGES:
        if slot < edge:
            return block
    return SlotBlock.EVENING if slot < 88 else SlotBlock.LATE


def weekday_rule(day: date) -> DayType:
    return DayType.WORKDAY if day.weekday() < 5 else DayType.WEEKEND


def day_type_from_workday(day: date, workday: bool) -> DayType:
    """``workday`` as the sensor/action says; ``off`` on Mon-Fri is a holiday."""
    if workday:
        return DayType.WORKDAY
    return DayType.HOLIDAY if day.weekday() < 5 else DayType.WEEKEND


def local_slot(moment: datetime, tz: tzinfo) -> tuple[date, int]:
    """Local date and quarter-hour index (0..95) of an instant."""
    local = moment.astimezone(tz)
    return local.date(), local.hour * 4 + local.minute // 15


# --- base load -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BaseLoad:
    """The base load history and how much of it lacks device measurements."""

    series: TimeSeries
    unmeasured_slots: Mapping[str, int] = field(default_factory=dict)
    """Device id -> quarter hours (with a house value) without a device measurement (0 W
    assumed, so the base load still contains that device's draw)."""
    clamped_slots: int = 0
    """Quarter hours where the devices exceeded the house (clipped to 0 W)."""


def base_load(house: TimeSeries, devices: Mapping[str, TimeSeries]) -> BaseLoad:
    """``house - sum(devices)`` per slot (W); every device series must share the house grid."""
    for device_id, series in devices.items():
        if not series.same_grid(house):
            msg = f"base_load: device {device_id} is not on the house grid"
            raise ValueError(msg)
    unmeasured = dict.fromkeys(devices, 0)
    clamped = 0
    values: list[float | None] = []
    for i, total in enumerate(house.values):
        if total is None:
            values.append(None)
            continue
        draw = 0.0
        for device_id, series in devices.items():
            value = series.values[i]
            if value is None:
                unmeasured[device_id] += 1
            else:
                draw += max(0.0, value)
        rest = total - draw
        if rest < 0:
            clamped += 1
            rest = 0.0
        values.append(rest)
    series = house.model_copy(update={"values": tuple(values), "unit": SeriesUnit.W})
    return BaseLoad(
        series=series,
        unmeasured_slots={k: v for k, v in unmeasured.items() if v},
        clamped_slots=clamped,
    )


# --- day grids -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DayValues:
    """One complete local day of a 15-minute series by wall-clock slot."""

    day: date
    slots: tuple[float | None, ...]
    """96 entries; ``None`` = unknown or the slot does not exist that day (spring forward)."""
    coverage: float
    """Known share of the slots that exist that day."""

    def known(self) -> list[tuple[int, float]]:
        return [(i, v) for i, v in enumerate(self.slots) if v is not None]


def day_values(series: TimeSeries, tz: tzinfo, until: datetime) -> list[DayValues]:
    """The complete local days of ``series`` that end at or before ``until``."""
    if not len(series):
        return []
    days = local_days(series.start, min(series.end, until), tz)
    out: list[DayValues] = []
    for day in days:
        sums = [0.0] * SLOTS_PER_DAY
        counts = [0] * SLOTS_PER_DAY
        exists = [False] * SLOTS_PER_DAY
        moment = day.start
        while moment < day.end:
            _, slot = local_slot(moment, tz)
            exists[slot] = True
            index = (moment - series.start) // series.step
            if 0 <= index < len(series):
                value = series.values[index]
                if value is not None:
                    sums[slot] += value
                    counts[slot] += 1
            moment += PLANNING_STEP
        slots = tuple(s / c if c else None for s, c in zip(sums, counts, strict=True))
        existing = sum(exists)
        known = sum(1 for c in counts if c)
        out.append(DayValues(day.day, slots, known / existing if existing else 0.0))
    return out


# --- temperature ---------------------------------------------------------------------------

TemperatureReason = Literal["active", "not_configured", "too_few_days", "no_spread"]


def degree_days(temperature_c: float) -> tuple[float, float]:
    """``(heating, cooling)`` degrees of a daily mean temperature."""
    return max(0.0, HEATING_BASE_C - temperature_c), max(0.0, temperature_c - COOLING_BASE_C)


@dataclass(frozen=True, slots=True)
class TemperatureTerm:
    """The fitted temperature dependence of the daily base load (W per kelvin)."""

    active: bool
    reason: TemperatureReason
    days: int = 0
    heating_w_per_k: float = 0.0
    cooling_w_per_k: float = 0.0
    mean_hdd: float = 0.0
    mean_cdd: float = 0.0

    def offset_w(self, temperature_c: float | None) -> float:
        """Offset of a day with mean ``temperature_c`` against an average fitting day."""
        if not self.active or temperature_c is None:
            return 0.0
        hdd, cdd = degree_days(temperature_c)
        return self.heating_w_per_k * (hdd - self.mean_hdd) + self.cooling_w_per_k * (
            cdd - self.mean_cdd
        )


def _slope(xs: Sequence[float], ys: Sequence[float], ws: Sequence[float]) -> float:
    """Weighted least-squares slope of centred data (0 when ``xs`` has no spread)."""
    sxx = sum(w * x * x for x, w in zip(xs, ws, strict=True))
    if sxx <= 1e-12:
        return 0.0
    return sum(w * x * y for x, y, w in zip(xs, ys, ws, strict=True)) / sxx


def _clamp_coef(value: float) -> float:
    return min(MAX_W_PER_K, max(0.0, value))


def fit_temperature(samples: Sequence[tuple[float, float, float]]) -> TemperatureTerm:
    """Fit the term from ``(daily residual W, daily mean °C, weight)`` samples."""
    if len(samples) < TEMPERATURE_MIN_DAYS:
        return TemperatureTerm(active=False, reason="too_few_days", days=len(samples))
    ws = [w for _, _, w in samples]
    hdd = [degree_days(t)[0] for _, t, _ in samples]
    cdd = [degree_days(t)[1] for _, t, _ in samples]
    res = [r for r, _, _ in samples]
    mh, mc, mr = weighted_mean(hdd, ws), weighted_mean(cdd, ws), weighted_mean(res, ws)
    xh = [h - mh for h in hdd]
    xc = [c - mc for c in cdd]
    y = [r - mr for r in res]

    def spread(xs: Sequence[float]) -> float:
        return math.sqrt(weighted_mean([x * x for x in xs], ws))

    has_h = spread(xh) >= TEMPERATURE_MIN_SPREAD_K
    has_c = spread(xc) >= TEMPERATURE_MIN_SPREAD_K
    if not has_h and not has_c:
        return TemperatureTerm(active=False, reason="no_spread", days=len(samples))
    h = c = 0.0
    if has_h and has_c:
        shh = sum(w * a * a for a, w in zip(xh, ws, strict=True))
        scc = sum(w * a * a for a, w in zip(xc, ws, strict=True))
        shc = sum(w * a * b for a, b, w in zip(xh, xc, ws, strict=True))
        shy = sum(w * a * b for a, b, w in zip(xh, y, ws, strict=True))
        scy = sum(w * a * b for a, b, w in zip(xc, y, ws, strict=True))
        det = shh * scc - shc * shc
        if det > 1e-9:
            h = (shy * scc - scy * shc) / det
            c = (scy * shh - shy * shc) / det
        if det <= 1e-9 or h < 0 or c < 0:  # fall back to the stronger single regressor
            h1, c1 = _slope(xh, y, ws), _slope(xc, y, ws)
            h, c = (h1, 0.0) if abs(h1) * spread(xh) >= abs(c1) * spread(xc) else (0.0, c1)
    elif has_h:
        h = _slope(xh, y, ws)
    else:
        c = _slope(xc, y, ws)
    return TemperatureTerm(
        active=True,
        reason="active",
        days=len(samples),
        heating_w_per_k=_clamp_coef(h),
        cooling_w_per_k=_clamp_coef(c),
        mean_hdd=mh,
        mean_cdd=mc,
    )


# --- model -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LoadModel:
    """A fitted load profile model (or the cold-start/no-history placeholder)."""

    quality: LoadQuality
    days: int
    """Complete local days that went into the fit."""
    profiles: Mapping[DayType, tuple[float, ...]] = field(default_factory=dict)
    day_counts: Mapping[DayType, int] = field(default_factory=dict)
    residuals: Mapping[SlotBlock, tuple[float, float]] = field(default_factory=dict)
    """``(q10, q90)`` offsets (W) per block, ``q10 <= 0 <= q90``."""
    flat_w: float | None = None
    """Cold start: the flat level (W)."""
    temperature: TemperatureTerm = TemperatureTerm(active=False, reason="not_configured")

    def day_mean(self, day_type: DayType) -> float:
        profile = self.profiles.get(day_type)
        return sum(profile) / len(profile) if profile else 0.0

    def quantiles(
        self, slot: int, day_type: DayType, temperature_c: float | None
    ) -> tuple[float, float, float] | None:
        """``(p10, p50, p90)`` W of one slot; ``None`` without a model."""
        if self.quality is LoadQuality.NO_HISTORY or (
            self.quality is LoadQuality.COLD_START and self.flat_w is None
        ):
            return None
        if self.quality is LoadQuality.COLD_START:
            assert self.flat_w is not None
            return COLD_START_LOW * self.flat_w, self.flat_w, COLD_START_HIGH * self.flat_w
        profile = self.profiles[day_type]
        bound = TEMPERATURE_MAX_SHARE * self.day_mean(day_type)
        offset = min(bound, max(-bound, self.temperature.offset_w(temperature_c)))
        p50 = max(0.0, profile[slot] + offset)
        q10, q90 = self.residuals.get(slot_block(slot), (0.0, 0.0))
        return max(0.0, min(p50, p50 + q10)), p50, max(p50, p50 + q90)


def _cell_mean(samples: Sequence[tuple[float, float]]) -> float:
    values = [v for v, _ in samples]
    floor = max(OUTLIER_FLOOR_W, OUTLIER_FLOOR_SHARE * abs(median(values)))
    clipped = clip_outliers(values, OUTLIER_Z, floor)
    return weighted_mean(clipped, [w for _, w in samples])


def _smooth(profile: Sequence[float]) -> tuple[float, ...]:
    n = len(profile)
    a, b, c = SMOOTHING_KERNEL
    return tuple(
        a * profile[(i - 1) % n] + b * profile[i] + c * profile[(i + 1) % n] for i in range(n)
    )


def _shrink(
    cells: Sequence[list[tuple[float, float]]], fallback: Sequence[float]
) -> tuple[float, ...]:
    out: list[float] = []
    for slot, samples in enumerate(cells):
        if not samples:
            out.append(fallback[slot])
            continue
        n = len(samples)
        out.append((n * _cell_mean(samples) + SHRINK_DAYS * fallback[slot]) / (n + SHRINK_DAYS))
    return tuple(out)


def _cold_start(
    base: TimeSeries, now: datetime, days: int, *, temperature_configured: bool
) -> LoadModel:
    term = TemperatureTerm(
        active=False, reason="too_few_days" if temperature_configured else "not_configured"
    )
    since = now - timedelta(days=COLD_START_RECENT_DAYS)
    recent = [v for begin, _, v in base.slots() if v is not None and since <= begin < now]
    if not recent:
        return LoadModel(quality=LoadQuality.NO_HISTORY, days=days, temperature=term)
    return LoadModel(
        quality=LoadQuality.COLD_START,
        days=days,
        flat_w=sum(recent) / len(recent),
        temperature=term,
    )


def _profiles(
    days: Sequence[DayValues], weights: Mapping[date, float], kinds: Mapping[date, DayType]
) -> dict[DayType, tuple[float, ...]]:
    pooled: list[list[tuple[float, float]]] = [[] for _ in range(SLOTS_PER_DAY)]
    by_type: dict[DayType, list[list[tuple[float, float]]]] = {
        t: [[] for _ in range(SLOTS_PER_DAY)] for t in DayType
    }
    for d in days:
        for slot, value in d.known():
            if kinds[d.day] is not DayType.HOLIDAY:  # holidays are special, not "any day"
                pooled[slot].append((value, weights[d.day]))
            by_type[kinds[d.day]][slot].append((value, weights[d.day]))
    pooled_mean = [_cell_mean(cell) if cell else math.nan for cell in pooled]
    known = [v for v in pooled_mean if not math.isnan(v)]
    level = sum(known) / len(known) if known else 0.0
    base_profile = tuple(level if math.isnan(v) else v for v in pooled_mean)
    profiles: dict[DayType, tuple[float, ...]] = {}
    profiles[DayType.WORKDAY] = _shrink(by_type[DayType.WORKDAY], base_profile)
    profiles[DayType.WEEKEND] = _shrink(by_type[DayType.WEEKEND], base_profile)
    profiles[DayType.HOLIDAY] = _shrink(by_type[DayType.HOLIDAY], profiles[DayType.WEEKEND])
    return {t: _smooth(p) for t, p in profiles.items()}


def _daily_temperatures(temperature: TimeSeries, tz: tzinfo, until: datetime) -> dict[date, float]:
    out: dict[date, float] = {}
    for d in day_values(temperature, tz, until):
        known = [v for _, v in d.known()]
        if known and d.coverage >= TEMPERATURE_DAY_COVERAGE:
            out[d.day] = sum(known) / len(known)
    return out


def _temperature_term(
    days: Sequence[DayValues],
    weights: Mapping[date, float],
    kinds: Mapping[date, DayType],
    profiles: Mapping[DayType, tuple[float, ...]],
    daily_temp: Mapping[date, float],
) -> TemperatureTerm:
    daily: list[tuple[float, float, float]] = []
    for d in days:
        if d.day not in daily_temp:
            continue
        pairs = d.known()
        profile = profiles[kinds[d.day]]
        residual = sum(v - profile[s] for s, v in pairs) / len(pairs)
        daily.append((residual, daily_temp[d.day], weights[d.day]))
    return fit_temperature(daily)


def _residuals(
    model: LoadModel,
    days: Sequence[DayValues],
    weights: Mapping[date, float],
    kinds: Mapping[date, DayType],
    daily_temp: Mapping[date, float],
) -> dict[SlotBlock, tuple[float, float]]:
    blocks: dict[SlotBlock, list[tuple[float, float]]] = {b: [] for b in SlotBlock}
    for d in days:
        temp = daily_temp.get(d.day)
        for slot, value in d.known():
            fitted = model.quantiles(slot, kinds[d.day], temp)
            assert fitted is not None
            blocks[slot_block(slot)].append((value - fitted[1], weights[d.day]))
    everything = [s for samples in blocks.values() for s in samples]
    residuals: dict[SlotBlock, tuple[float, float]] = {}
    for block, samples in blocks.items():
        use = samples if len(samples) >= RESIDUAL_MIN_SAMPLES else everything
        if not use:
            residuals[block] = (0.0, 0.0)
            continue
        values, ws = [v for v, _ in use], [w for _, w in use]
        q10 = weighted_quantile(values, ws, LOW_QUANTILE)
        q90 = weighted_quantile(values, ws, HIGH_QUANTILE)
        residuals[block] = (min(0.0, q10), max(0.0, q90))
    return residuals


def fit_load_model(
    base: TimeSeries,
    *,
    now: datetime,
    tz: tzinfo,
    day_types: Mapping[date, DayType],
    temperature: TimeSeries | None = None,
    temperature_configured: bool = False,
    half_life_days: float = HALF_LIFE_DAYS,
) -> LoadModel:
    """Fit the profiles (module doc) from the base load history up to ``now``.

    ``day_types`` maps history dates to their type (missing dates: weekday rule);
    ``temperature`` is the recorded outside temperature (°C, 15 minutes).
    """
    today = local_midnight(now, tz).astimezone(UTC)
    days = [d for d in day_values(base, tz, today) if d.coverage >= DAY_COVERAGE]
    if len(days) < COLD_START_MIN_DAYS:
        return _cold_start(base, now, len(days), temperature_configured=temperature_configured)
    today_date = now.astimezone(tz).date()
    weights = {d.day: recency_weight((today_date - d.day).days, half_life_days) for d in days}
    kinds = {d.day: day_types.get(d.day, weekday_rule(d.day)) for d in days}
    profiles = _profiles(days, weights, kinds)
    counts = {t: sum(1 for d in days if kinds[d.day] is t) for t in DayType}
    term = TemperatureTerm(active=False, reason="not_configured")
    daily_temp: dict[date, float] = {}
    if temperature_configured:
        if temperature is not None:
            daily_temp = _daily_temperatures(temperature, tz, today)
        term = _temperature_term(days, weights, kinds, profiles, daily_temp)
    model = LoadModel(
        quality=LoadQuality.OK,
        days=len(days),
        profiles=profiles,
        day_counts=counts,
        temperature=term,
    )
    residuals = _residuals(model, days, weights, kinds, daily_temp)
    return LoadModel(
        quality=LoadQuality.OK,
        days=len(days),
        profiles=profiles,
        day_counts=counts,
        residuals=residuals,
        temperature=term,
    )


def forecast_load(
    model: LoadModel,
    grid: Grid,
    tz: tzinfo,
    day_types: Mapping[date, DayType],
    daily_temperature: Mapping[date, float] | None = None,
) -> QuantileSeries | None:
    """P10/P50/P90 of the base load (W) on ``grid``; ``None`` without a model."""
    temps = daily_temperature or {}
    lows: list[float | None] = []
    mids: list[float | None] = []
    highs: list[float | None] = []
    for begin in grid.starts():
        day, slot = local_slot(begin, tz)
        q = model.quantiles(slot, day_types.get(day, weekday_rule(day)), temps.get(day))
        if q is None:
            return None
        lows.append(q[0])
        mids.append(q[1])
        highs.append(q[2])

    def series(values: list[float | None]) -> TimeSeries:
        return TimeSeries(start=grid.start, step=grid.step, values=tuple(values), unit=SeriesUnit.W)

    return QuantileSeries(p10=series(lows), p50=series(mids), p90=series(highs))


# --- Home Assistant shapes (parsed here, called by the adapter) ----------------------------


def temperature_c(value: object, unit: str | None) -> float | None:
    """A temperature in °C from a Home Assistant value and its unit (``°C``/``°F``).

    ``None`` for a missing/non-numeric value or an unknown unit (never guessed). A missing
    unit is read as °C (``UNVERIFIED``: the weather entity's ``temperature_unit`` attribute).
    """
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    if unit in (None, "°C", "C"):
        return number
    if unit in ("°F", "F"):
        return (number - 32.0) * 5.0 / 9.0
    return None


def parse_hourly_temperatures(
    response: Mapping[str, Any], entity_id: str, unit: str | None
) -> list[tuple[datetime, float]]:
    """``(time, °C)`` points of a ``weather.get_forecasts`` response for one entity.

    Shape (``docs/research/ha-websocket-api.md`` "Weather forecasts"):
    ``{entity_id: {"forecast": [{"datetime": iso, "temperature": float | null, ...}]}}``;
    only ``datetime`` is guaranteed. Items without a time zone, a parsable time or a
    temperature are skipped.
    """
    body = response.get(entity_id)
    items = body.get("forecast") if isinstance(body, Mapping) else None
    if not isinstance(items, list):
        return []
    points: list[tuple[datetime, float]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        raw = item.get("datetime")
        if not isinstance(raw, str):
            continue
        try:
            moment = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if moment.tzinfo is None or moment.utcoffset() is None:
            continue
        value = temperature_c(item.get("temperature"), unit)
        if value is not None:
            points.append((moment.astimezone(UTC), value))
    points.sort(key=lambda p: p[0])
    return points


def daily_mean_temperatures(
    points: Iterable[tuple[datetime, float]], tz: tzinfo, *, min_hours: int = MIN_TEMPERATURE_HOURS
) -> dict[date, float]:
    """Mean temperature per local date: points are averaged per local hour first (so a
    15-minute measurement and an hourly forecast weigh alike), then over the hours; a date with
    fewer than ``min_hours`` hours is left out."""
    hours: dict[tuple[date, int], list[float]] = {}
    for moment, value in points:
        local = moment.astimezone(tz)
        hours.setdefault((local.date(), local.hour), []).append(value)
    per_day: dict[date, list[float]] = {}
    for (day, _), values in hours.items():
        per_day.setdefault(day, []).append(sum(values) / len(values))
    return {d: sum(v) / len(v) for d, v in per_day.items() if len(v) >= min_hours}


def parse_check_date(response: Mapping[str, Any], entity_id: str) -> bool | None:
    """``workday.check_date`` response ``{entity_id: {"workday": bool}}`` -> the bool."""
    body = response.get(entity_id)
    value = body.get("workday") if isinstance(body, Mapping) else None
    return value if isinstance(value, bool) else None


def parse_workday_state(state: str, attributes: Mapping[str, Any]) -> bool | None:
    """The workday sensor's state for *today* (``on`` = workday); ``None`` if unknown or the
    sensor is shifted (``days_offset`` not 0: it describes another day)."""
    offset = attributes.get("days_offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset != 0:
        return None
    if state == "on":
        return True
    if state == "off":
        return False
    return None


__all__ = [
    "COLD_START_MIN_DAYS",
    "HALF_LIFE_DAYS",
    "HISTORY_DAYS",
    "LOAD_SOURCE_ID",
    "BaseLoad",
    "DayInfo",
    "DayType",
    "DayTypeSource",
    "DayValues",
    "LoadModel",
    "LoadQuality",
    "SlotBlock",
    "TemperatureReason",
    "TemperatureTerm",
    "base_load",
    "daily_mean_temperatures",
    "day_type_from_workday",
    "day_values",
    "degree_days",
    "fit_load_model",
    "fit_temperature",
    "forecast_load",
    "local_slot",
    "parse_check_date",
    "parse_hourly_temperatures",
    "parse_workday_state",
    "slot_block",
    "temperature_c",
    "weekday_rule",
]
