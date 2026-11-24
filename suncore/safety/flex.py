# SPDX-License-Identifier: Apache-2.0
"""EV charging invariants E1-E9 (plan WP9, ADR-0017 decisions 1, 4; PR 6-I).

Pure functions over plain values; :mod:`suncore.safety.invariants` calls them for a
``ChargeAction`` with the :class:`EvSafety` the gate builds per intent. Imports ``core`` only.

* **E1** - ``current_a`` is 0 or inside ``[min_current_a, max_current_a]`` and inside the live
  entity range (``min``/``max`` attributes); a pause (0 A) needs an ``enable`` switch or an
  entity that accepts 0 A: :func:`check_current_range`.
* **E2** - a phase change only while paused for ``phase_switch_pause_s``, at most
  ``max_phase_switches_per_hour``, never in mode ``now``, only on a charger with phase
  switching; charging resumes only ``phase_switch_pause_s`` after the change:
  :func:`check_phases`.
* **E3** - demand above the free surplus only within the bounded import exception of
  ADR-0017 decision 4 (:func:`ev_allowance_w`: ``now`` up to the maximum, ``min_pv`` up to the
  minimum on the current phases, ``target`` up to a fresh plan band's ``grid_power_max_w``);
  :func:`check_charge_grid` (with the import limit, G1).
* **E4** - G1/G3/G4/G5/G6 hold: the device invariants (``check_grid_situation``) judge a
  ``ChargeAction`` like any other load increase (``expected_power_w`` knows its power).
* **E5** - no increase while ``connected`` is known false, none in mode ``off``:
  :func:`check_ev_mode`.
* **E6** - the home battery never discharges into the EV (``battery.allow_discharge_to_ev``
  does not exist, default off): power from the allowance is accepted only while no battery is
  present or SunCore holds a discharge lock on it (``battery_locked``); the controller never
  counts battery discharge as surplus (``controller.surplus``). The lock itself is a battery
  intent judged by B4 with the EV reason (:func:`ev_lock_wanted`).
* **E7** - write on change, write rate, read-back within ``settle_s``, three failures trip the
  device: the unchanged gate path (``check_change``, ``check_write_rate``, ``_confirm``).
* **E8** - effective mode at most assisted while the behaviour is not confirmed: the gate's
  ``modes_of`` (``core.devices.behaviour_mode_capped``).
* **E9** - release restores current, phases and the enable switch: the driver's baseline is a
  full ``ChargeAction`` with ``enabled`` (``devices.drivers.charger``).

A ``ReleaseAction`` is never judged here ("back to normal" works from any state).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from suncore.core.control import DeviceBand
from suncore.core.decisions import BatteryAction, ChargeAction, Reason
from suncore.core.devices import ChargerControl
from suncore.core.flex import EvMode

CURRENT_TOLERANCE_A: Final = 0.05
"""Two currents closer than this are the same (entity values are floats)."""
POWER_TOLERANCE_W: Final = 1.0
SOC_LOCK_TOLERANCE_PCT: Final = 0.5
"""A ``min_soc`` lock at most this far below the live SoC still holds the energy."""

REASON_EV_MODE_OFF: Final = Reason(key="safety.ev_mode_off")
REASON_EV_NOT_CONNECTED: Final = Reason(key="safety.ev_not_connected")
REASON_EV_PAUSE_UNSUPPORTED: Final = Reason(key="safety.ev_pause_unsupported")
REASON_EV_PHASES_UNSUPPORTED: Final = Reason(key="safety.ev_phases_unsupported")
REASON_EV_PHASES_UNKNOWN: Final = Reason(key="safety.ev_phases_unknown")
REASON_EV_PHASES_NOW: Final = Reason(key="safety.ev_phases_now")
REASON_EV_PHASES_CHARGING: Final = Reason(key="safety.ev_phases_charging")
REASON_EV_BATTERY_UNLOCKED: Final = Reason(key="safety.ev_battery_unlocked")
REASON_SURPLUS_UNKNOWN: Final = Reason(key="safety.surplus_unknown")
REASON_GRID_UNKNOWN: Final = Reason(key="safety.grid_unknown")


@dataclass(frozen=True, slots=True)
class EvSafety:
    """What the EV invariants look at for one ``ChargeAction`` (the gate builds it).

    The defaults are the most conservative situation: mode ``pv`` (no allowance), connection
    unknown, a battery that is not locked, nothing observed.
    """

    mode: EvMode = EvMode.PV
    allowance_w: float = 0.0
    """Grid power the bounded import exception permits now (:func:`ev_allowance_w`)."""
    connected: bool | None = None
    battery_locked: bool = False
    """E6: no home battery, or SunCore holds a discharge lock on it."""
    observed: ChargeAction | None = None
    """The charger's state as read back (``None`` = unknown)."""
    entity_min_a: float | None = None
    entity_max_a: float | None = None
    """The current entity's live ``min``/``max`` attributes (``None`` = not published)."""


