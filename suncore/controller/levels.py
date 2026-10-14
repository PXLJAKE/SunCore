# SPDX-License-Identifier: Apache-2.0
"""Levels: the one abstraction the allocator works with for every control kind.

A device is driven through a small ordered table of :class:`Level` entries. Level 0 is
the *floor* - the state the device has when SunCore allocates nothing to it: off for a
switch, the lowest step, the control's minimum for a continuous load, the user's own
set-point for a climate unit. Higher levels draw more power. The allocator raises or lowers
the level; the level's ``action`` is what the gate is asked to write.

Kinds (BRIEF §4.5):

* ``on_off``: ``[off, on]`` with the nominal power;
* ``steps`` / ``relays``: one level per configured step;
* ``continuous``: the control's range in W, quantised on a grid of at least the device's
  hysteresis (the dead band) through the injected :class:`Quantiser` (the number driver's
  own snapping, so the intent equals the written value);
* ``setpoint`` (climate): shifts of ``step_c`` away from the user's base set-point inside
  the comfort band - up for ``heat``, down for ``cool``; other HVAC modes are not shifted.
  A unit that is off is only controllable with ``allow_switch_on``: level 1 then switches
  it on (``SwitchAction``) and the shifts follow. Every shift level "draws" the nominal
  power: a shifted unit may run flat out, so the gate and the controller both treat a
  shift away from the base as adding ``nominal - current draw``.
* ``mode``: one level per configured mode, ordered by power.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, Protocol

from suncore.core.control import ControlSettings, DeviceState, PowerEstimate
from suncore.core.conversion import value_to_power_w
from suncore.core.decisions import (
    Action,
    ModeAction,
    PowerSetpointAction,
    SetpointAction,
    StepAction,
    SwitchAction,
)
from suncore.core.devices import (
    ContinuousControl,
    DeviceConfig,
    ModeControl,
    OnOffControl,
    RelaysControl,
    SetpointControl,
    StepsControl,
    step_power_w,
)
from suncore.core.setpoint import snap_setpoint

HVAC_OFF: Final = "off"
HEATING_MODES: Final = frozenset({"heat"})
COOLING_MODES: Final = frozenset({"cool"})
MAX_CONTINUOUS_LEVELS: Final = 64
SETPOINT_EPS: Final = 1e-6


class Quantiser(Protocol):
    """Rounds targets to what the device really accepts (the drivers know the entity).

    The composition root wires ``ClimateDriver.effective_step_c`` and
    ``NumberDriver.snap_power_w`` through :class:`CallableQuantiser`;
    :class:`ConfigQuantiser` is the default from the
    device configuration alone.
    """

    def setpoint_step_c(self, device_id: str) -> float | None:
        """Set-point step in °C; ``None`` = use the control's ``step_c``."""
        ...

    def power_w(self, device_id: str, power_w: float) -> float:
        """The power a continuous load will really be set to for a request of ``power_w``."""
        ...


class ConfigQuantiser:
    """Quantisation from the device configuration (control ``step``/``step_c``, limits)."""

    def __init__(self, devices: dict[str, DeviceConfig] | None = None) -> None:
        self._devices = dict(devices or {})

    def update(self, devices: dict[str, DeviceConfig]) -> None:
        self._devices = dict(devices)

    def setpoint_step_c(self, device_id: str) -> float | None:  # noqa: ARG002 - protocol
        return None

    def power_w(self, device_id: str, power_w: float) -> float:
        device = self._devices.get(device_id)
        if device is None or not isinstance(device.control, ContinuousControl):
            return max(0.0, power_w)
        control = device.control
        nominal = device.power_model.nominal_w
        low = value_to_power_w(control.min, control, nominal)
        high = value_to_power_w(control.max, control, nominal)
        value = min(max(power_w, low), high)
        if control.step is not None:
            step_w = value_to_power_w(control.step, control, nominal)
            if step_w > 0:
                value = low + math.floor((value - low) / step_w + SETPOINT_EPS) * step_w
        return round(value, 6)


class CallableQuantiser:
    """Quantisation through per-device callables (the composition root's driver wiring).

    ``setpoint_steps[device_id]()`` -> ``ClimateDriver.effective_step_c``,
    ``power_snaps[device_id](power_w)`` -> ``NumberDriver.snap_power_w``. Devices without a
    callable fall back to ``fallback`` (default: :class:`ConfigQuantiser` without devices).
    """

    def __init__(
        self,
        *,
        setpoint_steps: Mapping[str, Callable[[], float]] | None = None,
        power_snaps: Mapping[str, Callable[[float], float]] | None = None,
        fallback: Quantiser | None = None,
    ) -> None:
        self._steps = dict(setpoint_steps or {})
        self._snaps = dict(power_snaps or {})
        self._fallback: Quantiser = fallback if fallback is not None else ConfigQuantiser()

    def setpoint_step_c(self, device_id: str) -> float | None:
        step = self._steps.get(device_id)
        if step is None:
            return self._fallback.setpoint_step_c(device_id)
        value = step()
        return value if value > 0 and math.isfinite(value) else None

    def power_w(self, device_id: str, power_w: float) -> float:
        snap = self._snaps.get(device_id)
        if snap is None:
            return self._fallback.power_w(device_id, power_w)
        value = snap(power_w)
        return value if math.isfinite(value) and value >= 0 else 0.0


