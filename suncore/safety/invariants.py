# SPDX-License-Identifier: Apache-2.0
"""Hard invariants (BRIEF §5) as pure functions; each returns ``None`` or a ``Reason``.

Inputs are the intent's action, the device configuration, the device state as the
driver observed it, the device's safety runtime, the site flows with the controller's
surplus estimate, the control settings and the injected ``now``. No I/O, no clock.
``evaluate`` runs every invariant and returns all violations; the gate rejects on the
first non-empty result. Property-based tests live in ``tests/safety/test_invariants.py``.

Conservative defaults: an unknown device state or an unknown surplus never permits
adding load; reducing load or restoring the baseline (``ReleaseAction``) never trips
the grid, battery or comfort invariants.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from suncore.core.balance import FlowSnapshot
from suncore.core.control import ControlSettings, DeviceState, PowerEstimate, heat_capped
from suncore.core.conversion import value_to_power_w
from suncore.core.decisions import (
    Action,
    BatteryAction,
    ModeAction,
    PowerSetpointAction,
    Reason,
    ReleaseAction,
    SetpointAction,
    StepAction,
    SwitchAction,
)
from suncore.core.devices import (
    ContinuousControl,
    DeviceConfig,
    DeviceKind,
    ModeControl,
    step_power_w,
)
from suncore.core.signals import Quality
from suncore.safety.runtime import DeviceRuntime

HVAC_OFF: Final = "off"
SETPOINT_TOLERANCE_C: Final = 0.05
POWER_TOLERANCE_W: Final = 1.0


@dataclass(frozen=True, slots=True)
class InvariantContext:
    """Everything the invariants look at for one intent."""

    action: Action
    device: DeviceConfig
    state: DeviceState | None
    runtime: DeviceRuntime
    now: datetime
    settings: ControlSettings
    flows: FlowSnapshot | None = None
    surplus: PowerEstimate | None = None
    """**Free** surplus in W: power available on top of everything that runs now, i.e.
    export minus reserve, *excluding* the judged device's own current draw. The grid
    check compares it with the action's *added* demand (``power_delta_w``), so a device
    already drawing 1000 W at zero export must not be raised to 1500 W. The controller
    computes it per tick; the gate sets it to ``None`` (unknown) when its inputs are
    older than ``ControlSettings.inputs_max_age_ticks`` ticks."""
    estimate: PowerEstimate | None = None
    """Current power of this device."""
    import_limit_w: float | None = None
    reserve_soc_pct: float | None = None
    claimed_w: float = 0.0
    """Load increases the gate already let through against the same ``GateInputs``
    snapshot (other intents of the same tick, a confirmed proposal): not yet visible in
    ``surplus`` or ``flows``, so the grid check subtracts them (review 3-B, G1)."""


# --- helpers ------------------------------------------------------------------


def device_is_on(state: DeviceState | None) -> bool | None:
    """Observed on/off; ``None`` when unknown."""
    if state is None or not state.known:
        return None
    if state.on is not None:
        return state.on
    if state.mode is not None:
        return state.mode != HVAC_OFF
    if state.step is not None:
        return state.step > 0
    if state.value is not None:
        return state.value > 0
    return None


def action_turns_on(action: Action, device: DeviceConfig) -> bool | None:  # noqa: PLR0911
    """Whether the action leaves the device on (``None``: unknown, e.g. a release)."""
    match action:
        case SwitchAction(on=on):
            return on
        case ModeAction(mode=mode):
            return mode != HVAC_OFF
        case StepAction(step=step):
            stepped = step_power_w(device.control, step)
            return stepped > 0 if stepped is not None else step > 0
        case PowerSetpointAction(power_w=power):
            return power > 0
        case SetpointAction():
            return True
        case ReleaseAction() | BatteryAction():  # battery: rejected by the gate before (WP9)
            return None
    return None  # pragma: no cover - exhaustive


def is_switch_transition(action: Action, device: DeviceConfig, state: DeviceState | None) -> bool:
    """``True`` when the action changes on/off (unknown state = assume a transition)."""
    target = action_turns_on(action, device)
    if target is None:
        return False
    current = device_is_on(state)
    return current is None or current != target


def expected_power_w(  # noqa: PLR0911 - one branch per action kind
    action: Action, device: DeviceConfig, estimate: PowerEstimate | None
) -> float | None:
    """Power the device is expected to draw after the action (``None`` = unknown)."""
    nominal = device.power_model.nominal_w
    match action:
        case SwitchAction(on=on):
            if not on:
                return 0.0
            return nominal if nominal is not None else _upper(estimate)
        case StepAction(step=step):
            return step_power_w(device.control, step)
        case ModeAction(mode=mode):
            if mode == HVAC_OFF:
                return 0.0
            control = device.control
            if isinstance(control, ModeControl):
                for entry in control.modes:
                    if entry.value == mode:
                        return entry.power_w
            return nominal if nominal is not None else _upper(estimate)
        case PowerSetpointAction(power_w=power):
            control = device.control
            if isinstance(control, ContinuousControl):
                return _continuous_power_w(power, control, nominal)
            return max(0.0, power)
        case SetpointAction():
            return nominal if nominal is not None else _upper(estimate)
        case ReleaseAction() | BatteryAction():  # battery: rejected by the gate before (WP9)
            return None
    return None  # pragma: no cover - exhaustive


def _upper(estimate: PowerEstimate | None) -> float | None:
    return None if estimate is None else estimate.upper_w


def _continuous_power_w(value: float, control: ContinuousControl, nominal: float | None) -> float:
    return value_to_power_w(value, control, nominal)


def power_delta_w(ctx: InvariantContext) -> float | None:
    """Added demand of the action in W (negative = reduces load); ``None`` = unknown.

    A set-point shift back toward the user's base never adds load. A shift away from
    the base on a running unit adds an unknown amount up to the nominal power, so the
    delta is ``nominal - estimate.lower`` (the full nominal without an estimate).
    """
    expected = expected_power_w(ctx.action, ctx.device, ctx.estimate)
    if expected is None:
        return None
    has_estimate = ctx.estimate is not None and ctx.estimate.quality in (
        Quality.OK,
        Quality.STALE,
    )
    if isinstance(ctx.action, SetpointAction):
        if _toward_base(ctx.action, ctx.runtime, ctx.state):
            current = ctx.estimate.lower_w if has_estimate and ctx.estimate else expected
            return min(0.0, expected - current)
        current = ctx.estimate.lower_w if has_estimate and ctx.estimate else 0.0
        return expected - current
    if has_estimate and ctx.estimate is not None:
        current = ctx.estimate.lower_w
    else:
        on = device_is_on(ctx.state)
        if on is True and ctx.device.power_model.nominal_w is not None:
            current = ctx.device.power_model.nominal_w
        else:
            current = 0.0  # off or unknown: assume nothing is running (conservative)
    return expected - current


def _toward_base(action: SetpointAction, runtime: DeviceRuntime, state: DeviceState | None) -> bool:
    """Set-point moves toward (or onto) the user's base: demand does not increase."""
    base = runtime.base_setpoint_c if runtime.base_setpoint_c is not None else action.base_c
    current = state.setpoint_c if state is not None and state.setpoint_c is not None else base
    return abs(action.setpoint_c - base) <= abs(current - base) + SETPOINT_TOLERANCE_C


