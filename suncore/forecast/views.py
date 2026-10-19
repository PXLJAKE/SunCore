# SPDX-License-Identifier: Apache-2.0
"""Read models of the PV forecast and its accuracy (what the API serves; Phase 4 WP5).

Values are W; ``None`` is an unknown slot (never zero). ``status`` is a
:class:`~suncore.forecast.pv.adapter.FetchStatus` value, rendered by the UI as
``forecast.status.<value>``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from suncore.core.forecast import AccuracyRecord

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
    status: str
    """Result of the latest attempt (``ok``, ``partial``, ``no_data``, ``error``, ...)."""
    fetched_at: AwareDatetime | None
    """When the source last delivered data; ``None`` = never."""
    age_s: float | None
    stale: bool
    """No data yet, or older than three refresh intervals."""
    filled_slots: int
    """Slots taken from the previous good fetch because the latest one lacked them."""
    series: PvSeriesView | None


class BlendedView(_View):
    """The forecast the planner would use.

    **Placeholder until the blending of part B**: ``method`` is ``first_source_p50`` - the P50
    of the first configured source that has data, no blending, no bias calibration.
    """

    method: Literal["first_source_p50"]
    source_id: str
    start: AwareDatetime
    step_s: Annotated[int, Field(gt=0)]
    p50_w: list[float | None]


class PvForecastView(_View):
    generated_at: AwareDatetime
    hours: int
    sources: list[PvSourceView]
    blended: BlendedView | None


class AccuracySourceView(_View):
    """Measured accuracy of one source; ``records`` only holds buckets with data."""

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
    "BlendedView",
    "ForecastAccuracyView",
    "PvForecastView",
    "PvSeriesView",
    "PvSourceView",
]
