# SPDX-License-Identifier: Apache-2.0
"""SG-Ready heat pump: two contact ``switch`` entities (ADR-0017 decision 5, PR 6-K).

``StepAction(step=i)`` selects ``control.steps[i]`` (0 = SG state 2 normal, 1 = state 3
intensified, 2 = state 4 forced, only with ``allow_forced``). The contacts are switched one at
a time along :func:`suncore.core.sgready.contact_path`, so a change of both contacts passes
only through a writable state - **never through the utility block (state 1)** or a pattern the
template does not name; a change without such a path is refused before any service call. Only
contacts whose cached state differs are written (``switch.turn_on`` / ``switch.turn_off``,
verified in research/ha-websocket-api.md "Services and fields").

Read-back maps the contact pair to its ``StepAction``; any other pair (state 1, an unmapped
pair, state 4 while ``allow_forced`` is off) is reported as ``ModeAction(mode="sg:<a><b>")``, a
different kind, so the gate sees "changed outside SunCore" and never adopts it as a step.

Release restores the takeover baseline when it is a writable step. Any other baseline (a pair
SunCore may not write: the utility block, state 4 without ``allow_forced``, an unmapped pair)
was set by someone else - a user, an automation, the grid operator's lock - and **release
writes nothing**: SunCore never lifts a foreign lock, and never writes state 1 either
(review 6-K, A1).

``UNVERIFIED`` (docs/STATUS.md): which contact pair selects which state is vendor-specific and
comes from the template; the generic template's mapping is the common one, unconfirmed. No real
heat pump has been driven: simulator only.
"""

from __future__ import annotations

from suncore.core.control import DeviceState
from suncore.core.decisions import Action, Intent, ModeAction, StepAction
from suncore.core.devices import DeviceConfig, SgContacts, SgReadyControl
from suncore.core.sgready import SG_NORMAL, contact_path, sg_contacts_label, sg_step_of
from suncore.core.signals import Quality
from suncore.devices.drivers.base import (
    EntityDriver,
    ServiceWriter,
    StateLookup,
    WriteRefusedError,
    domain_of,
)

SWITCH_DOMAINS = frozenset({"switch", "input_boolean"})


class SgReadyDriver(EntityDriver):
    """Drive the two SG-Ready contacts of one heat pump."""

    def __init__(self, device: DeviceConfig, *, write: ServiceWriter, states: StateLookup) -> None:
        control = device.control
        if not isinstance(control, SgReadyControl):
            msg = f"SgReadyDriver needs an sg_ready control, got {control.kind}"
            raise TypeError(msg)
        super().__init__(device, write=write, states=states)
        self._control = control
        self._a = self.entity_id(control.contact_a)
        self._b = self.entity_id(control.contact_b)
        for entity in (self._a, self._b):
            if domain_of(entity) not in SWITCH_DOMAINS:
                msg = f"SG-Ready contact {entity} must be a switch"
                raise ValueError(msg)

    # --- reading ------------------------------------------------------------------------

    def contacts(self) -> SgContacts | None:
        """The contact pair as the cache shows it; ``None`` if a contact is unusable."""
        values: list[bool] = []
        for entity in (self._a, self._b):
            view = self.lookup(entity)
            if view is None or view.state not in ("on", "off"):
                return None
            values.append(view.state == "on")
        return SgContacts(a=values[0], b=values[1])

    def _read_sync(self) -> Action | None:
        pair = self.contacts()
        if pair is None:
            return None
        step = sg_step_of(self._control, pair)
        if step is None:
            return ModeAction(mode=sg_contacts_label(pair))
        return StepAction(step=step)

    async def read_back(self) -> Action | None:
        return self._read_sync()

    def device_state(self) -> DeviceState:
        action = self._read_sync()
        if action is None:
            return DeviceState(quality=Quality.UNAVAILABLE)
        if isinstance(action, StepAction):
            return DeviceState(step=action.step, on=action.step > SG_NORMAL)
        assert isinstance(action, ModeAction)
        return DeviceState(on=None, mode=action.mode)

    # --- writing ------------------------------------------------------------------------

    async def apply(self, intent: Intent) -> str | None:
        action = intent.action
        if not isinstance(action, StepAction):
            raise self.unsupported(intent)
        if not 0 <= action.step < len(self._control.steps):
            raise WriteRefusedError(f"SG-Ready step {action.step} is not writable")
        self.begin_apply()
        return await self._select(self._control.steps[action.step])

    async def _select(self, target: SgContacts) -> str | None:
        current = self.contacts()
        if current is None:
            raise WriteRefusedError("an SG-Ready contact is unavailable")
        path = contact_path(self._control, current, target)
        if path is None:
            raise WriteRefusedError(
                f"no safe contact order from {sg_contacts_label(current)} "
                f"to {sg_contacts_label(target)}"
            )
        for pair in path:
            if pair.a != current.a:
                await self._set(self._a, on=pair.a)
            if pair.b != current.b:
                await self._set(self._b, on=pair.b)
            current = pair
        return self.last_context_id()

    async def _set(self, entity: str, *, on: bool) -> None:
        await self.call(domain_of(entity), "turn_on" if on else "turn_off", entity)

    async def release(self, baseline: Action | None) -> None:
        self.begin_apply()
        if not (isinstance(baseline, StepAction) and 0 <= baseline.step < len(self._control.steps)):
            return  # none, or a pair SunCore may not write: leave it as it is (A1)
        await self._select(self._control.steps[baseline.step])


__all__ = ["SgReadyDriver"]
