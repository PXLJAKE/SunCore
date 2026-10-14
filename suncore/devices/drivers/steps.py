# SPDX-License-Identifier: Apache-2.0
"""Stepped loads: one ``select`` / ``number`` entity, or 1-3 relay ``switch`` entities.

``StepAction(step=i)`` selects ``control.steps[i]``:

* ``steps`` control on a ``select`` entity -> ``select.select_option`` with
  ``option = str(step.value)`` (core rejects an option that is not in the entity's
  ``options``, case-sensitive);
* ``steps`` control on a ``number`` entity -> ``number.set_value`` with
  ``value = float(step.value)`` (core checks min/max, not the step);
* ``relays`` control -> for each relay entity ``switch.turn_on`` / ``switch.turn_off`` so that
  exactly the relays listed in ``steps[i].relays`` are on. Relays that must go off are switched off
  *first*, so the load never exceeds the target step in between. Only relays whose cached
  state differs are written.

Read-back maps the state to the matching ``StepAction``; a value that matches no step is
reported as ``ModeAction`` (select option / number value / ``relays:<roles>``), a different
kind, so override detection sees "the user changed it" and the release can restore it.
"""

from __future__ import annotations

from typing import Final

from suncore.core.control import DeviceState
from suncore.core.decisions import Action, Intent, ModeAction, ReleaseAction, StepAction
from suncore.core.devices import DeviceConfig, RelaysControl, StepsControl
from suncore.core.signals import Quality
from suncore.devices.drivers.base import (
    EntityDriver,
    ServiceWriter,
    StateLookup,
    WriteRefusedError,
    as_float,
    domain_of,
)

VALUE_TOLERANCE: Final = 1e-6
RELAYS_PREFIX: Final = "relays:"


