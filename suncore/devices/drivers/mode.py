# SPDX-License-Identifier: Apache-2.0
"""Mode-controlled devices: a ``select`` entity (``select.select_option``) or the preset of a
``climate`` entity (``climate.set_preset_mode`` with ``preset_mode``).

Core validates the option against the entity's ``options`` (select) and the preset against
``preset_modes`` (the service needs the ``PRESET_MODE`` feature). The driver additionally only
writes modes that the device's ``control.modes`` lists - the controller may choose nothing else -
and writes only on change. The release restores the user's own mode verbatim, even one that is
not in ``control.modes``.
"""

from __future__ import annotations

from suncore.core.control import DeviceState
from suncore.core.decisions import Action, Intent, ModeAction
from suncore.core.devices import DeviceConfig, ModeControl
from suncore.core.signals import Quality
from suncore.devices.drivers.base import (
    EntityDriver,
    ServiceWriter,
    StateLookup,
    WriteRefusedError,
    domain_of,
)


class ModeDriver(EntityDriver):
    """Select one of several modes."""

    def __init__(self, device: DeviceConfig, *, write: ServiceWriter, states: StateLookup) -> None:
        control = device.control
        if not isinstance(control, ModeControl):
            msg = f"ModeDriver needs a mode control, got {control.kind}"
            raise TypeError(msg)
        super().__init__(device, write=write, states=states)
        self._control = control
        self._entity = self.entity_id(control.role)
        self._domain = domain_of(self._entity)
        if self._domain not in ("select", "climate"):
            msg = f"ModeDriver needs a select or climate entity, got {self._entity}"
            raise ValueError(msg)

    def _current(self) -> str | None:
        view = self.lookup(self._entity)
        if view is None:
            return None
        if self._domain == "select":
            return view.state
        preset = view.attributes.get("preset_mode")
        return preset if isinstance(preset, str) and preset else None

    async def read_back(self) -> Action | None:
        current = self._current()
        return None if current is None else ModeAction(mode=current[:64])

    def device_state(self) -> DeviceState:
        current = self._current()
        if current is None:
            return DeviceState(quality=Quality.UNAVAILABLE)
        return DeviceState(mode=current[:64])

    async def apply(self, intent: Intent) -> str | None:
        action = intent.action
        if not isinstance(action, ModeAction):
            raise self.unsupported(intent)
        if action.mode not in {m.value for m in self._control.modes}:
            raise WriteRefusedError(f"mode {action.mode!r} is not configured for this device")
        self.begin_apply()
        return await self._set(action.mode)

    async def _set(self, mode: str) -> str | None:
        if self.lookup(self._entity) is None:
            raise WriteRefusedError(f"{self._entity} is unavailable")
        if self._current() == mode:
            return None  # write only on change
        if self._domain == "select":
            await self.call("select", "select_option", self._entity, {"option": mode})
        else:
            await self.call("climate", "set_preset_mode", self._entity, {"preset_mode": mode})
        return self.last_context_id()

    async def release(self, baseline: Action | None) -> None:
        self.begin_apply()
        if isinstance(baseline, ModeAction):
            await self._set(baseline.mode)
