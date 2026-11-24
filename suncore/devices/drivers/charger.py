# SPDX-License-Identifier: Apache-2.0
"""EV charger driven through Home Assistant entities (``ChargerControl``, ADR-0017; PR 6-I).

Roles (``DeviceConfig.entities``):

* ``control.role`` - the charging current, a ``number`` entity in A (``number.set_value`` with
  ``value``; core checks ``min``/``max`` but not ``step``, research/ha-websocket-api.md);
* ``control.enable_role`` (optional) - a ``switch``/``input_boolean`` that pauses and resumes
  charging (``turn_on``/``turn_off``);
* ``control.phases_role`` (optional) - a ``select``/``input_select`` (``select_option`` with
  ``option``) or a ``switch``/``input_boolean`` (``on``/``off``) whose value for one and three
  phases is ``phase_option_1``/``phase_option_3``;
* ``ev.vehicle_soc_role`` (sensor, %), ``ev.connected_role`` (binary sensor ``on``/``off``) -
  read only, for the controller and the gate (E5) and the planner.

``apply(ChargeAction)``: the phases first (only paused - the gate's E2), then a pause turns the
``enable`` switch off (without one it writes 0 A where the entity accepts it, otherwise it
refuses), a charge writes the current - snapped **down** to the entity's ``step`` and clamped to
its ``min``/``max`` - and then turns ``enable`` on. Every entity is written only on change.
``read_back`` is the full state: current, phases and the enable switch (``enabled``), so the
takeover baseline restores all three on release (E9).

``UNVERIFIED`` (docs/STATUS.md): exercised in the simulator only; how a real charger reacts to a
phase change while paused, and whether the current entity is in A on every integration, is
vendor-specific (template ``unverified`` lists).
"""

from __future__ import annotations

import math
from typing import Final

from suncore.core.control import DeviceState
from suncore.core.decisions import Action, ChargeAction, Intent
from suncore.core.devices import ChargerControl, DeviceConfig
from suncore.core.signals import Quality
from suncore.devices.drivers.base import (
    EntityDriver,
    ServiceWriter,
    StateLookup,
    WriteRefusedError,
    as_float,
    domain_of,
)

SWITCH_DOMAINS: Final = frozenset({"switch", "input_boolean"})
SELECT_DOMAINS: Final = frozenset({"select", "input_select"})
SAME_VALUE: Final = 1e-6
EPSILON: Final = 1e-9


