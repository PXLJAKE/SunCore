# SPDX-License-Identifier: Apache-2.0
"""On/off driver for ``switch`` and ``input_boolean`` entities.

Services (verified against home-assistant/core, research/ha-websocket-api.md): ``turn_on`` /
``turn_off`` of the entity's own domain, target only, no fields. HA accepts the call for
an unavailable entity without doing anything, so the driver refuses to write when the cache
shows the entity as unavailable and the gate verifies every write by reading back.
"""

from __future__ import annotations

from typing import Final

from suncore.core.control import DeviceState
from suncore.core.decisions import Action, Intent, ReleaseAction, SwitchAction
from suncore.core.devices import DeviceConfig, OnOffControl
from suncore.core.signals import Quality
from suncore.devices.drivers.base import (
    EntityDriver,
    ServiceWriter,
    StateLookup,
    WriteRefusedError,
    domain_of,
)

SUPPORTED_DOMAINS: Final = frozenset({"switch", "input_boolean"})


class OnOffDriver(EntityDriver):
    """Switch a ``switch`` / ``input_boolean`` entity on or off."""

    def __init__(self, device: DeviceConfig, *, write: ServiceWriter, states: StateLookup) -> None:
        if not isinstance(device.control, OnOffControl):
            msg = f"OnOffDriver needs an on_off control, got {device.control.kind}"
            raise TypeError(msg)
        super().__init__(device, write=write, states=states)
        self._entity = self.entity_id(device.control.role)
        if domain_of(self._entity) not in SUPPORTED_DOMAINS:
            msg = f"OnOffDriver supports {sorted(SUPPORTED_DOMAINS)}, got {self._entity}"
            raise ValueError(msg)

    async def apply(self, intent: Intent) -> str | None:
        action = intent.action
        if isinstance(action, ReleaseAction) or not isinstance(action, SwitchAction):
            raise self.unsupported(intent)
        self.begin_apply()
        return await self._set(action.on)

    async def _set(self, on: bool) -> str | None:
        view = self.lookup(self._entity)
        if view is None:
            raise WriteRefusedError(f"{self._entity} is unavailable")
        if (view.state == "on") == on and view.state in ("on", "off"):
            return None  # write only on change
        await self.call(domain_of(self._entity), "turn_on" if on else "turn_off", self._entity)
        return self.last_context_id()

    async def read_back(self) -> Action | None:
        view = self.lookup(self._entity)
        if view is None or view.state not in ("on", "off"):
            return None
        return SwitchAction(on=view.state == "on")

    def device_state(self) -> DeviceState:
        view = self.lookup(self._entity)
        if view is None or view.state not in ("on", "off"):
            return DeviceState(quality=Quality.UNAVAILABLE)
        return DeviceState(on=view.state == "on")

    async def release(self, baseline: Action | None) -> None:
        if isinstance(baseline, SwitchAction):
            self.begin_apply()
            await self._set(baseline.on)
