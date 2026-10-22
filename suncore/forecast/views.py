# SPDX-License-Identifier: Apache-2.0
"""Read models of the PV and load forecasts and their accuracy (what the API serves; Phase 4
WP5/WP6).

Values are W; ``None`` is an unknown slot (never zero). ``status`` is a
:class:`~suncore.forecast.pv.adapter.FetchStatus` value, rendered by the UI as
``forecast.status.<value>``.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from suncore.core.battery import BatteryStrategy, RiskLevel
from suncore.core.decisions import Reason
from suncore.core.forecast import AccuracyRecord, HorizonBucket
from suncore.forecast.load import DayType, DayTypeSource, LoadQuality, TemperatureReason
from suncore.forecast.pv.adapter import FetchStatus
from suncore.forecast.risk import DistributionSource, RiskClass, RiskWindow

MAX_VIEW_HOURS = 96
"""Largest ``hours`` the API serves."""


class _View(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PvSeriesView(_View):
    """P10/P50/P90 in W on one regular grid: ``p50_w[i]`` covers ``[start + i*step_s, ...)``."""

    start: AwareDatetime
    step_s: Annotated[int, Field(gt=0)]
    p10_w: list[float | None]
    p50_w: list[float | None]
    p90_w: list[float | None]


class PvSourceView(_View):
    """One source: its latest good series and how fresh it is."""

    source_id: str
    status: FetchStatus
    """Result of the latest attempt (``ok``, ``partial``, ``no_data``, ``error``, ...)."""
    fetched_at: AwareDatetime | None
    """When the source last delivered data; ``None`` = never."""
    age_s: float | None
    stale: bool
    """No data yet, or older than three refresh intervals."""
    filled_slots: int
    """Slots taken from the previous good fetch because the latest one lacked them."""
    series: PvSeriesView | None


class BlendWeightsView(_View):
    """The weights of one horizon bucket (before the per-slot renormalisation over the sources
    that have a value in a slot)."""

    horizon: HorizonBucket
    basis: Literal["inverse_mae", "equal_no_history"] | None
    """How the auto-weighted sources were weighted: by the inverse of their 14-day MAE, or
    equally because a source lacks enough accuracy history; ``None`` = all weights manual."""
    manual: bool
    """A manually weighted source took part."""
    weights: dict[str, float]
    """Source id to weight; the weights sum to 1."""


class CalibrationView(_View):
    """The bias calibration of one source.

    ``reason``: ``active`` (applied), ``collecting`` (learnt and stored but not applied before
    ``min_history_days`` days with data), ``no_history`` (nothing learnt yet),
    ``classifier_changed`` (learnt with the other weather classifier; waits).
    """

    source_id: str
    active: bool
    reason: Literal["active", "collecting", "no_history", "classifier_changed"]
    days_with_data: int
    min_history_days: int
    cells: int
    """Cells (hour x season x weather class) with samples."""
    factor_min: float | None
    factor_max: float | None


class BlendedView(_View):
    """The forecast the planner uses: the sources on the 15-minute grid, bias-calibrated where
    the calibration is active, blended by measured skill (P10/P50/P90 in W).

    ``uncalibrated_p50_w`` is the blend of the sources as they said it; ``calibrated_p50_w`` the
    blend with every learnt calibration applied, active or not (``None`` while no source has
    learnt anything), so the effect can be compared before it switches on.
    """

    method: Literal["weighted", "single_source"]
    sources: list[str]
    start: AwareDatetime
    step_s: Annotated[int, Field(gt=0)]
    p10_w: list[float | None]
    p50_w: list[float | None]
    p90_w: list[float | None]
    uncalibrated_p50_w: list[float | None]
    calibrated_p50_w: list[float | None] | None
    calibration_active: bool
    """At least one source's calibration is applied."""
    stale: bool
    """Every source's last good fetch is older than 12 h; the blend uses them anyway."""
    excluded_sources: list[str]
    """Sources left out of the blend: older than 12 h while a fresher source has data."""
    weights: list[BlendWeightsView]
    calibration: list[CalibrationView]


class PvForecastView(_View):
    generated_at: AwareDatetime
    hours: int
    sources: list[PvSourceView]
    blended: BlendedView | None


class AccuracySourceView(_View):
    """Measured accuracy of one source; ``records`` only holds buckets with data.

    The source id ``blended`` is the blended forecast the planner used.
    """

    source_id: str
    computed_at: AwareDatetime | None
    window_days: int | None
    records: list[AccuracyRecord]


class ForecastAccuracyView(_View):
    """Accuracy per source and horizon bucket.

    ``bias_w = actual - forecast`` (positive = the source under-forecasts); ``n`` counts
    15-minute daylight intervals (forecast or measurement above 0 W; clipped ones excluded).
    A record with ``n`` below ``min_samples`` is too thin to rely on.
    """

    min_samples: int
    sources: list[AccuracySourceView]


