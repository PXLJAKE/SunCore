# SPDX-License-Identifier: Apache-2.0
"""The ``HAConnection`` boundary: protocol, value types, errors and the service capability.

Nothing outside ``suncore.ha`` knows which transport (Supervisor or direct) or which
client implementation sits behind :class:`HAConnection`. The simulator provides a fake
that satisfies the same protocol (ADR-0010).

Verified on 2026-10-12 against ``home-assistant/core`` (dev branch):

* ``homeassistant/components/websocket_api/commands.py`` - ``get_states`` (result is a
  list of ``State.as_dict`` objects), ``get_config``, ``call_service`` (fields ``domain``,
  ``service``, ``target``, ``service_data``, ``return_response``; the result carries
  ``context`` and, if requested, ``response``), ``unsubscribe_events`` (field
  ``subscription``).
* ``homeassistant/core.py`` - ``State._as_dict`` keys ``entity_id``, ``state``,
  ``attributes``, ``last_changed``, ``last_reported``, ``last_updated`` (ISO strings).

``UNVERIFIED``: the exact set of error ``code`` values a ``call_service`` can return
beyond the constants in ``websocket_api/const.py``; callers must treat ``code`` as opaque.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

# --- errors -----------------------------------------------------------------


class HAError(Exception):
    """Base class of all connector errors."""


class AuthError(HAError):
    """Home Assistant rejected the token (``auth_invalid``). Never retried."""


class ProtocolError(HAError):
    """The peer sent something that is not the documented protocol."""


class ConnectionLostError(HAError):
    """The connection dropped while a command was pending or before it was ready."""


class NotConnectedError(HAError):
    """A command was issued while no connection is established."""


class HeartbeatTimeoutError(ConnectionLostError):
    """No ``pong`` arrived within the heartbeat timeout."""


class CommandError(HAError):
    """Home Assistant answered a command with ``success: false``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class CommandTimeoutError(HAError, TimeoutError):
    """No result arrived for a command in time."""


# --- value types --------------------------------------------------------------


class ConnectionState(StrEnum):
    """Lifecycle of a connection."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    AUTH_FAILED = "auth_failed"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class EntityState:
    """A Home Assistant entity state.

    Timestamps are epoch seconds (HA's own compressed format uses floats). A value is
    ``None`` when the source does not carry it: the compressed stream has no
    ``last_reported``; ``get_states`` has all three.
    """

    entity_id: str
    state: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    last_changed: float | None = None
    last_updated: float | None = None
    last_reported: float | None = None


@dataclass(frozen=True, slots=True)
class EntityUpdate:
    """One batch of entity changes delivered to listeners.

    ``full`` marks a complete snapshot of the subscription (first connect, resubscribe
    after a reconnect, periodic or inconsistency-triggered resync): entities that are
    not in ``changed`` no longer exist and must be dropped. Otherwise ``changed`` holds
    added or changed states (complete, diffs already applied) and ``removed`` the
    entity ids that disappeared.
    """

    changed: Mapping[str, EntityState] = field(default_factory=dict)
    removed: tuple[str, ...] = ()
    full: bool = False


StateListener = Callable[[ConnectionState], None]
EntityListener = Callable[[EntityUpdate], None]
Unsubscribe = Callable[[], None]


@runtime_checkable
class HAConnection(Protocol):
    """What the rest of SunCore may use from Home Assistant (read side only)."""

    @property
    def state(self) -> ConnectionState: ...

    def add_state_listener(self, listener: StateListener) -> Unsubscribe:
        """Call ``listener`` on every connection state change; returns the remover."""
        ...

    def add_entity_listener(self, listener: EntityListener) -> Unsubscribe:
        """Call ``listener`` for every :class:`EntityUpdate`; returns the remover."""
        ...

    async def connect(self) -> None:
        """Start the connection and return once it is usable.

        Network errors are retried in the background with back-off; an invalid token
        raises :class:`AuthError` and is never retried. Wrap in ``asyncio.wait_for`` if
        the caller must not wait for an unreachable Home Assistant.
        """
        ...

    async def close(self) -> None: ...

    async def subscribe_entities(self, entity_ids: Collection[str]) -> None:
        """Replace the set of subscribed entities; kept across reconnects.

        An empty collection unsubscribes (it does *not* mean "all entities").
        """
        ...

    async def get_states(self) -> list[EntityState]: ...

    async def get_config(self) -> Mapping[str, Any]: ...


@runtime_checkable
class ServiceCallerProvider(Protocol):
    """Implemented by connections that can hand out a :class:`ServiceCaller`."""

    def service_caller(self) -> ServiceCaller: ...


# --- service capability -------------------------------------------------------


class RateLimiter:
    """Minimum spacing between writes. Clock and sleep are injected."""

    def __init__(
        self,
        min_interval: float = 1.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if min_interval < 0:
            msg = "min_interval must be >= 0"
            raise ValueError(msg)
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            if self._last is not None:
                wait = self._last + self._min_interval - self._clock()
                if wait > 0:
                    await self._sleep(wait)
            self._last = self._clock()


@dataclass(frozen=True, slots=True)
class ServiceResult:
    """Outcome of a service call: HA's response and the state read back afterwards."""

    response: Any
    readback: EntityState | None


Commander = Callable[[Mapping[str, Any]], Awaitable[Any]]
StateReader = Callable[[str], Awaitable[EntityState | None]]


class ServiceCaller:
    """Capability to write to Home Assistant; handed to ``safety`` only.

    Every call is rate limited and, when ``readback_entity_id`` is given, followed by a
    read of that entity's state so the caller can verify the effect. Unused in Phase 1.
    The class is constructed by the connection (``service_caller()``); the safety layer
    is its only intended holder (``tests/safety/test_boundary.py`` enforces that
    ``call_service`` is not referenced elsewhere).
    """

    def __init__(
        self,
        commander: Commander,
        read_state: StateReader,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._commander = commander
        self._read_state = read_state
        self._limiter = limiter if limiter is not None else RateLimiter()

    async def call_service(
        self,
        domain: str,
        service: str,
        *,
        service_data: Mapping[str, Any] | None = None,
        target: Mapping[str, Any] | None = None,
        return_response: bool = False,
        readback_entity_id: str | None = None,
    ) -> ServiceResult:
        await self._limiter.acquire()
        payload: dict[str, Any] = {"type": "call_service", "domain": domain, "service": service}
        if service_data is not None:
            payload["service_data"] = dict(service_data)
        if target is not None:
            payload["target"] = dict(target)
        if return_response:
            payload["return_response"] = True
        result = await self._commander(payload)
        response = result.get("response") if isinstance(result, Mapping) else None
        readback = (
            await self._read_state(readback_entity_id) if readback_entity_id is not None else None
        )
        return ServiceResult(response=response, readback=readback)