# --- invariants -----------------------------------------------------------------


def check_grid(ctx: InvariantContext) -> Reason | None:  # noqa: PLR0911 - one per rule
    """No grid import caused by SunCore and never above the import limit (plan WP7).

    Adding ``delta`` W needs a known surplus with ``surplus - uncertainty - claimed >=
    delta``, no current import and ``grid + claimed + delta <= import_limit_w`` where
    configured (``claimed`` = increases already accepted against the same snapshot).
    """
    delta = power_delta_w(ctx)
    if delta is None or delta <= 0:
        return None
    if ctx.surplus is None or ctx.surplus.quality is not Quality.OK:
        return Reason(key="safety.surplus_unknown")
    grid = None if ctx.flows is None else ctx.flows.grid_w
    if grid is not None and grid.value is not None and grid.value > 0:
        return Reason(key="safety.grid_importing", args={"grid_w": round(grid.value)})
    claimed = max(0.0, ctx.claimed_w)
    available = ctx.surplus.lower_w - claimed
    if available < delta:
        return Reason(
            key="safety.grid_import_caused",
            args={"need_w": round(delta), "surplus_w": round(max(0.0, available))},
        )
    if ctx.import_limit_w is not None:
        if grid is None or grid.value is None:
            return Reason(key="safety.grid_unknown")
        if grid.value + claimed + delta > ctx.import_limit_w:
            return Reason(
                key="safety.grid_import_limit",
                args={"limit_w": round(ctx.import_limit_w), "grid_w": round(grid.value)},
            )
    return None


