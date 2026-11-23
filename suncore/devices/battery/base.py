# SPDX-License-Identifier: Apache-2.0
"""Shared plumbing of the battery adapters (Phase 4 WP10, ADR-0013).

A battery adapter is a ``suncore.safety.battery.BatteryActuator`` (structurally - nothing here
imports ``safety``): the gate hands it an intent whose ``BatteryAction`` is already resolved
against the takeover baseline (``None`` = baseline) and quantised, the adapter writes it and
reports what the entities show. It never decides: the invariants (reserve, grid charging,
discharge lock, import limit, ranges) are the gate's. Like the device drivers it reads the
entity cache only (never ``get_states``) and writes through the injected, rate-limited
``ServiceWriter`` (``ServiceCaller.write``).

Signs: the ``BatteryAction`` fields are magnitudes in W and % (battery + = charging); the
*measured* battery power is the site's (``site.battery.power`` with its ``invert_sign``), the
adapters never read or convert it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from suncore.core.battery import (
    BATTERY_ASSET_ID,
    FIELD_CAPABILITY,
    BatteryAction,
    BatteryCapability,
    BatteryModel,
    BatteryRanges,
    BatterySettings,
)
from suncore.core.decisions import Action, Intent, Reason
from suncore.devices.drivers.base import (
    UNAVAILABLE_STATES,
    ActionNotSupportedError,
    EntityStateView,
    ServiceWriter,
    StateLookup,
    WriteRefusedError,
    as_float,
)

DEFAULT_SETTLE_S: Final = 10.0


class BatteryDriver:
    """Common base: identity, entity lookup, the single write helper, last applied state."""

    def __init__(
        self,
        settings: BatterySettings,
        *,
        write: ServiceWriter,
        states: StateLookup,
        settle_s: float | None = None,
    ) -> None:
        if settings.model is None:
            msg = "a battery adapter needs a battery model (capacity and limits)"
            raise ValueError(msg)
        self._settings = settings
        self._model: BatteryModel = settings.model
        self._write = write
        self._states = states
        self._settle_s = DEFAULT_SETTLE_S if settle_s is None else float(settle_s)
        self._context_ids: tuple[str, ...] = ()
        self._applied: BatteryAction | None = None

    # --- identity ---------------------------------------------------------------------------

    @property
    def asset_id(self) -> str:
        return BATTERY_ASSET_ID

    @property
    def settle_s(self) -> float:
        return self._settle_s

    @property
    def heartbeat_s(self) -> float | None:
        return None

    @property
    def capabilities(self) -> frozenset[BatteryCapability]:  # pragma: no cover - overridden
        raise NotImplementedError

    @property
    def applied(self) -> BatteryAction | None:
        """The state of the last successful ``apply`` (``None`` after a release)."""
        return self._applied

    # --- defaults of the optional protocol parts --------------------------------------------

    def ranges(self) -> BatteryRanges:  # pragma: no cover - overridden
        raise NotImplementedError

    def refusal(self) -> Reason | None:
        return None

    def prepared(self) -> bool:
        return True

    async def arm_fallback(self) -> None:
        return None

    async def keep_alive(self) -> None:
        return None

    # --- helpers --------------------------------------------------------------------------------

    def entity_id(self, role: str) -> str | None:
        ref = self._settings.entities.get(role)
        return None if ref is None else ref.entity_id

    def lookup(self, entity_id: str | None) -> EntityStateView | None:
        """The cached state; ``None`` if unknown to the cache or unavailable/unknown."""
        if entity_id is None:
            return None
        view = self._states(entity_id)
        if view is None or view.state in UNAVAILABLE_STATES:
            return None
        return view

    def value_of(self, role: str) -> float | None:
        """The numeric state of a role's entity (``None`` = unmapped, unavailable or text)."""
        view = self.lookup(self.entity_id(role))
        return None if view is None else as_float(view.state)

    def battery_action(self, intent: Intent) -> BatteryAction:
        action = intent.action
        if not isinstance(action, BatteryAction):
            msg = f"{type(self).__name__} cannot apply a {action.kind.value} action"
            raise ActionNotSupportedError(msg)
        missing = [
            name
            for name, value in action.model_dump(exclude={"kind"}).items()
            if value is not None and FIELD_CAPABILITY[name] not in self.capabilities
        ]
        if missing:
            msg = f"{type(self).__name__} cannot set {', '.join(missing)}"
            raise WriteRefusedError(msg)
        return action

    async def service(
        self,
        domain: str,
        service: str,
        *,
        data: Mapping[str, Any] | None = None,
        entity_id: str | None = None,
    ) -> str | None:
        """One service call; returns the HA context id (``None`` if absent)."""
        result = await self._write(
            domain,
            service,
            service_data=dict(data) if data else None,
            target={"entity_id": entity_id} if entity_id is not None else None,
        )
        context_id = result.context_id
        if context_id is not None:
            self._context_ids = (*self._context_ids, context_id)
        return context_id

    def begin(self) -> None:
        self._context_ids = ()

    def last_context_id(self) -> str | None:
        return self._context_ids[-1] if self._context_ids else None

    async def read_back(self) -> Action | None:  # pragma: no cover - overridden
        raise NotImplementedError

    async def takeover_baseline(self) -> Action | None:
        """What ``None`` fields mean and a release restores: the state found at takeover."""
        return await self.read_back()

    async def apply(self, intent: Intent) -> str | None:  # pragma: no cover - overridden
        raise NotImplementedError

    async def release(self, baseline: Action | None) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


__all__ = ["DEFAULT_SETTLE_S", "BatteryDriver"]
