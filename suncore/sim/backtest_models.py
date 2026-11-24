# SPDX-License-Identifier: Apache-2.0
"""Request and result of a backtest (Phase 5 WP6, ADR-0014 decision 5).

These models are the wire format of ``/api/v1/backtests`` and the JSON stored in
``backtest_run``. They depend on ``core`` only. The request is an *overlay* on the configuration
of the moment: registry parameters (``planner``/``forecast`` group, tag ``whatif``; never a
``safety`` or controller parameter), a few battery settings and an optional flat tariff. Nothing
in a request is ever written back into the configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from suncore.core.battery import BatteryStrategy, GridChargePolicy
from suncore.core.decisions import Reason
from suncore.core.params import ParamError, ParamValue, check_whatif_overrides
from suncore.core.validation import invalid

BACKTEST_SCHEMA_VERSION: Final = 1
MAX_DAYS: Final = 30
"""Longest window of one run (plan §6 question 4)."""
DEFAULT_DAYS: Final = 14
MAX_PARAMS: Final = 20

Price = Annotated[float, Field(ge=-10, le=100, allow_inf_nan=False)]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class BatteryOverlay(_Model):
    """Battery settings to try; ``None`` keeps the configured value."""

    strategy: BatteryStrategy | None = None
    autarky_weight_per_kwh: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None = None
    grid_charge: GridChargePolicy | None = None
    capacity_wh: Annotated[float, Field(gt=0, le=1_000_000, allow_inf_nan=False)] | None = None
    max_charge_w: Annotated[float, Field(gt=0, le=1_000_000, allow_inf_nan=False)] | None = None
    max_discharge_w: Annotated[float, Field(gt=0, le=1_000_000, allow_inf_nan=False)] | None = None
    reserve_pct: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None = None


class TariffOverlay(_Model):
    """A flat tariff to try instead of the stored prices (currency/kWh)."""

    kind: Literal["fixed"] = "fixed"
    import_price: Price
    export_price: Price = 0.0


class BacktestRequest(_Model):
    """One run: local days ``start..end`` (inclusive) and what to change."""

    start: date
    end: date
    params: dict[str, ParamValue] = Field(default_factory=dict, max_length=MAX_PARAMS)
    """Registry overlay: ``planner``/``forecast`` parameters tagged ``whatif`` only."""
    battery: BatteryOverlay | None = None
    tariff: TariffOverlay | None = None
    perfect_foresight: bool = False
    """Also replay with the actual PV and load as forecast (the upper bound)."""

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.end < self.start:
            raise invalid("backtest_window_order", "backtest: end before start")
        days = (self.end - self.start).days + 1
        if days > MAX_DAYS:
            raise invalid("backtest_window_too_long", "backtest: at most {max} days", max=MAX_DAYS)
        try:
            checked = check_whatif_overrides(self.params)
        except ParamError as exc:
            raise exc.to_pydantic() from exc
        object.__setattr__(self, "params", dict(checked))
        return self

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


# --- result ----------------------------------------------------------------------------------


class RunFigures(_Model):
    """Energy (Wh), cost (currency) and KPIs of one run over the priced, replayed slots."""

    pv_wh: float
    house_wh: float
    import_wh: float
    export_wh: float
    charge_wh: float
    discharge_wh: float
    curtailed_wh: float = 0.0
    import_cost: float
    export_revenue: float
    cost_net: float
    autarky: float | None
    self_consumption: float | None
    equivalent_full_cycles: float | None


class DayResult(_Model):
    day: date
    slots: int
    """Replayed quarter hours with actual PV and load."""
    priced_slots: int
    actual: RunFigures
    whatif: RunFigures
    perfect: RunFigures | None = None
    baseline_no_pv_cost: float
    baseline_pv_only_cost: float


class SkippedDay(_Model):
    day: date
    reason: Reason


class HourlySeries(_Model):
    """Hourly means for the comparison chart (W, %); ``None`` = not replayed."""

    starts: list[datetime]
    actual_soc_pct: list[float | None]
    whatif_soc_pct: list[float | None]
    perfect_soc_pct: list[float | None] | None = None
    actual_grid_w: list[float | None]
    whatif_grid_w: list[float | None]
    actual_battery_w: list[float | None]
    whatif_battery_w: list[float | None]


class SolveStats(_Model):
    """Planner runs of the replay; times vary between machines and are not part of the
    deterministic result."""

    replans: int
    fallbacks: int
    """Runs that ended with the heuristic (solver unavailable, time limit, ...)."""
    solve_s_total: float
    solve_s_max: float


class BacktestTotals(_Model):
    days: int
    actual: RunFigures
    whatif: RunFigures
    perfect: RunFigures | None = None
    baseline_no_pv_cost: float
    baseline_pv_only_cost: float

    @property
    def savings_vs_actual(self) -> float:
        return self.actual.cost_net - self.whatif.cost_net


class BacktestResult(_Model):
    schema_version: int = BACKTEST_SCHEMA_VERSION
    label: Literal["plan_with_perfect_execution"] = "plan_with_perfect_execution"
    """ADR-0014: the what-if is the plan executed perfectly against the actual PV and load."""
    start: date
    end: date
    tz: str
    currency: str | None
    tariff: Literal["stored", "fixed"]
    params: dict[str, ParamValue]
    """The requested registry parameters with the values the run used (``budget_s`` capped)."""
    params_requested: dict[str, ParamValue] = Field(default_factory=dict)
    totals: BacktestTotals | None
    """``None`` when every day was skipped."""
    days: list[DayResult]
    skipped: list[SkippedDay]
    hourly: HourlySeries
    limitations: list[Reason]
    stats: SolveStats


# --- jobs ------------------------------------------------------------------------------------

BacktestStatus = Literal["running", "done", "failed", "cancelled"]


@dataclass(frozen=True, slots=True)
class BacktestJobView:
    """One run as the job API shows it (``result`` only when asked for and done)."""

    id: int
    status: BacktestStatus
    created_at: datetime
    finished_at: datetime | None
    start: date
    end: date
    request: BacktestRequest | None
    """``None`` if the stored request no longer validates (the registry changed)."""
    progress_done: int | None = None
    progress_total: int | None = None
    result: BacktestResult | None = None
    error: Reason | None = None
    """Why a run failed or was cancelled."""


class BacktestWindowError(ValueError):
    """The window is not entirely in the past."""


OVERLAY_INVALID: Final = "backtest_overlay_invalid"
OVERLAY_NO_BATTERY: Final = "backtest_overlay_no_battery"


class BacktestOverlayError(ValueError):
    """The overlay cannot be applied to the site (``key`` names why, ``api.error.<key>``)."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


class BacktestBusyError(RuntimeError):
    """Another run is active or the planner is running (one job at a time)."""


class BacktestUnavailableError(RuntimeError):
    """No time-series database or no site configuration: nothing to replay."""


__all__ = [
    "BACKTEST_SCHEMA_VERSION",
    "DEFAULT_DAYS",
    "MAX_DAYS",
    "OVERLAY_INVALID",
    "OVERLAY_NO_BATTERY",
    "BacktestBusyError",
    "BacktestJobView",
    "BacktestOverlayError",
    "BacktestRequest",
    "BacktestResult",
    "BacktestStatus",
    "BacktestTotals",
    "BacktestUnavailableError",
    "BacktestWindowError",
    "BatteryOverlay",
    "DayResult",
    "HourlySeries",
    "RunFigures",
    "SkippedDay",
    "SolveStats",
    "TariffOverlay",
]