class LoadDayView(_View):
    """The day type (and forecast mean temperature) of one local date of the forecast."""

    date: date
    day_type: DayType
    source: DayTypeSource
    """``workday_sensor`` (today's state), ``check_date`` (the Workday action) or
    ``weekday_rule`` (Mon-Fri workday, Sat/Sun weekend)."""
    temperature_c: float | None
    """Mean outside temperature of the day the temperature term used (``None``: none)."""


class LoadTemperatureView(_View):
    """The temperature term of the load profile.

    ``reason``: ``active``, ``not_configured`` (no weather entity), ``too_few_days`` (fewer
    than 14 days with a recorded temperature) or ``no_spread`` (the temperatures varied too
    little to learn anything)."""

    active: bool
    reason: TemperatureReason
    days: int
    heating_w_per_k: float
    """Extra base load (W) per kelvin of heating degrees (below 15 °C)."""
    cooling_w_per_k: float
    """Extra base load (W) per kelvin of cooling degrees (above 22 °C)."""


class LoadForecastView(_View):
    """The base load forecast (W, P10/P50/P90 on the 15-minute grid from the current slot).

    The base load is the house consumption without the configured (controllable) devices; their
    draw is planned separately. ``quality``: ``ok`` (profiles from history), ``cold_start``
    (fewer than 3 days: a flat line at the recent mean with a wide band) or ``no_history``
    (nothing measured yet: no forecast)."""

    generated_at: AwareDatetime
    hours: int
    source_id: str
    quality: LoadQuality
    days_of_history: int
    """Complete local days the profiles were learnt from."""
    fitted_at: AwareDatetime | None
    start: AwareDatetime | None
    step_s: Annotated[int, Field(gt=0)]
    p10_w: list[float | None]
    p50_w: list[float | None]
    p90_w: list[float | None]
    days: list[LoadDayView]
    temperature: LoadTemperatureView
    controlled_devices: list[str]
    """Devices subtracted from the house consumption (forecast by the planner, not here)."""
    devices_unmeasured: dict[str, int]
    """Per device the quarter hours of the fit window without a measurement (counted as 0 W:
    the base load still contains its draw there, e.g. in the history rebuilt from Home
    Assistant's statistics)."""
    clamped_slots: int
    """Quarter hours where the devices drew more than the house measured (clipped to 0 W)."""
    unmeasured_in_profile: list[DayType]
    """Day types whose profile still contains quarter hours with an unmeasured device (fewer
    than 7 fully measured days of that type; elsewhere those quarter hours are left out)."""


class EnergyView(_View):
    """P10/P50/P90 energy (Wh) of a window (the slot quantiles summed)."""

    p10_wh: float
    p50_wh: float
    p90_wh: float


class RiskWindowView(_View):
    """One window of the risk model and its net energy (PV - base load) at the risk quantile."""

    window: RiskWindow
    start: AwareDatetime
    end: AwareDatetime
    weather: RiskClass
    pv: EnergyView
    load: EnergyView
    net_p50_wh: float
    net_quantile_wh: float
    """Net energy that is reached with the risk level's probability."""
    source: DistributionSource
    samples: int
    """Learnt samples behind ``net_quantile_wh`` (0 for ``source_quantiles``)."""
    pooled: bool
    """Learnt over all weather classes of the window (its own class had too few samples)."""


class RiskTargetView(_View):
    """A computed state of charge with its structured reason."""

    soc_pct: float
    reason: Reason
    source: DistributionSource


class RiskView(_View):
    """The current morning buffer and evening target and what they rest on (Phase 4 WP7).

    ``status``: ``ok``; ``no_battery_model`` (the ``battery`` section has no model yet: the
    windows are shown, the targets are ``null``); ``no_forecast`` (no load forecast yet).
    ``distribution_source``: ``learned`` once ``min_history_days`` days of measured net errors
    exist, else ``source_quantiles`` (the PV sources' P10/P90 and the load residuals).
    """

    generated_at: AwareDatetime
    status: Literal["ok", "no_battery_model", "no_forecast"]
    risk: RiskLevel
    quantile: float
    strategy: BatteryStrategy
    bridge_next_day: bool
    pv_forecast: bool
    """A PV forecast was available (without one PV counts as 0 Wh: the cautious direction)."""
    distribution_source: DistributionSource
    history_days: int
    """Days with learnt net-error samples."""
    min_history_days: int
    samples: dict[str, int]
    """Learnt samples per ``window/weather`` cell."""
    evening_at: AwareDatetime | None
    """Start of the ``night`` window: today's sunset (the time the evening target refers to);
    after sunset the current quarter hour (the evening has begun)."""
    windows: list[RiskWindowView]
    morning_buffer: RiskTargetView | None
    evening_target: RiskTargetView | None


__all__ = [
    "MAX_VIEW_HOURS",
    "AccuracySourceView",
    "BlendWeightsView",
    "BlendedView",
    "CalibrationView",
    "EnergyView",
    "ForecastAccuracyView",
    "LoadDayView",
    "LoadForecastView",
    "LoadTemperatureView",
    "PvForecastView",
    "PvSeriesView",
    "PvSourceView",
    "RiskTargetView",
    "RiskView",
    "RiskWindowView",
]
