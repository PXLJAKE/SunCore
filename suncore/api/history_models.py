# SPDX-License-Identifier: Apache-2.0
"""Wire models of the history, plan-at/compare and decision-explain endpoints (Phase 5 WP4).

Energies are Wh, powers W, costs in the region currency (``currency``); signs follow
``core/units.py``. ``None`` means "not known" (no stored price, no reading), never zero.
Times carry the site's UTC offset.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from suncore.analysis.period import Granularity
from suncore.core.control import Plan, PlanInterval
from suncore.core.decisions import Decision


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- summary, sankey ---------------------------------------------------------------------


class EnergyOut(_Out):
    pv_wh: float
    house_wh: float
    import_wh: float
    export_wh: float
    charge_wh: float
    discharge_wh: float


class FlowBreakdownOut(_Out):
    """Attributed flows (ADR-0014 decision 2): PV serves the house, then the battery, then the
    grid; the grid charges the battery only for what PV cannot."""

    pv_to_house_wh: float
    pv_to_battery_wh: float
    pv_to_grid_wh: float
    battery_to_house_wh: float
    battery_to_grid_wh: float
    grid_to_house_wh: float
    grid_to_battery_wh: float
    residual_wh: float


class KpiOut(_Out):
    autarky: float | None
    """Share of the house load not drawn from the grid, 0..1; ``None`` without load."""
    self_consumption: float | None
    """Share of the PV energy used on site (house or battery), 0..1; ``None`` without PV."""
    equivalent_full_cycles: float | None
    """Battery throughput over usable capacity; ``None`` without a battery model."""


class CostOut(_Out):
    """Grid cost of the days that have stored prices, and the two baselines (ADR-0014).

    ``savings_vs_no_pv`` is what the plant adds (house load entirely from the grid minus actual
    cost), ``savings_vs_pv_only`` what SunCore adds on top of the plant (PV without battery and
    control minus actual cost), ``pv_contribution`` the difference. Device shifting is not undone.
    """

    currency: str | None
    priced_days: int
    """Days that contribute (every figure sums only these)."""
    import_cost: float | None
    export_revenue: float | None
    cost_net: float | None
    baseline_no_pv_cost: float | None
    baseline_pv_only_cost: float | None
    savings_vs_no_pv: float | None
    savings_vs_pv_only: float | None
    pv_contribution: float | None


class BucketOut(_Out):
    """One bar of the period chart: a day (week, month) or a month (year)."""

    key: str
    start: AwareDatetime
    end: AwareDatetime
    days_with_data: int
    complete: bool
    live: bool
    """Contains a day computed just now (not yet frozen)."""
    energy: EnergyOut
    kpis: KpiOut
    cost: CostOut


class SummaryOut(_Out):
    period: Granularity
    key: str
    """``2026-03-29``, ``2026-W13``, ``2026-03`` or ``2026``."""
    start: AwareDatetime
    end: AwareDatetime
    tz: str
    days: int
    """Local days of the period up to and including today."""
    days_with_data: int
    days_complete: int
    complete: bool
    """Every day has data and enough of it (``MIN_COVERAGE``)."""
    in_progress: bool
    """The period has not ended yet."""
    includes_live: bool
    coverage: float | None
    """Mean measured share of the days with data, 0..1."""
    derived_share: float | None
    """Share of the measured slots that were rebuilt from Home Assistant statistics."""
    energy: EnergyOut
    flows: FlowBreakdownOut
    kpis: KpiOut
    cost: CostOut
    buckets: list[BucketOut]


SankeyNode = Literal[
    "pv", "grid_import", "battery_discharge", "house", "battery_charge", "grid_export"
]


class SankeyLinkOut(_Out):
    source: SankeyNode
    target: SankeyNode
    wh: float


class SankeyOut(_Out):
    period: Granularity
    key: str
    start: AwareDatetime
    end: AwareDatetime
    complete: bool
    nodes: list[SankeyNode]
    links: list[SankeyLinkOut]
    """All seven attributed flows (also zero ones) in a fixed order."""
    residual_wh: float


# --- series, heatmap, cycles, forecast skill, devices ----------------------------------------


SeriesResolution = Literal["1m", "15m", "1h", "1d"]


class SeriesOut(_Out):
    key: str
    unit: str
    """``W``, ``%``, ``°C`` or ``share`` (the on/off state: share of the time on)."""
    values: list[float | None]
    """Mean per step, aligned with ``SeriesResponse.times``; ``None`` = no data."""


class SeriesResponse(_Out):
    start: AwareDatetime
    end: AwareDatetime
    resolution: SeriesResolution
    resolution_reason: str
    """``span`` (the rule by span), ``points`` (coarsened to stay within the point cap),
    ``retention`` (no 1-minute data that old) or ``requested``."""
    times: list[AwareDatetime]
    """Start of every step."""
    series: list[SeriesOut]


class HeatmapOut(_Out):
    key: str
    unit: str
    start: AwareDatetime
    end: AwareDatetime
    tz: str
    weekdays: list[str] = Field(
        default_factory=lambda: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    )
    """Row order of ``values`` (ISO weekdays, Monday first)."""
    values: list[list[float | None]]
    """7 x 24: mean per local weekday and hour of day; ``None`` without data."""
    counts: list[list[int]]
    """Quarter-hour samples behind every cell."""


class CycleDayOut(_Out):
    day: date
    equivalent_full_cycles: float | None
    charge_wh: float
    discharge_wh: float
    soc_min_pct: float | None
    soc_max_pct: float | None
    complete: bool


class CyclesOut(_Out):
    start: date
    end: date
    total_equivalent_full_cycles: float | None
    days: list[CycleDayOut]


class SkillPointOut(_Out):
    day: date
    mae_w: float
    bias_w: float
    n: int


class SkillSeriesOut(_Out):
    source_id: str
    kind: Literal["pv", "load"]
    horizon: str
    mae_w: float | None
    """Mean absolute error over the range, weighted by ``n``."""
    bias_w: float | None
    """Mean error (actual - forecast) over the range, weighted by ``n``."""
    n: int
    points: list[SkillPointOut]


class ForecastSkillOut(_Out):
    start: date
    end: date
    series: list[SkillSeriesOut]


class DeviceDayOut(_Out):
    day: date
    run_hours: float | None
    energy_wh: float | None


class DeviceHistoryItemOut(_Out):
    device_id: str
    name: str
    run_hours: float | None
    """Time the device was on (from the recorded on/off state), ``None`` without records."""
    energy_wh: float | None
    """Estimated energy from the recorded power estimate."""
    starts: int | None
    """Off-to-on transitions at quarter-hour resolution (shorter cycles are not seen)."""
    days: list[DeviceDayOut]


class DeviceHistoryOut(_Out):
    period: Granularity
    key: str
    start: AwareDatetime
    end: AwareDatetime
    devices: list[DeviceHistoryItemOut]


# --- plan at / compare -------------------------------------------------------------------------


class PlanAtOut(_Out):
    """The plan snapshot that was valid at a time."""

    snapshot_id: int
    created_at: AwareDatetime
    source: str
    schema_version: int
    at: AwareDatetime
    interval_index: int | None
    """Index into ``plan.intervals`` of the interval that covers ``at``, if any."""
    plan: Plan


class SnapshotRefOut(_Out):
    snapshot_id: int
    created_at: AwareDatetime
    source: str
    intervals: int


class IntervalDiffOut(_Out):
    """Differences of one interval start; a side without the interval is ``None``.

    Deltas are ``b - a`` and only present where both sides have the value."""

    start: AwareDatetime
    end: AwareDatetime
    only_in: Literal["a", "b"] | None
    a: PlanInterval | None
    b: PlanInterval | None
    battery_w_delta: float | None
    soc_pct_delta: float | None
    import_w_delta: float | None
    export_w_delta: float | None
    devices_w_delta: float | None
    import_price_delta: float | None
    devices_changed: list[str]
    """Devices whose allowed/preference/max power differ."""


class PlanCompareOut(_Out):
    a: SnapshotRefOut
    b: SnapshotRefOut
    changed: int
    intervals: list[IntervalDiffOut]
    """Only intervals that differ or exist on one side, in time order."""


# --- decision explain --------------------------------------------------------------------------


class DecisionEntryOut(_Out):
    id: int
    decision: Decision


class ExplainInputOut(_Out):
    key: str
    value: float | None
    unit: str


class ExplainPlanOut(_Out):
    snapshot_id: int
    created_at: AwareDatetime
    source: str
    interval_index: int | None
    interval: PlanInterval | None
    """The plan interval that covered the decision's time (device band, expected flows)."""


class DecisionExplainOut(_Out):
    """Everything the "Why?" panel shows about one decision."""

    id: int
    decision: Decision
    before: list[DecisionEntryOut]
    """Earlier decisions of the same device, oldest first."""
    after: list[DecisionEntryOut]
    plan: ExplainPlanOut | None
    """``None`` when no snapshot from before the decision is stored any more."""
    inputs: list[ExplainInputOut]
    """Site readings of the quarter hour around the decision (``None`` value: no data)."""
    inputs_at: AwareDatetime | None
    inputs_resolution: Literal["1m", "15m"] | None