@dataclass(frozen=True, slots=True)
class Level:
    """One allocatable state of a device."""

    index: int
    power_w: float
    """Power the device is expected to draw at this level (0 for the climate base)."""
    action: Action | None
    """What to write to reach the level; ``None`` for a floor that needs no write."""
    setpoint_c: float | None = None
    """Climate: the absolute set-point of this level."""

    @property
    def on(self) -> bool:
        return self.power_w > 0


@dataclass(frozen=True, slots=True)
class LevelTable:
    """The levels of a device right now, plus how it moves between them."""

    levels: tuple[Level, ...]
    ramped: bool = False
    """Move one level per hysteresis delay (climate shifts) instead of jumping."""
    base_setpoint_c: float | None = None
    """Climate: the user's set-point the shifts are relative to."""
    switchable: bool = True
    """Whether level 0 <-> >0 is a switch (min run/pause, switches per hour apply)."""

    @property
    def top(self) -> int:
        return len(self.levels) - 1

    def level(self, index: int) -> Level:
        return self.levels[max(0, min(index, self.top))]

    def need_w(self, from_index: int, to_index: int) -> float:
        """Added power when moving up (never negative)."""
        return max(0.0, self.level(to_index).power_w - self.level(from_index).power_w)


# --- building -----------------------------------------------------------------------


def nominal_power_w(device: DeviceConfig, estimate: PowerEstimate | None) -> float | None:
    """The power the device draws when fully on (``None`` = unknown, not controllable)."""
    nominal = device.power_model.nominal_w
    if nominal is not None:
        return nominal
    control = device.control
    if isinstance(control, StepsControl | RelaysControl):
        return control.steps[-1].power_w
    if isinstance(control, ModeControl):
        return max(m.power_w for m in control.modes)
    if isinstance(control, ContinuousControl):
        return value_to_power_w(control.max, control, None) if control.unit != "%" else None
    if estimate is not None and estimate.value_w > 0:
        return estimate.upper_w
    return None


def build_levels(  # noqa: PLR0911 - one return per control kind
    device: DeviceConfig,
    state: DeviceState | None,
    estimate: PowerEstimate | None,
    settings: ControlSettings,
    quantiser: Quantiser,
    *,
    base_setpoint_c: float | None = None,
    switched_on_by_us: bool = False,
) -> LevelTable | None:
    """The level table of ``device`` in its current situation; ``None`` = not controllable."""
    control = device.control
    if isinstance(control, OnOffControl):
        nominal = nominal_power_w(device, estimate)
        if nominal is None:
            return None
        return LevelTable(
            (
                Level(0, 0.0, SwitchAction(on=False)),
                Level(1, nominal, SwitchAction(on=True)),
            )
        )
    if isinstance(control, StepsControl | RelaysControl):
        return LevelTable(
            tuple(
                Level(i, step_power_w(control, i) or 0.0, StepAction(step=i))
                for i in range(len(control.steps))
            )
        )
    if isinstance(control, ModeControl):
        modes = sorted(control.modes, key=lambda m: m.power_w)
        return LevelTable(
            tuple(Level(i, m.power_w, ModeAction(mode=m.value)) for i, m in enumerate(modes)),
            switchable=modes[0].power_w == 0,
        )
    if isinstance(control, ContinuousControl):
        return _continuous_levels(device, control, settings, quantiser)
    if isinstance(control, SetpointControl):
        return _climate_levels(
            device,
            control,
            state,
            estimate,
            quantiser,
            base_setpoint_c=base_setpoint_c,
            switched_on_by_us=switched_on_by_us,
        )
    return None  # pragma: no cover - exhaustive


def _continuous_levels(
    device: DeviceConfig,
    control: ContinuousControl,
    settings: ControlSettings,
    quantiser: Quantiser,
) -> LevelTable | None:
    nominal = device.power_model.nominal_w
    low = value_to_power_w(control.min, control, nominal)
    high = value_to_power_w(control.max, control, nominal)
    if high <= low:
        return None
    hysteresis = (
        device.hysteresis_w if device.hysteresis_w is not None else settings.default_hysteresis_w
    )
    grid = max(hysteresis, (high - low) / MAX_CONTINUOUS_LEVELS, 1.0)
    levels: list[Level] = []
    seen: set[float] = set()
    value = low
    while value <= high + SETPOINT_EPS:
        power = quantiser.power_w(device.id, min(value, high))
        if power not in seen:
            seen.add(power)
            levels.append(Level(len(levels), power, PowerSetpointAction(power_w=power)))
        value += grid
    top = quantiser.power_w(device.id, high)
    if top not in seen:
        levels.append(Level(len(levels), top, PowerSetpointAction(power_w=top)))
    levels.sort(key=lambda lv: lv.power_w)
    ordered = tuple(Level(i, lv.power_w, lv.action, lv.setpoint_c) for i, lv in enumerate(levels))
    return LevelTable(ordered, switchable=ordered[0].power_w == 0)