NO_EV_SAFETY: Final = EvSafety()


# --- the bounded import exception -------------------------------------------------------------


def charge_power_w(current_a: float, phases: int, voltage_v: float) -> float:
    """Power of ``current_a`` on ``phases`` at ``voltage_v`` (W)."""
    return max(0.0, current_a) * voltage_v * phases


def active_phases(
    control: ChargerControl, action: ChargeAction | None, observed: ChargeAction | None
) -> Literal[1, 3]:
    """The phases a charge runs on: the action's, else the observed, else the wired ones."""
    if action is not None and action.phases is not None:
        return action.phases
    if observed is not None and observed.phases is not None:
        return observed.phases
    return control.phases


def ev_allowance_w(
    mode: EvMode,
    control: ChargerControl,
    phases: Literal[1, 3],
    *,
    connected: bool | None,
    band: DeviceBand | None,
) -> float:
    """Grid power the bounded import exception permits (ADR-0017 decision 4), W.

    ``now``: the maximum current on ``phases``; ``min_pv``: the minimum current on ``phases``;
    ``target``: the fresh plan band's ``grid_power_max_w`` (only in an allowed band); ``pv`` and
    ``off``: 0. A vehicle known to be unplugged gets nothing. ``band`` must be the band of a
    fresh plan interval covering now (``None`` = none).
    """
    if connected is False:
        return 0.0
    if mode is EvMode.NOW:
        return charge_power_w(control.max_current_a, phases, control.voltage_v)
    if mode is EvMode.MIN_PV:
        return charge_power_w(control.min_current_a, phases, control.voltage_v)
    if mode is EvMode.TARGET and band is not None and band.allowed:
        return max(0.0, band.grid_power_max_w or 0.0)
    return 0.0


def holds_discharge_lock(held: BatteryAction | None, soc_pct: float | None) -> bool:
    """SunCore holds a battery state that stops discharging (E6): ``max_discharge_w`` 0, or a
    ``min_soc`` at (or just below) the live SoC. An unknown SoC never proves a ``min_soc`` lock.
    """
    if held is None:
        return False
    if held.max_discharge_w is not None and held.max_discharge_w <= POWER_TOLERANCE_W:
        return True
    return (
        held.min_soc_pct is not None
        and soc_pct is not None
        and held.min_soc_pct >= soc_pct - SOC_LOCK_TOLERANCE_PCT
    )


def ev_lock_wanted(allowances_w: list[float]) -> bool:
    """The battery controller asks for a discharge lock while an EV has an allowance (E6)."""
    return any(a > POWER_TOLERANCE_W for a in allowances_w)


# --- invariants ---------------------------------------------------------------------------------


def check_ev_mode(increase: bool, ev: EvSafety) -> Reason | None:
    """E5 + mode ``off``: no increase while unplugged or switched off by the user."""
    if not increase:
        return None
    if ev.mode is EvMode.OFF:
        return REASON_EV_MODE_OFF
    if ev.connected is False:
        return REASON_EV_NOT_CONNECTED
    return None


def check_current_range(
    action: ChargeAction, control: ChargerControl, ev: EvSafety
) -> Reason | None:
    """E1: 0 A (a pause the charger can do) or a current inside both ranges."""
    current = action.current_a
    if current <= CURRENT_TOLERANCE_A:
        entity_min = ev.entity_min_a
        if control.enable_role is None and (entity_min is None or entity_min > 0):
            return REASON_EV_PAUSE_UNSUPPORTED
        return None
    low = control.min_current_a
    high = control.max_current_a
    if ev.entity_min_a is not None:
        low = max(low, ev.entity_min_a)
    if ev.entity_max_a is not None:
        high = min(high, ev.entity_max_a)
    if low - CURRENT_TOLERANCE_A <= current <= high + CURRENT_TOLERANCE_A:
        return None
    return Reason(
        key="safety.ev_current_range",
        args={"current_a": round(current, 1), "min_a": round(low, 1), "max_a": round(high, 1)},
    )


