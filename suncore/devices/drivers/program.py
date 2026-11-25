# SPDX-License-Identifier: Apache-2.0
"""Driver of a job-driven shiftable program (PR 6-L, ADR-0017 decision 7).

Start methods (``DeviceConfig.program.start_method``):

* ``plug`` - a ``switch``/``input_boolean`` smart plug in front of the appliance. ``turn_on``
  starts it (the appliance resumes the selected programme after power returns: confirmed by the
  user, ``UNVERIFIED`` per appliance), ``turn_off`` only at arming while the appliance is idle
  (P4, judged by the gate). Release restores the takeover baseline, which the gate sets to
  **on** (P3); the gate never hands it a switch-off of a program that may be running (P2).
* ``remote`` - a ``switch`` that starts the appliance remotely (``UNVERIFIED`` per
  integration). Only ``turn_on`` is ever written; release writes nothing (switching such a
  switch off could abort the programme; whether it does is vendor-specific).
* ``notify`` - nothing is written: ``apply`` refuses, ``read_back`` and the state are unknown
  and release is a no-op. The user starts the appliance after a notification.

Services: ``turn_on`` / ``turn_off`` of the entity's own domain, target only (verified with the
on/off driver, research/ha-websocket-api.md). A ``button`` role (``button.press``) is not
supported yet (plan section 8, 6-L).
"""

from __future__ import annotations

from typing import Final

from suncore.core.control import DeviceState
from suncore.core.decisions import Action, Intent, SwitchAction
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


class ProgramDriver(EntityDriver):
    """Start (and, at arming, power down) a shiftable program; see the module docstring."""

    def __init__(self, device: DeviceConfig, *, write: ServiceWriter, states: StateLookup) -> None:
        spec = device.program
        if spec is None:
            msg = f"ProgramDriver needs a program spec, {device.id} has none"
            raise TypeError(msg)
        super().__init__(device, write=write, states=states)
        self._method = spec.start_method
        self._entity: str | None = None
        if self._method == "notify":
            return
        control = device.control
        if not isinstance(control, OnOffControl):
            msg = f"ProgramDriver: start method {self._method} needs an on_off control"
            raise TypeError(msg)
        self._entity = self.entity_id(control.role)
        if domain_of(self._entity) not in SUPPORTED_DOMAINS:
            msg = f"ProgramDriver supports {sorted(SUPPORTED_DOMAINS)}, got {self._entity}"
            raise ValueError(msg)

    @property
    def start_method(self) -> str:
        return self._method

    async def apply(self, intent: Intent) -> str | None:
        action = intent.action
        if self._entity is None or not isinstance(action, SwitchAction):
            raise self.unsupported(intent)
        if self._method == "remote" and not action.on:
            raise WriteRefusedError(f"{self._entity}: a remote start is never switched off")
        self.begin_apply()
        return await self._set(self._entity, action.on)

    async def _set(self, entity: str, on: bool) -> str | None:
        view = self.lookup(entity)
        if view is None:
            raise WriteRefusedError(f"{entity} is unavailable")
        if (view.state == "on") == on and view.state in ("on", "off"):
            return None  # write only on change
        await self.call(domain_of(entity), "turn_on" if on else "turn_off", entity)
        return self.last_context_id()

    async def read_back(self) -> Action | None:
        if self._entity is None:
            return None
        view = self.lookup(self._entity)
        if view is None or view.state not in ("on", "off"):
            return None
        return SwitchAction(on=view.state == "on")

    def device_state(self) -> DeviceState:
        if self._entity is None:
            # Notify-only: there is no switch; the power model carries what is known.
            return DeviceState(quality=Quality.OK)
        view = self.lookup(self._entity)
        if view is None or view.state not in ("on", "off"):
            return DeviceState(quality=Quality.UNAVAILABLE)
        return DeviceState(on=view.state == "on")

    async def release(self, baseline: Action | None) -> None:
        """Plug: restore the baseline the gate gives (on, P3; ``None`` = keep as it is).
        Remote and notify: nothing is written."""
        if self._method != "plug" or self._entity is None:
            return
        if isinstance(baseline, SwitchAction):
            self.begin_apply()
            await self._set(self._entity, baseline.on)


__all__ = ["ProgramDriver"]
