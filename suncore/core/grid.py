# SPDX-License-Identifier: Apache-2.0
"""One resolver for every grid limit (ADR-0018 decision 1).

Planner, controller and gate compute the effective import limit, the effective export limit
and the §14a budget only through the pure functions here, so the three layers cannot drift
(the reserve lesson of ADR-0013). The configuration pieces that are not entity references
(peak shaving, backup) are defined here as well and embedded in ``core.site.GridConfig``.
Imports nothing from sibling modules but the validation helper (plan WP1 boundary rule).

Signs and units: W, import and export limits are magnitudes (>= 0).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from enum import StrEnum, unique
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from suncore.core.validation import ENTITY_ID_PATTERN, invalid

PARA14A_DEFAULT_LIMIT_W: Final = 4200.0
"""§14a EnWG: minimum power per controllable device with direct control (BNetzA). With
several devices behind an EMS the operator's value applies (formula ``UNVERIFIED``)."""

DEVICE_ID_PATTERN: Final = r"^[a-z][a-z0-9_]{0,47}$"
"""Same as ``core.devices.DEVICE_ID_PATTERN`` (no import, boundary rule)."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


@unique
class GridState(StrEnum):
    """Connection state of the site (ADR-0018 decision 6)."""

    CONNECTED = "connected"
    ISLAND = "island"
    """Backup power: the grid is gone, the house runs from PV and battery."""
    UNKNOWN = "unknown"
    """A backup entity is configured but unavailable or not in the cache: not island (no island
    rules), with a warning (ADR-0018 decision 6, from 6-F)."""
    NOT_CONFIGURED = "not_configured"
    """No backup entity: the site is grid-connected as far as SunCore can know; no warning."""


class PeakShaving(_Frozen):
    """Monthly peak shaving (capacity tariffs). Off by default (plan §0 decision 12)."""

    enabled: bool = False
    peak_floor_w: float = Field(default=2500.0, ge=0, le=1_000_000, allow_inf_nan=False)
    """The import limit never goes below this, even early in the month."""
    capacity_price_per_kw_month: float = Field(default=0.0, ge=0, le=1000, allow_inf_nan=False)
    """Currency per kW of monthly peak; the planner's peak variable cost (WP8)."""


class BackupConfig(_Frozen):
    """Backup power: the entity that shows island operation (entity only, no heuristic).

    ``island_states`` are the state values that mean "island" (case-sensitive, as HA reports
    them; ``UNVERIFIED`` per backup device - the user or the template names them).
    ``keep_devices`` are device ids that are not shed in island operation.
    """

    entity_id: Annotated[str, StringConstraints(pattern=ENTITY_ID_PATTERN)]
    island_states: Annotated[
        tuple[Annotated[str, StringConstraints(min_length=1, max_length=64)], ...],
        Field(min_length=1, max_length=8),
    ]
    keep_devices: Annotated[
        tuple[Annotated[str, StringConstraints(pattern=DEVICE_ID_PATTERN)], ...],
        Field(max_length=64),
    ] = ()

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if set(self.island_states) & {"unavailable", "unknown"}:
            raise invalid(
                "backup_unavailable_state",
                "backup: `unavailable`/`unknown` cannot mean island (unknown = connected)",
            )
        if len(set(self.island_states)) != len(self.island_states):
            raise invalid("backup_states_unique", "backup: island states must be unique")
        if len(set(self.keep_devices)) != len(self.keep_devices):
            raise invalid("backup_keep_unique", "backup: keep devices must be unique")
        return self


UNAVAILABLE_STATES: Final = frozenset({"unavailable", "unknown", ""})


def grid_state_from(backup: BackupConfig | None, entity_state: str | None) -> GridState:
    """The grid state from the backup entity's state (``None`` = not in the cache)."""
    if backup is None:
        return GridState.NOT_CONFIGURED
    if entity_state is None or entity_state in UNAVAILABLE_STATES:
        return GridState.UNKNOWN
    return GridState.ISLAND if entity_state in backup.island_states else GridState.CONNECTED


# --- import side ----------------------------------------------------------------------------


def peak_limit_w(peak: PeakShaving, month_peak_w: float | None) -> float | None:
    """Peak-shaving import limit: max(month's highest 15-min mean import, floor); ``None`` = off."""
    if not peak.enabled:
        return None
    return max(month_peak_w or 0.0, peak.peak_floor_w)


def effective_import_limit_w(
    fuse_limit_w: float | None, peak: PeakShaving, month_peak_w: float | None
) -> float | None:
    """min(fuse limit, peak limit); never above the fuse limit; ``None`` = no limit at all."""
    candidates = [v for v in (fuse_limit_w, peak_limit_w(peak, month_peak_w)) if v is not None]
    return min(candidates) if candidates else None


def updated_month_peak_w(month_peak_w: float | None, quarter_mean_import_w: float) -> float:
    """The month's peak after one more 15-minute mean (import magnitude, export counts as 0)."""
    return max(month_peak_w or 0.0, 0.0, quarter_mean_import_w)


