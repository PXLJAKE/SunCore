# SPDX-License-Identifier: Apache-2.0
"""Set-point lift on a ``water_heater`` entity (hot water of a heat pump, PR 6-K).

Verified against ``home-assistant/core`` ``c433453`` (``water_heater/__init__.py`` and
``water_heater/const.py``, fetched 2026-11-24; research/ha-websocket-api.md):

* the entity **state is ``current_operation``** (an operation mode such as ``eco``,
  ``heat_pump``, ``off``; ``unknown`` when the entity has no operation mode);
* state attributes ``temperature`` (the target set-point), ``current_temperature``,
  ``min_temp``, ``max_temp``, ``target_temp_step`` (only when the entity has one);
* ``water_heater.set_temperature`` takes ``temperature`` (required); core does **not** range
  check it, so this driver clamps to ``min_temp``/``max_temp`` itself.

Only ``SetpointAction`` is written (the lift above the user's base inside the comfort band); the
driver never switches the heater on or off and never sends a set-point while the operation is
``off``. Read-back is ``SetpointAction`` (the target) or ``SwitchAction(on=False)`` while off.
Release writes the takeover set-point back (only while the heater is not off).

``UNVERIFIED``: no real heat-pump water heater has been driven (simulator only); vendors may
round or reject set-points the entity advertises.
"""

from __future__ import annotations

import math
from typing import Final

from suncore.core.control import DeviceState
from suncore.core.decisions import Action, Intent, ReleaseAction, SetpointAction, SwitchAction
from suncore.core.devices import DeviceConfig, SetpointControl
from suncore.core.signals import Quality
from suncore.devices.drivers.base import (
    EntityDriver,
    EntityStateView,
    ServiceWriter,
    StateLookup,
    WriteRefusedError,
    as_float,
)
from suncore.devices.setpoint import snap_setpoint

OPERATION_OFF: Final = "off"
NO_OPERATION: Final = "unknown"
"""The state of a water heater without operation modes (``current_operation`` is ``None``)."""
SAME_SETPOINT_C: Final = 1e-6


class WaterHeaterDriver(EntityDriver):
    """Lift the set-point of one ``water_heater`` entity."""

    def __init__(self, device: DeviceConfig, *, write: ServiceWriter, states: StateLookup) -> None:
        if not isinstance(device.control, SetpointControl):
            msg = f"WaterHeaterDriver needs a setpoint control, got {device.control.kind}"
            raise TypeError(msg)
        super().__init__(device, write=write, states=states)
        self._control = device.control
        self._entity = self.entity_id(device.control.role)
        if not self._entity.startswith("water_heater."):
            msg = f"WaterHeaterDriver needs a water_heater entity, got {self._entity}"
            raise ValueError(msg)

    def _view(self) -> EntityStateView | None:
        view = self._states(self._entity)
        if view is None or view.state == "unavailable":
            return None
        if view.state == NO_OPERATION and as_float(view.attributes.get("temperature")) is None:
            return None
        return view

    def effective_step_c(self, view: EntityStateView | None = None) -> float:
        view = view if view is not None else self._view()
        step = as_float(view.attributes.get("target_temp_step")) if view is not None else None
        return step if step is not None and step > 0 else self._control.step_c

    async def read_back(self) -> Action | None:
        view = self._view()
        if view is None:
            return None
        if view.state == OPERATION_OFF:
            return SwitchAction(on=False)
        target = as_float(view.attributes.get("temperature"))
        if target is None:
            return None
        return SetpointAction(setpoint_c=target, base_c=target)

    def device_state(self) -> DeviceState:
        view = self._view()
        if view is None:
            return DeviceState(quality=Quality.UNAVAILABLE)
        on = view.state != OPERATION_OFF
        # The lift is a heating set-point: the controller shifts it upward only (``heat``).
        return DeviceState(
            on=on,
            mode="heat" if on else OPERATION_OFF,
            setpoint_c=as_float(view.attributes.get("temperature")),
        )

    async def apply(self, intent: Intent) -> str | None:
        action = intent.action
        if not isinstance(action, SetpointAction):
            raise self.unsupported(intent)
        self.begin_apply()
        return await self._set_setpoint(action)

    async def _set_setpoint(self, action: SetpointAction) -> str | None:
        view = self._view()
        if view is None:
            raise WriteRefusedError(f"{self._entity} is unavailable")
        if view.state == OPERATION_OFF:
            raise WriteRefusedError(f"{self._entity} is off: SunCore never switches it on")
        target = snap_setpoint(
            action.setpoint_c,
            base=action.base_c,
            step=self.effective_step_c(view),
            minimum=as_float(view.attributes.get("min_temp")),
            maximum=as_float(view.attributes.get("max_temp")),
        )
        current = as_float(view.attributes.get("temperature"))
        if current is not None and math.isclose(current, target, abs_tol=SAME_SETPOINT_C):
            return None  # write only on change
        await self.call("water_heater", "set_temperature", self._entity, {"temperature": target})
        return self.last_context_id()

    async def release(self, baseline: Action | None) -> None:
        self.begin_apply()
        if baseline is None or isinstance(baseline, ReleaseAction | SwitchAction):
            return  # nothing was lifted, or the heater was off: SunCore never switches it
        if isinstance(baseline, SetpointAction):
            view = self._view()
            if view is not None and view.state == OPERATION_OFF:
                return
            await self._set_setpoint(baseline)


__all__ = ["WaterHeaterDriver"]