def check_comfort(ctx: InvariantContext) -> Reason | None:
    """A set-point never leaves the comfort band."""
    if not isinstance(ctx.action, SetpointAction):
        return None
    band = ctx.device.comfort
    if band is None:
        return Reason(key="safety.comfort_band_missing")
    if not band.min_c <= ctx.action.setpoint_c <= band.max_c:
        return Reason(
            key="safety.comfort_band",
            args={"setpoint_c": ctx.action.setpoint_c, "min_c": band.min_c, "max_c": band.max_c},
        )
    return None


def check_min_run_pause(ctx: InvariantContext) -> Reason | None:
    """Minimum run time before a switch-off, minimum pause before a switch-on."""
    if isinstance(ctx.action, ReleaseAction):
        return None
    if not is_switch_transition(ctx.action, ctx.device, ctx.state):
        return None
    target = action_turns_on(ctx.action, ctx.device)
    if target is True:
        since, limit = ctx.runtime.last_off_ts, ctx.device.min_pause_s
    else:
        since, limit = ctx.runtime.last_on_ts, ctx.device.min_run_s
    if limit <= 0 or since is None:
        return None
    elapsed = (ctx.now - since).total_seconds()
    if elapsed >= limit:
        return None
    args: dict[str, str | int | float | bool] = {"remaining_s": math.ceil(limit - elapsed)}
    if target is True:
        return Reason(key="safety.min_pause", args=args)
    return Reason(key="safety.min_run", args=args)


def check_switch_rate(ctx: InvariantContext) -> Reason | None:
    """At most ``max_switches_per_hour`` switch transitions in the sliding hour."""
    if isinstance(ctx.action, ReleaseAction):
        return None
    if not is_switch_transition(ctx.action, ctx.device, ctx.state):
        return None
    limit = ctx.device.max_switches_per_hour
    if limit is None:
        limit = ctx.settings.default_max_switches_per_hour
    count = ctx.runtime.switches_in_window(ctx.now)
    if count >= limit:
        return Reason(key="safety.max_switches", args={"limit": limit, "count": count})
    return None


def check_write_rate(ctx: InvariantContext) -> Reason | None:
    """At most one write per ``min_write_interval_s`` per device."""
    if isinstance(ctx.action, ReleaseAction):
        return None
    last = ctx.runtime.last_write_ts
    if last is None:
        return None
    wait = timedelta(seconds=ctx.settings.min_write_interval_s)
    if ctx.now - last < wait:
        remaining = (last + wait - ctx.now).total_seconds()
        return Reason(key="safety.rate_limited", args={"remaining_s": math.ceil(remaining)})
    return None


def check_change(ctx: InvariantContext) -> Reason | None:
    """Write only on change: the observed state must differ from the target."""
    if isinstance(ctx.action, ReleaseAction):
        return None
    if ctx.state is not None and action_matches_state(ctx.action, ctx.state, ctx.device):
        return Reason(key="safety.write_no_change")
    return None


def check_battery_reserve(ctx: InvariantContext) -> Reason | None:
    """Never add load while the battery discharges at or below the reserve (guard)."""
    delta = power_delta_w(ctx)
    if delta is None or delta <= 0 or ctx.flows is None or ctx.reserve_soc_pct is None:
        return None
    soc = ctx.flows.battery_soc_pct.value
    battery = ctx.flows.battery_w.value
    if soc is None or battery is None:
        return None
    if battery < 0 and soc <= ctx.reserve_soc_pct:
        return Reason(
            key="safety.battery_reserve",
            args={"soc_pct": round(soc, 1), "reserve_pct": ctx.reserve_soc_pct},
        )
    return None


def check_climate_switch_on(ctx: InvariantContext) -> Reason | None:
    """A climate unit that is off is only switched on with ``allow_switch_on``, and
    only through the configured ``switch_on_hvac_mode``.

    ``lg_thinq`` turns the unit on for a ``set_temperature`` while off
    (research/device-integrations.md), so a set-point write counts as switching on.
    A ``ModeAction`` with any other mode (``dry``, ``fan_only``, ...) while off is
    rejected even with the opt-in. An unknown state is treated as off.
    """
    if ctx.device.kind is not DeviceKind.CLIMATE:
        return None
    target = action_turns_on(ctx.action, ctx.device)
    if target is not True:
        return None
    if device_is_on(ctx.state) is True:
        return None
    if not ctx.device.allow_switch_on or ctx.device.switch_on_hvac_mode is None:
        return Reason(key="safety.climate_off")
    if isinstance(ctx.action, ModeAction) and ctx.action.mode != ctx.device.switch_on_hvac_mode:
        return Reason(
            key="safety.climate_switch_on_mode",
            args={"mode": ctx.action.mode, "allowed": ctx.device.switch_on_hvac_mode},
        )
    return None


