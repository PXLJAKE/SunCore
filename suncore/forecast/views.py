# SPDX-License-Identifier: Apache-2.0
"""Read models of the PV forecast and its accuracy (what the API serves; Phase 4 WP5).

Values are W; ``None`` is an unknown slot (never zero). ``status`` is a
:class:`~suncore.forecast.pv.adapter.FetchStatus` value, rendered by the UI as
``forecast.status.<value>``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from suncore.core.forecast import AccuracyRecord, HorizonBucket
from suncore.forecast.pv.adapter import FetchStatus

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
    15-minute intervals. A record with ``n`` below ``min_samples`` is too thin to rely on.
    """

    min_samples: int
    sources: list[AccuracySourceView]


__all__ = [
    "MAX_VIEW_HOURS",
    "AccuracySourceView",
    "BlendWeightsView",
    "BlendedView",
    "CalibrationView",
    "ForecastAccuracyView",
    "PvForecastView",
    "PvSeriesView",
    "PvSourceView",
]
