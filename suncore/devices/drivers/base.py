# SPDX-License-Identifier: Apache-2.0
"""Shared driver plumbing: the injected write/read capabilities and the error types.

Drivers are dumb (ADR-0011): ``apply`` writes, ``read_back`` reports, ``release`` restores
the takeover baseline. They never decide and never import ``suncore.ha``: the composition
root injects

* a :class:`ServiceWriter` - an async callable performing one Home Assistant service call
  and returning an object with the HA context id (``ServiceCaller.write``);
* a :class:`StateLookup` - a synchronous lookup in the **entity cache** (never
  ``get_states``) returning the current state of an entity or ``None``.

Both are protocols defined here so the dependency points from ``ha``/``app`` to ``devices``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any, Final, Protocol

from suncore.core.control import DeviceState
from suncore.core.decisions import Intent
from suncore.core.devices import DeviceConfig, RoleName
from suncore.core.signals import Quality

UNAVAILABLE_STATES: Final = frozenset({"unavailable", "unknown"})
"""Entity states that mean "no usable value" (HA ``STATE_UNAVAILABLE`` / ``STATE_UNKNOWN``)."""


class DriverError(Exception):
    """Base class of driver failures; the gate records any exception as a failed write."""


class ActionNotSupportedError(DriverError):
    """The action kind does not exist for this driver (a wiring or planner bug)."""


class WriteRefusedError(DriverError):
    """A precondition forbids the write (device off, unavailable, switching not allowed).

    Raised *before* any service call; nothing was sent to Home Assistant.
    """


class EntityStateView(Protocol):
    """What a driver reads from the entity cache (structurally ``ha.EntityState``)."""

    @property
    def entity_id(self) -> str: ...

    @property
    def state(self) -> str: ...

    @property
    def attributes(self) -> Mapping[str, Any]: ...


StateLookup = Callable[[str], EntityStateView | None]
"""Entity id -> cached state, ``None`` when the cache does not know the entity."""


class WriteResult(Protocol):
    """The part of a service-call result drivers use (structurally ``ha.ServiceResult``)."""

    @property
    def context_id(self) -> str | None: ...


class ServiceWriter(Protocol):
    """One Home Assistant service call (``call_service`` over the WebSocket).

    The composition root passes the rate-limited, safety-owned capability
    (``ServiceCaller.write``); drivers never see anything else.
    """

    async def __call__(
        self,
        domain: str,
        service: str,
        *,
        service_data: Mapping[str, Any] | None = None,
        target: Mapping[str, Any] | None = None,
    ) -> WriteResult: ...


class EntityDriver:
    """Common base: device identity, entity lookup, the single write helper."""

    def __init__(self, device: DeviceConfig, *, write: ServiceWriter, states: StateLookup) -> None:
        self._device = device
        self._write = write
        self._states = states
        self._context_ids: tuple[str, ...] = ()

    @property
    def asset_id(self) -> str:
        return self._device.id

    @property
    def device(self) -> DeviceConfig:
        return self._device

    @property
    def context_ids(self) -> tuple[str, ...]:
        """HA context ids of every service call of the latest ``apply`` (override evidence)."""
        return self._context_ids

    # --- helpers for subclasses ----------------------------------------------------------

    def entity_id(self, role: RoleName) -> str:
        return self._device.entities[role].entity_id

    def lookup(self, entity_id: str) -> EntityStateView | None:
        """The cached state or ``None`` if unknown to the cache or unavailable/unknown."""
        view = self._states(entity_id)
        if view is None or view.state in UNAVAILABLE_STATES:
            return None
        return view

    def quality_of(self, *entity_ids: str) -> Quality:
        """``ok`` when every entity has a usable state, else ``unavailable``."""
        return (
            Quality.OK
            if all(self.lookup(e) is not None for e in entity_ids)
            else Quality.UNAVAILABLE
        )

    async def call(
        self, domain: str, service: str, entity_id: str, data: Mapping[str, Any] | None = None
    ) -> str | None:
        """One service call on one entity; returns the HA context id (``None`` if absent)."""
        result = await self._write(
            domain,
            service,
            service_data=dict(data) if data else None,
            target={"entity_id": entity_id},
        )
        context_id = result.context_id
        if context_id is not None:
            self._context_ids = (*self._context_ids, context_id)
        return context_id

    def begin_apply(self) -> None:
        self._context_ids = ()

    def last_context_id(self) -> str | None:
        return self._context_ids[-1] if self._context_ids else None

    def device_state(self) -> DeviceState:  # pragma: no cover - overridden
        raise NotImplementedError

    def unsupported(self, intent: Intent) -> ActionNotSupportedError:
        return ActionNotSupportedError(
            f"{type(self).__name__} cannot apply a {intent.action.kind.value} action"
        )


def domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


def as_float(value: Any) -> float | None:
    """A finite float from an attribute or state text, else ``None``."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
