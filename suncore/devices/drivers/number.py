# SPDX-License-Identifier: Apache-2.0
"""Continuous loads written through a ``number`` entity (W, A or %).

``number.set_value`` takes ``value``; core checks it against the entity's ``min``/``max`` (state
unit) and clamps to the native range but does **not** enforce ``step``
(research/ha-websocket-api.md). The driver therefore

* converts the requested power to the control's unit (W; A with ``voltage_v`` x ``phases``;
  % of ``nominal_w``) and, for a ``kW`` entity, to kW;
* clamps to the control's ``min``/``max`` and to the entity's ``min``/``max`` attributes;
* snaps *down* to the entity's ``step`` attribute (else the control's ``step``), measured from
  the minimum: a load never gets more than was asked for;
* writes only if the cached value differs.

A control whose ``min`` is above zero cannot be switched off through the number; a request for
less than ``min`` writes ``min``.
"""

from __future__ import annotations

import math
from typing import Final

from suncore.core.control import DeviceState
from suncore.core.decisions import Action, Intent, PowerSetpointAction
from suncore.core.devices import ContinuousControl, DeviceConfig
from suncore.core.signals import Quality
from suncore.core.units import Unit, parse_unit
from suncore.devices.conversion import power_to_value, value_to_power_w
from suncore.devices.drivers.base import (
    EntityDriver,
    EntityStateView,
    ServiceWriter,
    StateLookup,
    WriteRefusedError,
    as_float,
)

SAME_VALUE: Final = 1e-6
EPSILON: Final = 1e-9


class NumberDriver(EntityDriver):
    """Drive a ``number`` entity that sets power, current or a percentage."""

    def __init__(self, device: DeviceConfig, *, write: ServiceWriter, states: StateLookup) -> None:
        control = device.control
        if not isinstance(control, ContinuousControl):
            msg = f"NumberDriver needs a continuous control, got {control.kind}"
            raise TypeError(msg)
        super().__init__(device, write=write, states=states)
        self._control = control
        self._entity = self.entity_id(control.role)
        if not self._entity.startswith("number."):
            msg = f"NumberDriver needs a number entity, got {self._entity}"
            raise ValueError(msg)
        self._nominal_w = device.power_model.nominal_w

    # --- conversion ---------------------------------------------------------------------

    def _entity_factor(self, view: EntityStateView) -> float:
        """Entity value = control value x factor (only ``kW`` entities for a W control)."""
        unit = parse_unit(str(view.attributes.get("unit_of_measurement", "")))
        if self._control.unit == "W" and unit is Unit.KW:
            return 0.001
        return 1.0

    # --- reading ------------------------------------------------------------------------

    def _control_value(self, view: EntityStateView) -> float | None:
        raw = as_float(view.state)
        return None if raw is None else raw / self._entity_factor(view)

    async def read_back(self) -> Action | None:
        view = self.lookup(self._entity)
        value = self._control_value(view) if view is not None else None
        if value is None:
            return None
        return PowerSetpointAction(power_w=value_to_power_w(value, self._control, self._nominal_w))

    def device_state(self) -> DeviceState:
        view = self.lookup(self._entity)
        value = self._control_value(view) if view is not None else None
        if value is None:
            return DeviceState(quality=Quality.UNAVAILABLE)
        return DeviceState(value=value, on=value > 0)

    # --- writing ------------------------------------------------------------------------

    async def apply(self, intent: Intent) -> str | None:
        action = intent.action
        if not isinstance(action, PowerSetpointAction):
            raise self.unsupported(intent)
        self.begin_apply()
        return await self._write_power(action.power_w, limit=True)

    async def _write_power(self, power_w: float, *, limit: bool) -> str | None:
        view = self.lookup(self._entity)
        if view is None:
            raise WriteRefusedError(f"{self._entity} is unavailable")
        if not math.isfinite(power_w):
            raise WriteRefusedError("power set-point is not finite")
        control = self._control
        factor = self._entity_factor(view)
        value = power_to_value(power_w, control, self._nominal_w)
        if limit:
            value = min(max(value, control.min), control.max)
        entity_min = as_float(view.attributes.get("min"))
        entity_max = as_float(view.attributes.get("max"))
        low = max(control.min, entity_min / factor if entity_min is not None else control.min)
        high = min(control.max, entity_max / factor if entity_max is not None else control.max)
        step = as_float(view.attributes.get("step"))
        step = step / factor if step is not None and step > 0 else control.step
        if limit:
            value = min(max(value, low), high)
            if step is not None and step > 0:
                value = low + math.floor((value - low) / step + EPSILON) * step
        target = round(value * factor, 6)
        current = as_float(view.state)
        if current is not None and math.isclose(current, target, abs_tol=SAME_VALUE):
            return None  # write only on change
        await self.call("number", "set_value", self._entity, {"value": target})
        return self.last_context_id()

    async def release(self, baseline: Action | None) -> None:
        self.begin_apply()
        if isinstance(baseline, PowerSetpointAction):
            await self._write_power(baseline.power_w, limit=False)