def check_heat_limit(ctx: InvariantContext) -> Reason | None:
    """No load increase for a thermal device at its ``comfort.max_c`` (water heater tank).

    Applies to a device with a ``temperature`` probe and a comfort band (not climate set-point
    control); a probe that is unknown, stale or not in °C counts as a full tank. Lowering and
    switching off stay allowed; an increase is any action with a positive (or unknown)
    added demand that is not a switch-off.
    """
    if isinstance(ctx.action, ReleaseAction) or not heat_capped(ctx.device, ctx.state):
        return None
    if action_turns_on(ctx.action, ctx.device) is False:
        return None
    delta = power_delta_w(ctx)
    if delta is not None and delta <= 0:
        return None
    assert ctx.device.comfort is not None
    temperature = None if ctx.state is None else ctx.state.temperature_c
    if temperature is None:
        return Reason(key="safety.heat_temperature_unknown")
    return Reason(
        key="safety.heat_limit",
        args={"temperature_c": round(temperature, 1), "max_c": ctx.device.comfort.max_c},
    )


ALL_CHECKS: Final = (
    check_climate_switch_on,
    check_comfort,
    check_heat_limit,
    check_change,
    check_min_run_pause,
    check_switch_rate,
    check_write_rate,
    check_grid,
    check_battery_reserve,
)


def evaluate(ctx: InvariantContext) -> tuple[Reason, ...]:
    """All violated invariants, in check order; empty = the intent may be written."""
    return tuple(r for r in (check(ctx) for check in ALL_CHECKS) if r is not None)


# --- comparison -----------------------------------------------------------------


def action_matches_state(  # noqa: PLR0911
    action: Action, state: DeviceState, device: DeviceConfig | None = None
) -> bool:
    """Whether the observed state already equals the action's target.

    ``DeviceState.value`` is in the control's unit (W, A or %); with ``device`` it is
    converted to W before the comparison with a ``PowerSetpointAction``.
    """
    if not state.known:
        return False
    match action:
        case SwitchAction(on=on):
            return device_is_on(state) == on
        case SetpointAction(setpoint_c=target):
            return (
                device_is_on(state) is not False
                and state.setpoint_c is not None
                and abs(state.setpoint_c - target) <= SETPOINT_TOLERANCE_C
            )
        case StepAction(step=step):
            return state.step == step
        case ModeAction(mode=mode):
            return state.mode == mode
        case PowerSetpointAction(power_w=power):
            if state.value is None:
                return False
            current = state.value
            if device is not None and isinstance(device.control, ContinuousControl):
                current = _continuous_power_w(current, device.control, device.power_model.nominal_w)
            return abs(current - power) <= POWER_TOLERANCE_W
        case ReleaseAction() | BatteryAction():  # battery: rejected by the gate before (WP9)
            return False
    return False  # pragma: no cover - exhaustive


def action_matches(expected: Action, observed: Action | None) -> bool:
    """Read-back comparison: the observed action equals the written one (with tolerance)."""
    if observed is None or expected.kind is not observed.kind:
        return False
    match expected, observed:
        case SetpointAction(setpoint_c=a), SetpointAction(setpoint_c=b):
            return abs(a - b) <= SETPOINT_TOLERANCE_C
        case PowerSetpointAction(power_w=a), PowerSetpointAction(power_w=b):
            return abs(a - b) <= POWER_TOLERANCE_W
        case _:
            return expected == observed


__all__ = [
    "ALL_CHECKS",
    "InvariantContext",
    "action_matches",
    "action_matches_state",
    "action_turns_on",
    "check_battery_reserve",
    "check_change",
    "check_climate_switch_on",
    "check_comfort",
    "check_grid",
    "check_min_run_pause",
    "check_switch_rate",
    "check_write_rate",
    "device_is_on",
    "evaluate",
    "expected_power_w",
    "is_switch_transition",
    "power_delta_w",
]