def shift_direction(state: DeviceState | None, device: DeviceConfig) -> int:
    """+1 = raise the set-point (heating), -1 = lower it (cooling), 0 = do not shift."""
    mode = state.mode if state is not None else None
    if mode is None or mode == HVAC_OFF:
        mode = device.switch_on_hvac_mode
    if mode in HEATING_MODES:
        return 1
    if mode in COOLING_MODES:
        return -1
    return 0


def _climate_levels(
    device: DeviceConfig,
    control: SetpointControl,
    state: DeviceState | None,
    estimate: PowerEstimate | None,
    quantiser: Quantiser,
    *,
    base_setpoint_c: float | None,
    switched_on_by_us: bool,
) -> LevelTable | None:
    nominal = nominal_power_w(device, estimate)
    comfort = device.comfort
    if nominal is None or comfort is None or state is None or not state.known:
        return None
    is_on = state.on is True or (state.on is None and state.mode not in (None, HVAC_OFF))
    direction = shift_direction(state, device)
    base = base_setpoint_c if base_setpoint_c is not None else state.setpoint_c
    levels: list[Level] = []
    if not is_on or switched_on_by_us:
        # Level 0 = off; only reachable/leavable with the opt-in.
        if not device.allow_switch_on:
            return None
        levels.append(Level(0, 0.0, SwitchAction(on=False)))
        # An unit that is already on is brought back to the user's base set-point (the way
        # down from a shift runs through this level); only an unit that is off is switched on.
        on_action: Action = (
            SetpointAction(setpoint_c=base, base_c=base)
            if is_on and base is not None
            else SwitchAction(on=True)
        )
        levels.append(Level(1, nominal, on_action, setpoint_c=base))
        switchable = True
    else:
        if base is None or not comfort.min_c <= base <= comfort.max_c:
            # A base outside the comfort band could not be restored through the gate's
            # comfort invariant: SunCore leaves such a unit alone.
            return None
        levels.append(Level(0, 0.0, SetpointAction(setpoint_c=base, base_c=base), setpoint_c=base))
        switchable = False
    if direction != 0 and base is not None:
        step = quantiser.setpoint_step_c(device.id) or control.step_c
        shifts = math.floor(control.shift_max_c / control.step_c + SETPOINT_EPS)
        seen = {lv.setpoint_c for lv in levels if lv.setpoint_c is not None}
        for k in range(1, shifts + 1):
            target = snap_setpoint(
                base + direction * k * control.step_c,
                base=base,
                step=step,
                minimum=comfort.min_c,
                maximum=comfort.max_c,
            )
            # Clamping to the comfort band can move a base outside the band *against*
            # the direction (more demand would then be a lower heat set-point): skip.
            if target in seen or direction * (target - base) < SETPOINT_EPS:
                continue
            seen.add(target)
            levels.append(
                Level(
                    len(levels),
                    nominal,
                    SetpointAction(setpoint_c=target, base_c=base),
                    setpoint_c=target,
                )
            )
    if len(levels) < 2:
        return None
    return LevelTable(tuple(levels), ramped=True, base_setpoint_c=base, switchable=switchable)


# --- observing ----------------------------------------------------------------------


def observed_level(  # noqa: PLR0911, PLR0912 - one branch per control kind
    table: LevelTable, device: DeviceConfig, state: DeviceState | None
) -> int | None:
    """The level the device is at according to its observed state (``None`` = unknown)."""
    if state is None or not state.known:
        return None
    control = device.control
    if isinstance(control, OnOffControl):
        return None if state.on is None else int(state.on)
    if isinstance(control, StepsControl | RelaysControl):
        return state.step if state.step is not None and state.step <= table.top else None
    if isinstance(control, ModeControl):
        for level in table.levels:
            if isinstance(level.action, ModeAction) and level.action.mode == state.mode:
                return level.index
        return None
    if isinstance(control, ContinuousControl):
        if state.value is None:
            return None
        power = value_to_power_w(state.value, control, device.power_model.nominal_w)
        return _nearest_level(table, power)
    if isinstance(control, SetpointControl):
        is_on = state.on is True or (state.on is None and state.mode not in (None, HVAC_OFF))
        if not is_on:
            return 0 if table.switchable else None
        if state.setpoint_c is None:
            return None
        for level in table.levels:
            if level.setpoint_c is not None and abs(level.setpoint_c - state.setpoint_c) < 0.05:
                return level.index
        return None
    return None  # pragma: no cover - exhaustive


def _nearest_level(table: LevelTable, power_w: float) -> int:
    best = table.levels[0]
    for level in table.levels:
        if abs(level.power_w - power_w) < abs(best.power_w - power_w):
            best = level
    return best.index


__all__ = [
    "CallableQuantiser",
    "ConfigQuantiser",
    "Level",
    "LevelTable",
    "Quantiser",
    "build_levels",
    "nominal_power_w",
    "observed_level",
    "shift_direction",
]