def month_key(at: datetime, tz: tzinfo) -> str:
    """``YYYY-MM`` of the local month ``at`` lies in (peaks reset at local midnight)."""
    if at.tzinfo is None:
        msg = "at must be timezone-aware"
        raise ValueError(msg)
    return at.astimezone(tz).strftime("%Y-%m")


def month_bounds(at: datetime, tz: tzinfo) -> tuple[datetime, datetime]:
    """Absolute ``[start, end)`` (UTC) of the local month containing ``at`` (DST-safe)."""
    local = at.astimezone(tz)
    start = datetime(local.year, local.month, 1, tzinfo=tz)
    year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
    end = datetime(year, month, 1, tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


# --- export side ----------------------------------------------------------------------------


def static_export_limit_w(
    export_limit_w: float | None, export_limit_pct_kwp: float | None, total_kwp: float | None
) -> float | None:
    """The configured static export limit in W (``None`` = no limit configured)."""
    candidates: list[float] = []
    if export_limit_w is not None:
        candidates.append(export_limit_w)
    if export_limit_pct_kwp is not None and total_kwp is not None:
        candidates.append(export_limit_pct_kwp / 100.0 * total_kwp * 1000.0)
    return min(candidates) if candidates else None


def effective_export_limit_w(
    static_limit_w: float | None,
    *,
    zero_export: bool,
    export_dynamic: bool,
    export_price: float | None,
    adapter_verified: bool,
) -> float | None:
    """The export limit that applies now (ADR-0018 decisions 3 and 4); never above static.

    Zero export -> 0 W. Dynamic: 0 W while the export price is negative, only with a verified
    inverter adapter (otherwise the limit could not be enforced and the static one applies).
    """
    if zero_export:
        return 0.0
    if export_dynamic and adapter_verified and export_price is not None and export_price < 0:
        return 0.0
    return static_limit_w


# --- §14a -----------------------------------------------------------------------------------

Para14aSignalKind = Literal["binary", "power_w"]


@unique
class Para14aStatus(StrEnum):
    INACTIVE = "inactive"
    ACTIVE = "active"
    UNKNOWN = "unknown"
    """The signal is configured but unavailable: no increase of flagged devices (G5)."""


@dataclass(frozen=True, slots=True)
class Para14aBudget:
    """The §14a situation: ``budget_w`` is the summed power the flagged devices may draw."""

    status: Para14aStatus
    budget_w: float | None = None
    """``None`` while inactive; while unknown the flagged devices are frozen (no increase)."""


PARA14A_INACTIVE: Final = Para14aBudget(Para14aStatus.INACTIVE)

_ON_STATES: Final = frozenset({"on", "true", "1", "active"})
_OFF_STATES: Final = frozenset({"off", "false", "0", "inactive"})


def para14a_budget_w(  # noqa: PLR0911 - one return per signal state
    enabled: bool,
    signal_kind: Para14aSignalKind,
    signal_state: str | None,
    limit_w: float,
) -> Para14aBudget:
    """The §14a budget from the signal (ADR-0018 decision 5).

    ``binary``: ``on`` = dimmed to ``limit_w``, ``off`` = not dimmed. ``power_w``: the entity
    carries the allowed power in W while dimmed and 0 when not dimmed (convention of this
    contract; how a control box reports it is ``UNVERIFIED``); the budget is the more
    conservative of the signalled value and ``limit_w``. Unavailable or unparsable ->
    ``unknown`` (G5: no increase of flagged devices).
    """
    if not enabled:
        return PARA14A_INACTIVE
    if signal_state is None or signal_state in UNAVAILABLE_STATES:
        return Para14aBudget(Para14aStatus.UNKNOWN)
    if signal_kind == "binary":
        value = signal_state.strip().lower()
        if value in _ON_STATES:
            return Para14aBudget(Para14aStatus.ACTIVE, limit_w)
        if value in _OFF_STATES:
            return PARA14A_INACTIVE
        return Para14aBudget(Para14aStatus.UNKNOWN)
    try:
        allowed = float(signal_state)
    except ValueError:
        return Para14aBudget(Para14aStatus.UNKNOWN)
    if not math.isfinite(allowed) or allowed < 0:
        return Para14aBudget(Para14aStatus.UNKNOWN)
    if allowed == 0:
        return PARA14A_INACTIVE
    return Para14aBudget(Para14aStatus.ACTIVE, min(allowed, limit_w))


__all__ = [
    "PARA14A_DEFAULT_LIMIT_W",
    "PARA14A_INACTIVE",
    "BackupConfig",
    "GridState",
    "Para14aBudget",
    "Para14aSignalKind",
    "Para14aStatus",
    "PeakShaving",
    "effective_export_limit_w",
    "effective_import_limit_w",
    "grid_state_from",
    "month_bounds",
    "month_key",
    "para14a_budget_w",
    "peak_limit_w",
    "static_export_limit_w",
    "updated_month_peak_w",
]
