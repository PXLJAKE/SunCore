# SPDX-License-Identifier: Apache-2.0
"""Grid invariants of the import side, §14a and island (plan WP6, ADR-0018; PR 6-F).

Pure functions over plain values; :mod:`suncore.safety.invariants` (devices) and
:mod:`suncore.safety.battery` (the battery) call them with their own context, so both paths
judge the same rules. Imports ``core`` only (no cycle with the two callers).

* G1 - every load increase keeps import <= the effective import limit (fuse and peak):
  ``invariants.check_grid`` and battery B5, judged with ``GateInputs.import_limit_w`` =
  ``core.grid.effective_import_limit_w``.
* G2 - reductions pass; above the **fuse** limit min run and the switch limit are waived for
  non-thermal kinds: :func:`fuse_shed_waiver` in ``check_min_run_pause``/``check_switch_rate``.
* G3 - while §14a is active the flagged devices together stay <= the budget, no battery grid
  charging: :func:`check_para14a`, :func:`check_battery_grid`.
* G4 - island: every load increase and every battery state that can cause import is rejected:
  :func:`check_island`, :func:`check_battery_grid`.
* G5 - unknown limit inputs (stale grid power, unknown §14a signal, stale snapshot): no
  increase: :func:`check_grid_inputs`, :func:`check_para14a`.
* G6 - after island or §14a, increases wait ``safe_state_cooldown_s``:
  :func:`check_grid_cooldown`, :func:`check_battery_grid`.

A ``ReleaseAction`` is never judged here: "back to normal" restores the user's own state and
must work from any state (BRIEF §5), also in island.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from suncore.core.balance import FlowSnapshot
from suncore.core.decisions import Reason
from suncore.core.devices import THERMAL_KINDS, DeviceKind
from suncore.core.grid import GridState, Para14aStatus
from suncore.core.signals import Quality

POWER_TOLERANCE_W: Final = 1.0

REASON_ISLAND_NO_INCREASE: Final = Reason(key="safety.island_no_increase")
REASON_ISLAND_BATTERY: Final = Reason(key="safety.island_battery")
REASON_PARA14A_UNKNOWN: Final = Reason(key="safety.para14a_unknown")
REASON_PARA14A_GRID_CHARGE: Final = Reason(key="safety.para14a_grid_charge")
REASON_GRID_UNKNOWN: Final = Reason(key="safety.grid_unknown")


@dataclass(frozen=True, slots=True)
class GridSafety:
    """The grid situation one intent is judged with (the gate builds it per intent).

    The defaults are the Phase 3 situation (no island, no §14a, no cool-down), so contexts
    built without it judge exactly as before.
    """

    grid_state: GridState = GridState.NOT_CONFIGURED
    para14a_status: Para14aStatus = Para14aStatus.INACTIVE
    para14a_budget_w: float | None = None
    para14a_others_w: float | None = 0.0
    """Upper power of the *other* §14a devices plus the increases already let through against
    the same snapshot; ``None`` = one of them is unknown (no increase while dimmed)."""
    island_hold_s: float = 0.0
    """Remaining cool-down after island (``inf`` while island or unknown after island)."""
    para14a_hold_s: float = 0.0
    """Remaining cool-down after §14a dimming ended."""
    fuse_limit_w: float | None = None
    """The connection/fuse import limit (``GridConfig.import_limit_w``), not the peak limit."""
    inputs_fresh: bool = True
    """The snapshot is newer than ``inputs_max_age_ticks`` ticks."""


NO_GRID_SAFETY: Final = GridSafety()


def _remaining(hold_s: float) -> int:
    return 3600 if math.isinf(hold_s) else math.ceil(hold_s)


# --- devices ----------------------------------------------------------------------------------


def check_grid_inputs(
    increase: bool, flows: FlowSnapshot | None, grid: GridSafety
) -> Reason | None:
    """G5: no load increase on a stale snapshot or with a grid reading that is not ``OK``.

    Without any flows (unit contexts) the surplus rule of ``check_grid`` decides alone: an
    unknown surplus already rejects every increase.
    """
    if not increase:
        return None
    if not grid.inputs_fresh:
        return REASON_GRID_UNKNOWN
    if flows is None:
        return None
    reading = flows.grid_w
    if reading.value is None or reading.quality is not Quality.OK:
        return REASON_GRID_UNKNOWN
    return None


def check_island(increase: bool, grid: GridSafety) -> Reason | None:
    """G4: in island operation no load increase at all (the battery and PV carry the house)."""
    if increase and grid.grid_state is GridState.ISLAND:
        return REASON_ISLAND_NO_INCREASE
    return None


def check_para14a(
    flagged: bool, increase: bool, after_w: float | None, grid: GridSafety
) -> Reason | None:
    """G3 + G5: a §14a device is raised only while the summed power stays within the budget.

    ``after_w`` is the device's expected power after the action (``None`` = unknown). An unknown
    signal, an unknown budget, an unknown power of another flagged device or an unknown
    ``after_w`` reject every increase of a flagged device (the conservative direction).
    """
    if not flagged or not increase:
        return None
    status = grid.para14a_status
    if status is Para14aStatus.INACTIVE:
        return None
    if status is Para14aStatus.UNKNOWN:
        return REASON_PARA14A_UNKNOWN
    budget, others = grid.para14a_budget_w, grid.para14a_others_w
    if budget is None or others is None or after_w is None:
        return REASON_PARA14A_UNKNOWN
    total = others + max(0.0, after_w)
    if total > budget + POWER_TOLERANCE_W:
        return Reason(
            key="safety.para14a_limit",
            args={"budget_w": round(budget), "total_w": round(total)},
        )
    return None


def check_grid_cooldown(flagged: bool, increase: bool, grid: GridSafety) -> Reason | None:
    """G6: after island (every device) or §14a (flagged devices) increases wait the cool-down."""
    if not increase:
        return None
    if grid.island_hold_s > 0:
        return Reason(
            key="safety.grid_cooldown", args={"remaining_s": _remaining(grid.island_hold_s)}
        )
    if flagged and grid.para14a_hold_s > 0:
        return Reason(
            key="safety.para14a_cooldown", args={"remaining_s": _remaining(grid.para14a_hold_s)}
        )
    return None


def device_grid_violation(
    *,
    kind: DeviceKind,
    flagged: bool,
    increase: bool,
    after_w: float | None,
    flows: FlowSnapshot | None,
    grid: GridSafety,
) -> Reason | None:
    """The first grid violation of a device action (G3-G6), or ``None``."""
    del kind  # every kind is judged alike (G1 "devices, EV, heat pump, programs alike")
    for reason in (
        check_island(increase, grid),
        check_grid_cooldown(flagged, increase, grid),
        check_para14a(flagged, increase, after_w, grid),
        check_grid_inputs(increase, flows, grid),
    ):
        if reason is not None:
            return reason
    return None


def fuse_exceeded(flows: FlowSnapshot | None, grid: GridSafety) -> bool:
    """The measured import (``OK`` reading) is above the fuse/connection limit."""
    limit = grid.fuse_limit_w
    reading = None if flows is None else flows.grid_w
    if limit is None or reading is None or reading.value is None:
        return False
    return reading.quality is Quality.OK and reading.value > limit


def fuse_shed_waiver(
    kind: DeviceKind, switches_off: bool, flows: FlowSnapshot | None, grid: GridSafety
) -> bool:
    """G2: a switch-off above the fuse limit skips min run and the switch limit, except for
    thermal kinds (compressors keep their minimum run time). Needs a fresh snapshot: an old
    ``OK`` value above the fuse in a stale snapshot is an unknown situation, not a breach."""
    return (
        switches_off
        and grid.inputs_fresh
        and kind not in THERMAL_KINDS
        and fuse_exceeded(flows, grid)
    )


# --- battery ----------------------------------------------------------------------------------


def check_battery_grid(
    *, grid_charge: bool, can_cause_import: bool, grid: GridSafety
) -> Reason | None:
    """G3/G4/G6 for the battery (a §14a consumer "with regard to drawing from the grid").

    Island: no state that can cause import (grid charging, a discharge lock - there is no grid
    to cover the house). §14a active or unknown: no grid charging. After island or §14a: no grid
    charging during the cool-down.
    """
    if grid.grid_state is GridState.ISLAND and can_cause_import:
        return REASON_ISLAND_BATTERY
    if not grid_charge:
        return None
    if grid.para14a_status is not Para14aStatus.INACTIVE:
        return REASON_PARA14A_GRID_CHARGE
    if grid.island_hold_s > 0:
        return Reason(
            key="safety.grid_cooldown", args={"remaining_s": _remaining(grid.island_hold_s)}
        )
    if grid.para14a_hold_s > 0:
        return Reason(
            key="safety.para14a_cooldown", args={"remaining_s": _remaining(grid.para14a_hold_s)}
        )
    return None


__all__ = [
    "NO_GRID_SAFETY",
    "REASON_GRID_UNKNOWN",
    "REASON_ISLAND_BATTERY",
    "REASON_ISLAND_NO_INCREASE",
    "REASON_PARA14A_GRID_CHARGE",
    "REASON_PARA14A_UNKNOWN",
    "GridSafety",
    "check_battery_grid",
    "check_grid_cooldown",
    "check_grid_inputs",
    "check_island",
    "check_para14a",
    "device_grid_violation",
    "fuse_exceeded",
    "fuse_shed_waiver",
]
