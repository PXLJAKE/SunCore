# SPDX-License-Identifier: Apache-2.0
"""Nominal power: what the configuration says the device draws in its current state.

* on/off: ``on`` -> ``nominal_w``, ``off`` -> 0;
* steps / relays: the step's ``power_w``; modes: the mode's ``power_w``;
* continuous: the written value converted from W, A (``voltage_v`` x ``phases``) or %
  (of ``nominal_w``);
* set-point (climate): ``on`` -> ``nominal_w`` with a wide band (the compressor load
  follows the room, which a nominal model cannot know).

The uncertainty reflects how trustworthy a catalogue figure is: 20 % for a fixed load, 50 %
for a climate unit, and the full nominal power when the on/off state is unknown.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final

from suncore.core.control import DeviceState, PowerEstimate
from suncore.core.devices import (
    ContinuousControl,
    ControlSpec,
    ModeControl,
    PowerModelKind,
    SetpointControl,
    StepsControl,
    step_power_w,
)
from suncore.core.signals import Quality
from suncore.devices.conversion import value_to_power_w
from suncore.devices.power.common import (
    STANDBY_UNCERTAINTY_W,
    Samples,
    make_estimate,
    worst_quality,
)

FIXED_LOAD_REL: Final = 0.2
CLIMATE_REL: Final = 0.5
OFF_MODES: Final = frozenset({"off"})


class NominalPowerModel:
    """Power from configured figures; needs no sensor."""

    kind = PowerModelKind.NOMINAL

    def __init__(self, nominal_w: float | None, control: ControlSpec) -> None:
        self._nominal_w = nominal_w
        self._control = control

    @property
    def inputs(self) -> tuple[str, ...]:
        return ()

    def observe(self, now: datetime, state: DeviceState | None, samples: Samples) -> None:
        """Nothing to learn."""

    def estimate(
        self,
        now: datetime,  # noqa: ARG002
        state: DeviceState | None,
        samples: Samples,  # noqa: ARG002
    ) -> PowerEstimate:
        nominal = self._nominal_w
        if state is None or not state.known:
            full = nominal if nominal is not None else self._max_step_w()
            return make_estimate(0.0, full, Quality.UNAVAILABLE if state is None else state.quality)
        quality = worst_quality(state.quality, Quality.OK)
        value, rel = self._power_and_rel(state)
        if value is None:
            return make_estimate(0.0, nominal or 0.0, worst_quality(quality, Quality.UNAVAILABLE))
        unc = STANDBY_UNCERTAINTY_W if value == 0 else value * rel
        return make_estimate(value, unc, quality)

    def _max_step_w(self) -> float:
        control = self._control
        if isinstance(control, StepsControl):
            return control.steps[-1].power_w
        if isinstance(control, ModeControl):
            return max(m.power_w for m in control.modes)
        return 0.0

    def _power_and_rel(self, state: DeviceState) -> tuple[float | None, float]:  # noqa: PLR0911
        control = self._control
        nominal = self._nominal_w
        if isinstance(control, SetpointControl):
            return self._on_off_power(state, nominal), CLIMATE_REL
        if isinstance(control, ModeControl):
            if state.mode is not None:
                if state.mode in OFF_MODES:
                    return 0.0, FIXED_LOAD_REL
                for mode in control.modes:
                    if mode.value == state.mode:
                        return mode.power_w, FIXED_LOAD_REL
            return self._on_off_power(state, nominal), CLIMATE_REL
        if isinstance(control, ContinuousControl):
            if state.value is not None:
                return value_to_power_w(state.value, control, nominal), FIXED_LOAD_REL
            return self._on_off_power(state, nominal), FIXED_LOAD_REL
        if state.step is not None:
            step = step_power_w(control, state.step)
            if step is not None:
                return step, FIXED_LOAD_REL
        return self._on_off_power(state, nominal), FIXED_LOAD_REL

    @staticmethod
    def _on_off_power(state: DeviceState, nominal: float | None) -> float | None:
        on = state.on
        if on is None and state.mode is not None:
            on = state.mode not in OFF_MODES
        if on is None:
            return None
        if not on:
            return 0.0
        return nominal

    def dump_state(self) -> dict[str, Any]:
        return {}

    def load_state(self, data: Mapping[str, Any]) -> None:
        del data


__all__ = ["NominalPowerModel"]
