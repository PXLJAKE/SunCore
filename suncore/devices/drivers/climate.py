# SPDX-License-Identifier: Apache-2.0
"""Climate driver: set-point shifts and (opt-in) switching through the HVAC mode.

Verified behaviour (research/ha-websocket-api.md "Write services and contexts (Phase 3)" and
device-integrations.md "Climate write behaviour (Phase 3)", home-assistant/core ``c433453``):

* ``climate.set_temperature`` takes ``temperature`` (HA unit system); core checks only
  ``min_temp``..``max_temp``, no step. ``lg_thinq`` turns a unit that is **off** on when it gets
  a set-point (``POWER_ON``, 2 s sleep, then the target) and truncates with ``int()`` when the
  device step is >= 1. Therefore:

  - **a set-point is never written while ``hvac_mode`` is ``off``** (or unknown): the write
    would switch the unit on behind the gate's back;
  - set-points are rounded to the device step here (``target_temp_step`` attribute, else the
    control's ``step_c``) toward the user's base value, and clamped to ``min_temp``/``max_temp``;
* switching on is ``climate.set_hvac_mode`` with the device's ``switch_on_hvac_mode`` (checked
  against the entity's ``hvac_modes``); ``climate.turn_on`` only as a fallback when that mode
  is not offered but the entity advertises ``TURN_ON`` (256) and ``allow_switch_on`` is set;
* switching off is ``climate.turn_off`` when the entity advertises ``TURN_OFF`` (128), else
  ``climate.set_hvac_mode`` with ``off``;
* ``hvac_mode`` is never passed to ``set_temperature``.

Read-back kinds. The gate compares the read-back with the *kind* of action it wrote, so the
driver reports ``SwitchAction`` after a switch intent and ``SetpointAction`` after a
set-point intent (or before anything was written: that is the takeover baseline). An off
unit is always ``SwitchAction(on=False)``.

``UNVERIFIED`` (docs/STATUS.md, "Known problems and UNVERIFIED items"): only the ``lg_thinq`` paths
come from source reading, every other climate integration is simulator-tested only; no live write
has been made yet. Open in detail: ``lg_thinq`` raising ``TypeError`` from ``set_temperature`` when
the device reports ``step is None``; the push latency after a command (the gate's settle time and
the late confirmation absorb it, the real value is unmeasured); the ``climate.turn_on`` fallback
for a ``switch_on_hvac_mode`` that is not in ``hvac_modes``.
"""

from __future__ import annotations

import math
from typing import Final

from suncore.core.control import DeviceState
from suncore.core.decisions import (
    Action,
    Intent,
    ReleaseAction,
    SetpointAction,
    SwitchAction,
)
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

TARGET_TEMPERATURE: Final = 1
"""``ClimateEntityFeature.TARGET_TEMPERATURE``."""
TURN_OFF: Final = 128
"""``ClimateEntityFeature.TURN_OFF``."""
TURN_ON: Final = 256
"""``ClimateEntityFeature.TURN_ON``."""
HVAC_OFF: Final = "off"
SAME_SETPOINT_C: Final = 1e-6