class ChargerDriver(EntityDriver):
    """Write current, phases and the enable switch of a charger; read them back."""

    def __init__(self, device: DeviceConfig, *, write: ServiceWriter, states: StateLookup) -> None:
        control = device.control
        if not isinstance(control, ChargerControl):
            msg = f"ChargerDriver needs a charger control, got {control.kind}"
            raise TypeError(msg)
        super().__init__(device, write=write, states=states)
        self._control = control
        self._current = self.entity_id(control.role)
        if domain_of(self._current) != "number":
            msg = f"ChargerDriver needs a number entity for the current, got {self._current}"
            raise ValueError(msg)
        self._enable = None if control.enable_role is None else self.entity_id(control.enable_role)
        if self._enable is not None and domain_of(self._enable) not in SWITCH_DOMAINS:
            msg = f"ChargerDriver needs a switch for enable, got {self._enable}"
            raise ValueError(msg)
        self._phases = None if control.phases_role is None else self.entity_id(control.phases_role)
        if self._phases is not None and domain_of(self._phases) not in (
            SWITCH_DOMAINS | SELECT_DOMAINS
        ):
            msg = f"ChargerDriver needs a select or switch for phases, got {self._phases}"
            raise ValueError(msg)
        ev = device.ev
        soc_role = None if ev is None else ev.vehicle_soc_role
        self._soc = None if soc_role is None else self.entity_id(soc_role)
        self._connected = (
            None if ev is None or ev.connected_role is None else self.entity_id(ev.connected_role)
        )

    # --- reading ------------------------------------------------------------------------

    def _current_a(self) -> float | None:
        view = self.lookup(self._current)
        return None if view is None else as_float(view.state)

    def _enabled(self) -> bool | None:
        if self._enable is None:
            return None
        view = self.lookup(self._enable)
        if view is None or view.state not in ("on", "off"):
            return None
        return view.state == "on"

    def _phase_count(self) -> int | None:
        """1 or 3 from the phases entity; ``None`` without a role or for another value."""
        if self._phases is None:
            return None
        view = self.lookup(self._phases)
        if view is None:
            return None
        if view.state == self._control.phase_option_1:
            return 1
        if view.state == self._control.phase_option_3:
            return 3
        return None

    def current_range_a(self) -> tuple[float | None, float | None]:
        """The current entity's live ``min``/``max`` attributes (invariant E1)."""
        view = self.lookup(self._current)
        if view is None:
            return None, None
        return as_float(view.attributes.get("min")), as_float(view.attributes.get("max"))

    def snap_current_a(self, current_a: float) -> float:
        """The current the entity will really be set to for ``current_a`` (step snapped down
        from the entity minimum, clamped to the control's and the entity's range); 0 stays 0."""
        if not math.isfinite(current_a) or current_a <= 0:
            return 0.0
        control = self._control
        low, high = control.min_current_a, control.max_current_a
        view = self.lookup(self._current)
        step: float | None = None
        if view is not None:
            entity_min = as_float(view.attributes.get("min"))
            entity_max = as_float(view.attributes.get("max"))
            low = max(low, entity_min) if entity_min is not None else low
            high = min(high, entity_max) if entity_max is not None else high
            step = as_float(view.attributes.get("step"))
        value = min(max(current_a, low), high)
        if step is not None and step > 0:
            base = as_float(view.attributes.get("min")) if view is not None else None
            origin = base if base is not None else 0.0
            value = max(low, origin + math.floor((value - origin) / step + EPSILON) * step)
        return round(value, 6)

    async def read_back(self) -> Action | None:
        current = self._current_a()
        if current is None:
            return None
        if self._enable is not None and self._enabled() is None:
            return None
        phase_count = self._phase_count()
        if self._phases is not None and phase_count is None:
            return None
        return ChargeAction(
            current_a=max(0.0, current),
            phases=1 if phase_count == 1 else (3 if phase_count == 3 else None),
            enabled=self._enabled(),
        )

    def device_state(self) -> DeviceState:
        current = self._current_a()
        enabled = self._enabled()
        if current is None or (self._enable is not None and enabled is None):
            return DeviceState(quality=Quality.UNAVAILABLE)
        phase_count = self._phase_count()
        if self._phases is not None and phase_count is None:
            return DeviceState(quality=Quality.UNAVAILABLE)
        return DeviceState(
            value=current,
            on=current > 0 and enabled is not False,
            phases=1 if phase_count == 1 else (3 if phase_count == 3 else None),
            connected=self._binary(self._connected),
            soc_pct=self._soc_pct(),
        )

    def _binary(self, entity_id: str | None) -> bool | None:
        if entity_id is None:
            return None
        view = self.lookup(entity_id)
        if view is None or view.state not in ("on", "off"):
            return None
        return view.state == "on"

    def _soc_pct(self) -> float | None:
        if self._soc is None:
            return None
        view = self.lookup(self._soc)
        value = None if view is None else as_float(view.state)
        return value if value is not None and 0 <= value <= 100 else None

    # --- writing ------------------------------------------------------------------------

    async def apply(self, intent: Intent) -> str | None:
        action = intent.action
        if not isinstance(action, ChargeAction):
            raise self.unsupported(intent)
        self.begin_apply()
        await self._write_charge(action, limit=True)
        return self.last_context_id()

    async def _write_charge(self, action: ChargeAction, *, limit: bool) -> None:
        if self.lookup(self._current) is None:
            raise WriteRefusedError(f"{self._current} is unavailable")
        if action.phases is not None and self._phases is not None:
            await self._set_phases(action.phases)
        if action.effective_current_a <= 0:
            if self._enable is not None:
                if action.enabled is False:
                    # A baseline: the user's current setting comes back with their switch.
                    await self._set_current(action.current_a, limit=False)
                await self._set_enable(on=False)
                return
            low, _ = self.current_range_a()
            if low is not None and low > 0:
                raise WriteRefusedError(f"{self._current} cannot be set to 0 A (min {low})")
            await self._set_current(0.0, limit=False)
            return
        current = self.snap_current_a(action.current_a) if limit else action.current_a
        await self._set_current(current, limit=limit)
        await self._set_enable(on=True)

    async def _set_current(self, current_a: float, *, limit: bool) -> None:
        del limit  # the value is already snapped (apply) or the user's own (release)
        target = round(current_a, 6)
        current = self._current_a()
        if current is not None and math.isclose(current, target, abs_tol=SAME_VALUE):
            return
        await self.call("number", "set_value", self._current, {"value": target})

    async def _set_enable(self, *, on: bool) -> None:
        if self._enable is None:
            return
        if self.lookup(self._enable) is None:
            raise WriteRefusedError(f"{self._enable} is unavailable")
        if self._enabled() is on:
            return
        await self.call(domain_of(self._enable), "turn_on" if on else "turn_off", self._enable)

    async def _set_phases(self, phases: int) -> None:
        entity = self._phases
        assert entity is not None
        option = self._control.phase_option_1 if phases == 1 else self._control.phase_option_3
        assert option is not None
        view = self.lookup(entity)
        if view is None:
            raise WriteRefusedError(f"{entity} is unavailable")
        if view.state == option:
            return
        domain = domain_of(entity)
        if domain in SELECT_DOMAINS:
            await self.call(domain, "select_option", entity, {"option": option})
        elif option in ("on", "off"):
            await self.call(domain, "turn_on" if option == "on" else "turn_off", entity)
        else:
            raise WriteRefusedError(f"{entity}: a switch takes only on/off, not {option!r}")

    async def release(self, baseline: Action | None) -> None:
        self.begin_apply()
        if isinstance(baseline, ChargeAction):
            await self._write_charge(baseline, limit=False)


__all__ = ["ChargerDriver"]