class StepsDriver(EntityDriver):
    """Select a power step of a ``steps`` or ``relays`` control."""

    def __init__(self, device: DeviceConfig, *, write: ServiceWriter, states: StateLookup) -> None:
        control = device.control
        if not isinstance(control, StepsControl | RelaysControl):
            msg = f"StepsDriver needs a steps or relays control, got {control.kind}"
            raise TypeError(msg)
        super().__init__(device, write=write, states=states)
        self._control = control
        if isinstance(control, RelaysControl):
            self._relays = {role: self.entity_id(role) for role in control.roles}
            for entity in self._relays.values():
                if domain_of(entity) != "switch":
                    msg = f"relay {entity} must be a switch"
                    raise ValueError(msg)
            self._entity = ""
            self._is_number = False
        else:
            self._relays = {}
            self._entity = self.entity_id(control.role)
            self._is_number = domain_of(self._entity) == "number"
            if domain_of(self._entity) not in ("select", "number"):
                msg = f"StepsDriver needs a select or number entity, got {self._entity}"
                raise ValueError(msg)

    # --- reading ------------------------------------------------------------------------

    async def read_back(self) -> Action | None:
        return self._read_sync()

    def _on_relays(self) -> frozenset[str] | None:
        """Roles of the relays that are on; ``None`` if any relay state is unusable."""
        on: set[str] = set()
        for role, entity in self._relays.items():
            view = self.lookup(entity)
            if view is None or view.state not in ("on", "off"):
                return None
            if view.state == "on":
                on.add(role)
        return frozenset(on)

    def _read_relays(self, control: RelaysControl) -> Action | None:
        on = self._on_relays()
        if on is None:
            return None
        for index, step in enumerate(control.steps):
            if frozenset(step.relays) == on:
                return StepAction(step=index)
        ordered = [r for r in control.roles if r in on]
        return ModeAction(mode=f"{RELAYS_PREFIX}{','.join(ordered)}"[:64])

    def device_state(self) -> DeviceState:
        # `read_back` is async by protocol but touches only the cache; reuse its mapping.
        action = self._read_sync()
        if action is None:
            return DeviceState(quality=Quality.UNAVAILABLE)
        if isinstance(action, StepAction):
            return DeviceState(step=action.step, on=self._step_on(action.step))
        return DeviceState(on=None, mode=action.mode if isinstance(action, ModeAction) else None)

    def _read_sync(self) -> Action | None:
        if isinstance(self._control, RelaysControl):
            return self._read_relays(self._control)
        view = self.lookup(self._entity)
        if view is None:
            return None
        control = self._control
        assert isinstance(control, StepsControl)
        value = as_float(view.state) if self._is_number else None
        if self._is_number and value is None:
            return None
        for index, step in enumerate(control.steps):
            if self._is_number:
                target = as_float(step.value)
                matches = (
                    value is not None
                    and target is not None
                    and abs(value - target) <= VALUE_TOLERANCE
                )
            else:
                matches = view.state == str(step.value)
            if matches:
                return StepAction(step=index)
        return ModeAction(mode=view.state[:64])

    def _step_on(self, step: int) -> bool:
        return self._control.steps[step].power_w > 0

    # --- writing ------------------------------------------------------------------------

    async def apply(self, intent: Intent) -> str | None:
        action = intent.action
        if not isinstance(action, StepAction):
            raise self.unsupported(intent)
        if action.step >= len(self._control.steps):
            raise WriteRefusedError(f"step {action.step} does not exist")
        self.begin_apply()
        return await self._select(action.step)

    async def _select(self, index: int) -> str | None:
        if isinstance(self._control, RelaysControl):
            return await self._set_relays(frozenset(self._control.steps[index].relays))
        control = self._control
        assert isinstance(control, StepsControl)
        view = self.lookup(self._entity)
        if view is None:
            raise WriteRefusedError(f"{self._entity} is unavailable")
        step = control.steps[index]
        if self._is_number:
            target = as_float(step.value)
            if target is None:
                raise WriteRefusedError(f"step value {step.value!r} is not a number")
            current = as_float(view.state)
            if current is not None and abs(current - target) <= VALUE_TOLERANCE:
                return None
            await self.call("number", "set_value", self._entity, {"value": target})
        else:
            option = str(step.value)
            if view.state == option:
                return None  # write only on change
            await self.call("select", "select_option", self._entity, {"option": option})
        return self.last_context_id()

    async def _set_relays(self, wanted: frozenset[str]) -> str | None:
        current = self._on_relays()
        if current is None:
            raise WriteRefusedError("a relay is unavailable")
        # Off first (never exceed the target in between), then on.
        for role in (r for r in self._relays if r in current and r not in wanted):
            await self.call(domain_of(self._relays[role]), "turn_off", self._relays[role])
        for role in (r for r in self._relays if r in wanted and r not in current):
            await self.call(domain_of(self._relays[role]), "turn_on", self._relays[role])
        return self.last_context_id()

    async def release(self, baseline: Action | None) -> None:
        self.begin_apply()
        if baseline is None or isinstance(baseline, ReleaseAction):
            return
        if isinstance(baseline, StepAction):
            if baseline.step < len(self._control.steps):
                await self._select(baseline.step)
            return
        if not isinstance(baseline, ModeAction):
            return
        # The user's own value, which matched no step: put it back verbatim.
        if isinstance(self._control, RelaysControl):
            if baseline.mode.startswith(RELAYS_PREFIX):
                roles = [r for r in baseline.mode[len(RELAYS_PREFIX) :].split(",") if r]
                await self._set_relays(frozenset(r for r in roles if r in self._relays))
            return
        view = self.lookup(self._entity)
        if view is None:
            raise WriteRefusedError(f"{self._entity} is unavailable")
        if self._is_number:
            target = as_float(baseline.mode)
            current = as_float(view.state)
            if target is not None and (current is None or abs(current - target) > VALUE_TOLERANCE):
                await self.call("number", "set_value", self._entity, {"value": target})
        elif view.state != baseline.mode:
            await self.call("select", "select_option", self._entity, {"option": baseline.mode})
