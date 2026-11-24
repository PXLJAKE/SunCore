# SPDX-License-Identifier: Apache-2.0
"""Heat-pump invariants H1-H5 (plan WP11, ADR-0017 decisions 5 and 8; PR 6-K).

Pure functions over plain values; :mod:`suncore.safety.invariants` wires them into
``ALL_CHECKS`` and uses :func:`failsafe_reduction` to waive the protection times. Imports
``core`` only.

* H1 - **never SG state 1**: an SG-Ready device takes only ``StepAction`` with a writable step
  (state 1 has no step) or a release (:func:`check_sg_action`); the driver switches the contacts
  only along a path of writable states (``core.sgready.contact_path``).
* H2 - **state 4 only with ``allow_forced``** (:func:`check_sg_action`).
* H3 - **dwell** ``min_dwell_s`` (>= 10 min) between two SG state changes
  (:func:`check_sg_dwell`) besides the device's min run/pause and switch rate
  (``invariants.check_min_run_pause``/``check_switch_rate``).
* H4 - **lift within the band and the heat limit**: a heat-pump set-point lift never leaves the
  comfort band (``invariants.check_comfort``) and never goes below the user's base
  (:func:`check_lift_base`); an SG-Ready device is intensified only with a store probe and a
  comfort band (:func:`check_sg_probe`), never at or above ``comfort.max_c`` or with an unknown
  or stale probe (``invariants.check_heat_limit``).
* H5 - **back to normal**: the release restores the baseline (state 2 for anything SunCore may not
  write, the user's set-point); a return to SG state 2 for a fail-safe cause
  (``core.sgready.heat_pump_failsafe``: unknown/stale temperature, heat limit, no probe, §14a,
  island) is never held back by dwell, minimum run or the switch limit
  (:func:`failsafe_reduction`), and no increase passes while such a cause holds
  (:func:`check_heat_pump_failsafe`).

A ``ReleaseAction`` is never judged here.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Final

from suncore.core.control import DeviceState
from suncore.core.decisions import Action, Reason, ReleaseAction, SetpointAction, StepAction
from suncore.core.devices import DeviceConfig, SgReadyControl
from suncore.core.grid import GridState, Para14aStatus
from suncore.core.sgready import (
    SG_FORCED,
    SG_NORMAL,
    FailsafeCause,
    heat_pump_failsafe,
    is_heat_pump_lift,
    sg_has_probe,
    sg_state,
)

SETPOINT_TOLERANCE_C: Final = 0.05

REASON_SG_ACTION_INVALID: Final = Reason(key="safety.sg_action_invalid")
REASON_SG_FORCED_NOT_ALLOWED: Final = Reason(key="safety.sg_forced_not_allowed")
REASON_SG_NEEDS_PROBE: Final = Reason(key="safety.sg_needs_probe")


def failsafe_cause(
    device: DeviceConfig,
    state: DeviceState | None,
    grid_state: GridState,
    para14a_status: Para14aStatus,
) -> FailsafeCause | None:
    """``core.sgready.heat_pump_failsafe`` with the gate's grid view."""
    return heat_pump_failsafe(
        device,
        state,
        island=grid_state is GridState.ISLAND,
        para14a_dimmed=para14a_status is not Para14aStatus.INACTIVE,
    )


def failsafe_reduction(
    action: Action,
    device: DeviceConfig,
    state: DeviceState | None,
    grid_state: GridState,
    para14a_status: Para14aStatus,
) -> bool:
    """H5: a return to SG state 2 while a fail-safe cause holds (waives dwell, min run, switch
    limit - state 2 hands control back to the heat pump's own controller)."""
    return (
        isinstance(device.control, SgReadyControl)
        and isinstance(action, StepAction)
        and action.step == SG_NORMAL
        and failsafe_cause(device, state, grid_state, para14a_status) is not None
    )


def check_sg_action(action: Action, device: DeviceConfig) -> Reason | None:
    """H1 + H2: only writable SG states (2, 3; 4 with ``allow_forced``), never state 1."""
    control = device.control
    if not isinstance(control, SgReadyControl) or isinstance(action, ReleaseAction):
        return None
    if not isinstance(action, StepAction):
        return REASON_SG_ACTION_INVALID
    if action.step == SG_FORCED and not control.allow_forced:
        return REASON_SG_FORCED_NOT_ALLOWED
    if not 0 <= action.step < len(control.steps):
        return Reason(key="safety.sg_state_invalid", args={"step": action.step})
    return None


def check_sg_probe(action: Action, device: DeviceConfig) -> Reason | None:
    """H4: an SG-Ready device is raised above state 2 only with a store probe and a band."""
    if (
        isinstance(device.control, SgReadyControl)
        and isinstance(action, StepAction)
        and action.step > SG_NORMAL
        and not sg_has_probe(device)
    ):
        return REASON_SG_NEEDS_PROBE
    return None


def check_sg_dwell(
    action: Action,
    device: DeviceConfig,
    state: DeviceState | None,
    last_write_ts: datetime | None,
    now: datetime,
    *,
    waived: bool,
) -> Reason | None:
    """H3: at least ``min_dwell_s`` in one SG state before SunCore changes it again.

    SunCore writes the contacts only on change, so its last write is the last state change it
    made. A fail-safe return to state 2 (``waived``) is never held back.
    """
    control = device.control
    if not isinstance(control, SgReadyControl) or not isinstance(action, StepAction):
        return None
    if waived or last_write_ts is None:
        return None
    if state is not None and state.step == action.step:
        return None
    elapsed = (now - last_write_ts).total_seconds()
    if elapsed >= control.min_dwell_s:
        return None
    return Reason(
        key="safety.sg_dwell",
        args={
            "remaining_s": math.ceil(control.min_dwell_s - elapsed),
            "state": sg_state(action.step),
        },
    )


def check_lift_base(action: Action, device: DeviceConfig, base_c: float | None) -> Reason | None:
    """H4: a heat-pump set-point lift never goes below the user's base set-point."""
    if not is_heat_pump_lift(device) or not isinstance(action, SetpointAction):
        return None
    base = base_c if base_c is not None else action.base_c
    if action.setpoint_c < base - SETPOINT_TOLERANCE_C:
        return Reason(
            key="safety.lift_below_base",
            args={"setpoint_c": action.setpoint_c, "base_c": base},
        )
    return None


def check_heat_pump_failsafe(
    increase: bool,
    device: DeviceConfig,
    state: DeviceState | None,
    grid_state: GridState,
    para14a_status: Para14aStatus,
) -> Reason | None:
    """H5: no increase of a heat pump while it must stay in its normal state (§14a dimming of
    a flagged heat pump, island). An unknown or full store is
    judged by ``check_heat_limit``, a missing probe by :func:`check_sg_probe`."""
    if not increase:
        return None
    cause = failsafe_cause(device, state, grid_state, para14a_status)
    if cause in (FailsafeCause.PARA14A, FailsafeCause.ISLAND):
        assert cause is not None
        return Reason(key="safety.heat_pump_failsafe", args={"cause": cause.value})
    return None


__all__ = [
    "REASON_SG_ACTION_INVALID",
    "REASON_SG_FORCED_NOT_ALLOWED",
    "REASON_SG_NEEDS_PROBE",
    "check_heat_pump_failsafe",
    "check_lift_base",
    "check_sg_action",
    "check_sg_dwell",
    "check_sg_probe",
    "failsafe_cause",
    "failsafe_reduction",
]