class ClimateDriver(EntityDriver):
    """Drive one ``climate`` entity."""

    def __init__(self, device: DeviceConfig, *, write: ServiceWriter, states: StateLookup) -> None:
        if not isinstance(device.control, SetpointControl):
            msg = f"ClimateDriver needs a setpoint control, got {device.control.kind}"
            raise TypeError(msg)
        super().__init__(device, write=write, states=states)
        self._control = device.control
        self._entity = self.entity_id(device.control.role)
        if not self._entity.startswith("climate."):
            msg = f"ClimateDriver needs a climate entity, got {self._entity}"
            raise ValueError(msg)
        self._report_switch = False
        self._last_on_mode: str | None = None
        self._switched_off_by_us = False

    # --- reading ------------------------------------------------------------------------

    def _view(self) -> EntityStateView | None:
        return self.lookup(self._entity)

    def _features(self, view: EntityStateView) -> int:
        value = as_float(view.attributes.get("supported_features"))
        return int(value) if value is not None else 0

    def _hvac_modes(self, view: EntityStateView) -> list[str] | None:
        modes = view.attributes.get("hvac_modes")
        return [str(m) for m in modes] if isinstance(modes, list | tuple) else None

    def effective_step_c(self, view: EntityStateView | None = None) -> float:
        """Set-point step in force: the entity's ``target_temp_step``, else the control's."""
        view = view if view is not None else self._view()
        step = as_float(view.attributes.get("target_temp_step")) if view is not None else None
        return step if step is not None and step > 0 else self._control.step_c

    def _remember(self, view: EntityStateView) -> None:
        """Remember the last on-mode (to restore it) and that the unit is on again."""
        if view.state != HVAC_OFF:
            self._last_on_mode = view.state
            self._switched_off_by_us = False

    async def read_back(self) -> Action | None:
        view = self._view()
        if view is None:
            return None
        self._remember(view)
        if view.state == HVAC_OFF:
            return SwitchAction(on=False)
        target = as_float(view.attributes.get("temperature"))
        if self._report_switch or target is None:
            return SwitchAction(on=True)
        return SetpointAction(setpoint_c=target, base_c=target)

    def device_state(self) -> DeviceState:
        view = self._view()
        if view is None:
            return DeviceState(quality=Quality.UNAVAILABLE)
        self._remember(view)
        return DeviceState(
            on=view.state != HVAC_OFF,
            mode=view.state,
            setpoint_c=as_float(view.attributes.get("temperature")),
        )

    # --- writing ------------------------------------------------------------------------

    async def apply(self, intent: Intent) -> str | None:
        action = intent.action
        self.begin_apply()
        if isinstance(action, SetpointAction):
            context = await self._set_setpoint(action)
            self._report_switch = False
            return context
        if isinstance(action, SwitchAction):
            context = await self._set_on(on=action.on)
            self._report_switch = True
            return context
        raise self.unsupported(intent)

    async def _set_setpoint(self, action: SetpointAction) -> str | None:
        view = self._view()
        if view is None:
            raise WriteRefusedError(f"{self._entity} is unavailable")
        if view.state == HVAC_OFF:
            # lg_thinq (and others) switch a unit on when it gets a set-point.
            raise WriteRefusedError(f"{self._entity} is off: a set-point would switch it on")
        features = self._features(view)
        if "supported_features" in view.attributes and not features & TARGET_TEMPERATURE:
            raise WriteRefusedError(f"{self._entity} has no target temperature")
        step = self.effective_step_c(view)
        target = snap_setpoint(
            action.setpoint_c,
            base=action.base_c,
            step=step,
            minimum=as_float(view.attributes.get("min_temp")),
            maximum=as_float(view.attributes.get("max_temp")),
        )
        current = as_float(view.attributes.get("temperature"))
        if current is not None and math.isclose(current, target, abs_tol=SAME_SETPOINT_C):
            return None  # write only on change
        await self.call("climate", "set_temperature", self._entity, {"temperature": target})
        return self.last_context_id()

    async def _set_on(self, *, on: bool, mode: str | None = None) -> str | None:
        view = self._view()
        if view is None:
            raise WriteRefusedError(f"{self._entity} is unavailable")
        is_on = view.state != HVAC_OFF
        if is_on == on:
            return None  # write only on change
        features = self._features(view)
        if not on:
            if features & TURN_OFF:
                await self.call("climate", "turn_off", self._entity)
            else:
                await self.call("climate", "set_hvac_mode", self._entity, {"hvac_mode": HVAC_OFF})
            self._switched_off_by_us = True
            return self.last_context_id()
        if not self._device.allow_switch_on:
            raise WriteRefusedError(f"{self._entity}: switching on is not allowed for this device")
        wanted = mode or self._device.switch_on_hvac_mode
        modes = self._hvac_modes(view)
        if wanted is not None and (modes is None or wanted in modes):
            await self.call("climate", "set_hvac_mode", self._entity, {"hvac_mode": wanted})
        elif features & TURN_ON:
            await self.call("climate", "turn_on", self._entity)
        else:
            raise WriteRefusedError(f"{self._entity} cannot be switched on with {wanted!r}")
        return self.last_context_id()

    # --- release ------------------------------------------------------------------------

    async def release(self, baseline: Action | None) -> None:
        """Restore the takeover baseline (idempotent).

        * baseline ``SetpointAction`` (the unit was on): if it is still on, write the user's
          set-point; if it is off now, switch it back on in the mode it had - but only when
          SunCore switched it off in this process or the device has ``allow_switch_on``. A
          unit the user switched off stays off; a bare ``set_temperature`` is never sent to an
          off unit;
        * baseline ``SwitchAction`` off: switch off; on: as above.
        """
        self.begin_apply()
        self._report_switch = False
        if baseline is None or isinstance(baseline, ReleaseAction):
            return
        if isinstance(baseline, SetpointAction):
            view = self._view()
            if view is not None and view.state == HVAC_OFF:
                await self._restore_on()
            else:
                await self._set_setpoint(baseline)
        elif isinstance(baseline, SwitchAction):
            if baseline.on:
                await self._restore_on()
            else:
                await self._set_on(on=False)

    async def _restore_on(self) -> None:
        """Switch back on in the mode the unit had at takeover, if that is ours to undo."""
        view = self._view()
        if view is None:
            raise WriteRefusedError(f"{self._entity} is unavailable")
        if view.state != HVAC_OFF:
            return
        if not (self._device.allow_switch_on or self._switched_off_by_us):
            return  # the user switched it off: respect that
        mode = self._last_on_mode or self._device.switch_on_hvac_mode
        modes = self._hvac_modes(view)
        features = self._features(view)
        if mode is not None and (modes is None or mode in modes):
            await self.call("climate", "set_hvac_mode", self._entity, {"hvac_mode": mode})
        elif features & TURN_ON:
            await self.call("climate", "turn_on", self._entity)
        else:
            raise WriteRefusedError(f"{self._entity} cannot be switched back on")
        self._switched_off_by_us = False


__all__ = ["ClimateDriver"]