def check_phases(  # noqa: PLR0911 - one return per rule
    action: ChargeAction,
    control: ChargerControl,
    ev: EvSafety,
    *,
    now: datetime,
    paused_since: datetime | None,
    last_phase_switch: datetime | None,
    phase_switches_last_hour: int,
    never_charged: bool = False,
) -> Reason | None:
    """E2: a phase change only paused, rarely, never in ``now``; resume after the pause.

    ``paused_since`` is when SunCore paused the charger (``runtime.last_off_ts``; a pause it did
    not see does not count), ``last_phase_switch`` its latest phase change. ``never_charged``:
    SunCore has never started or stopped charging on this charger (no ``last_on_ts`` and no
    ``last_off_ts``) - then there is no SunCore pause to wait for, and the observed
    "not charging" (checked here) is the condition; the controller still waits the pause from
    when it first saw the charger idle (``controller.ev``).
    """
    pause = control.phase_switch_pause_s
    observed = ev.observed
    target = action.phases
    observed_phases = None if observed is None else observed.phases
    if target is not None and target != (observed_phases or control.phases):
        if not control.phase_switching or control.phases_role is None:
            return REASON_EV_PHASES_UNSUPPORTED
        if observed is None or observed_phases is None:
            return REASON_EV_PHASES_UNKNOWN
        if ev.mode is EvMode.NOW:
            return REASON_EV_PHASES_NOW
        if action.current_a > CURRENT_TOLERANCE_A or observed.effective_current_a > (
            CURRENT_TOLERANCE_A
        ):
            return REASON_EV_PHASES_CHARGING
        waited = math.inf if paused_since is None else (now - paused_since).total_seconds()
        if (paused_since is None and not never_charged) or waited < pause:
            remaining = pause if paused_since is None else math.ceil(pause - waited)
            return Reason(key="safety.ev_phase_pause", args={"remaining_s": int(remaining)})
        if phase_switches_last_hour >= control.max_phase_switches_per_hour:
            return Reason(
                key="safety.ev_phase_switches",
                args={
                    "limit": control.max_phase_switches_per_hour,
                    "count": phase_switches_last_hour,
                },
            )
        return None
    if action.current_a > CURRENT_TOLERANCE_A and last_phase_switch is not None:
        waited = (now - last_phase_switch).total_seconds()
        if waited < pause:
            return Reason(
                key="safety.ev_phase_pause", args={"remaining_s": math.ceil(pause - waited)}
            )
    return None


def check_charge_grid(
    *,
    after_w: float,
    current_lower_w: float,
    surplus_lower_w: float | None,
    claimed_w: float,
    grid_w: float | None,
    import_limit_w: float | None,
    ev: EvSafety,
) -> Reason | None:
    """E3 + E6 + G1 for a charge increase with an allowance (``ev.allowance_w > 0``).

    The surplus the device may use is ``A = current_lower + free surplus - claimed`` (what it
    could draw without import); the charge may reach ``allowance + max(0, A)`` - the import it
    causes is at most the allowance. Power beyond ``A`` comes from the grid and is accepted only
    while the home battery cannot cover it (E6). The import limit always holds (G1): ``grid +
    claimed + delta <= import_limit_w``, which needs a known grid reading.
    """
    if surplus_lower_w is None:
        return REASON_SURPLUS_UNKNOWN
    available = current_lower_w + surplus_lower_w - max(0.0, claimed_w)
    ceiling = ev.allowance_w + max(0.0, available)
    if after_w > ceiling + POWER_TOLERANCE_W:
        return Reason(
            key="safety.ev_import_exception",
            args={"need_w": round(after_w), "allowed_w": round(ceiling)},
        )
    if after_w > max(0.0, available) + POWER_TOLERANCE_W and not ev.battery_locked:
        return REASON_EV_BATTERY_UNLOCKED
    if import_limit_w is not None:
        if grid_w is None:
            return REASON_GRID_UNKNOWN
        delta = max(0.0, after_w - current_lower_w)
        if grid_w + max(0.0, claimed_w) + delta > import_limit_w:
            return Reason(
                key="safety.grid_import_limit",
                args={"limit_w": round(import_limit_w), "grid_w": round(grid_w)},
            )
    return None


def charge_matches(expected: ChargeAction, observed: ChargeAction) -> bool:
    """Read-back: the same effective current and, where both are known, the same phases."""
    if abs(expected.effective_current_a - observed.effective_current_a) > CURRENT_TOLERANCE_A:
        return False
    return expected.phases is None or observed.phases is None or expected.phases == observed.phases


__all__ = [
    "CURRENT_TOLERANCE_A",
    "NO_EV_SAFETY",
    "REASON_EV_BATTERY_UNLOCKED",
    "REASON_EV_MODE_OFF",
    "REASON_EV_NOT_CONNECTED",
    "REASON_EV_PAUSE_UNSUPPORTED",
    "REASON_EV_PHASES_CHARGING",
    "REASON_EV_PHASES_NOW",
    "REASON_EV_PHASES_UNKNOWN",
    "REASON_EV_PHASES_UNSUPPORTED",
    "EvSafety",
    "active_phases",
    "charge_matches",
    "charge_power_w",
    "check_charge_grid",
    "check_current_range",
    "check_ev_mode",
    "check_phases",
    "ev_allowance_w",
    "ev_lock_wanted",
    "holds_discharge_lock",
]
