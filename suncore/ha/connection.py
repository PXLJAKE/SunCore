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
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Final, Protocol, runtime_checkable

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


class ActionNotAllowedError(HAError):
    """``call_action_response`` was asked for an action that is not on the read-only allowlist."""


class ActionRateLimitedError(HAError):
    """A read-only action was asked for again before its minimum interval elapsed.

    Nothing was sent to Home Assistant. ``retry_after_s`` is how long until the call would be
    admitted. The user-visible text is the catalogue entry :data:`ERR_ACTION_RATE_LIMITED`.
    """

    code = "rate_limited"

    def __init__(self, action: str, retry_after_s: float) -> None:
        super().__init__(f"{action} is rate limited, retry in {retry_after_s:.1f} s")
        self.action = action
        self.retry_after_s = retry_after_s


ERR_ACTION_RATE_LIMITED: Final = "ha.error.action_rate_limited"
"""Catalogue key of the refusal of a too frequent read-only action."""


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
class StateContext:
    """The HA context of the change that produced a state (``State.context``).

    A change SunCore caused through ``call_service`` carries the id of that call (positive
    proof, research/ha-websocket-api.md "Context propagation"); an integration push has a
    fresh id and no user; a change started in the HA UI has the user's id.
    """

    id: str
    user_id: str | None = None
    parent_id: str | None = None


@dataclass(frozen=True, slots=True)
class EntityState:
    """A Home Assistant entity state.

    Timestamps are epoch seconds (HA's own compressed format uses floats). A value is
    ``None`` when the source does not carry it: the compressed stream has no
    ``last_reported``; ``get_states`` has all three. ``context`` is the HA context of the change
    that produced the state: parsed from the ``c`` key of ``subscribe_entities`` (a string id or
    a dict, merged across diffs) and from the ``context`` dict of ``get_states``; ``None`` where
    the source carries none.
    """

    entity_id: str
    state: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    last_changed: float | None = None
    last_updated: float | None = None
    last_reported: float | None = None
    context: StateContext | None = None


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

REGISTRY_EVENT_TYPES: Final[tuple[str, ...]] = (
    "entity_registry_updated",
    "device_registry_updated",
    "area_registry_updated",
)
"""Registry update events (verified 2026-10-13, ``docs/research/ha-websocket-api.md``)."""

RegistryListener = Callable[[str], None]
"""Called with the event type of a registry update (the event data is not needed: a refresh
re-reads the list commands)."""


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

    # --- registries and Energy dashboard (read only, Phase 2 WP5) ---------------

    async def entity_registry_list_for_display(self) -> Mapping[str, Any]:
        """``config/entity_registry/list_for_display``: ``{entity_categories, entities}``.

        Compact, without disabled entities (verified, ``docs/research/ha-websocket-api.md``).
        """
        ...

    async def device_registry_list(self) -> Sequence[Mapping[str, Any]]:
        """``config/device_registry/list``."""
        ...

    async def area_registry_list(self) -> Sequence[Mapping[str, Any]]:
        """``config/area_registry/list``."""
        ...

    async def energy_get_prefs(self) -> Mapping[str, Any] | None:
        """``energy/get_prefs``; ``None`` where HA answers ``not_found`` ("No prefs")."""
        ...

    async def subscribe_registry_updates(self, listener: RegistryListener) -> Unsubscribe:
        """Call ``listener(event_type)`` on every registry update event.

        Subscribes to the three events of :data:`REGISTRY_EVENT_TYPES`; the subscriptions are
        renewed after a reconnect. Returns the remover.
        """
        ...

    async def call_action_response(
        self,
        domain: str,
        service: str,
        data: Mapping[str, Any] | None = None,
        *,
        target: Mapping[str, Any] | None = None,
        min_interval_s: float | None = None,
        max_wait_s: float = 0.0,
    ) -> dict[str, Any]:
        """Call a read-only action with ``return_response`` and return its response.

        Raises :class:`ActionNotAllowedError` unless ``domain.service`` is in
        :data:`suncore.core.actions.READ_ONLY_ACTIONS`. This is *not* a write path.

        Each ``(domain, service)`` is rate limited (:class:`ActionRateLimiter`): at most one
        call per ``min_interval_s`` (default :data:`DEFAULT_ACTION_INTERVAL_S`; a caller passes
        the interval of its configured polling). A call that is too early waits at most
        ``max_wait_s`` (default: not at all) and otherwise raises
        :class:`ActionRateLimitedError`; it never blocks indefinitely.
        """
        ...

    async def statistics_during_period(
        self,
        statistic_ids: Sequence[str],
        start: datetime,
        end: datetime | None = None,
        *,
        period: str = "hour",
        types: Sequence[str] | None = None,
        units: Mapping[str, str] | None = None,
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        """``recorder/statistics_during_period`` (read only, not admin gated).

        ``start``/``end`` are sent as ISO 8601 with their UTC offset. The result maps each
        statistic id *that has data* to its rows (``start``/``end`` in epoch milliseconds,
        the requested ``types``); ids without data are absent. The response carries no unit:
        pass ``units`` (e.g. ``{"power": "W", "energy": "Wh"}``) so the numbers do not change
        when the user switches a sensor's display unit. Verified shapes:
        ``docs/research/ha-websocket-api.md`` "Long-term statistics".
        ``UNVERIFIED``: that the Supervisor proxy forwards ``recorder/*``.
        """
        ...


@runtime_checkable
class ServiceCallerProvider(Protocol):
    """Implemented by connections that can hand out a :class:`ServiceCaller`."""

    def service_caller(self) -> ServiceCaller: ...


# --- service capability -------------------------------------------------------


DEFAULT_WRITE_INTERVAL_S: Final = 0.25
"""Connection flood floor: minimum spacing of two service calls (lowered from 1 s in
Phase 3; the per-device write rate limit of ``safety`` is the real protection)."""


class RateLimiter:
    """Minimum spacing between writes. Clock and sleep are injected."""

    def __init__(
        self,
        min_interval: float = DEFAULT_WRITE_INTERVAL_S,
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


DEFAULT_ACTION_INTERVAL_S: Final = 30.0
"""Default minimum spacing of two calls of the same read-only action (follow-up A3)."""


class ActionRateLimiter:
    """Minimum spacing per key (``domain.service``) with try-acquire and a wait deadline.

    Slots are handed out in order: a caller that waits reserves the slot after the previous
    one, so two concurrent callers do not both pass. Clock and sleep are injected. A key that
    is too busy is refused instead of queued (:meth:`acquire` returns ``False``), so a slow
    integration can never stack up calls.
    """

    def __init__(
        self,
        default_interval_s: float = DEFAULT_ACTION_INTERVAL_S,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if default_interval_s < 0:
            msg = "default_interval_s must be >= 0"
            raise ValueError(msg)
        self._default = default_interval_s
        self._clock = clock
        self._sleep = sleep
        self._next_ok: dict[str, float] = {}

    def retry_after(self, key: str) -> float:
        """Seconds until ``key`` would be admitted (0 = now)."""
        return max(0.0, self._next_ok.get(key, 0.0) - self._clock())

    def try_acquire(self, key: str, interval_s: float | None = None) -> bool:
        """Take the slot if it is free now; otherwise change nothing and return ``False``."""
        interval = self._interval(interval_s)
        now = self._clock()
        if self._next_ok.get(key, 0.0) > now:
            return False
        self._next_ok[key] = now + interval
        return True

    async def acquire(
        self, key: str, interval_s: float | None = None, *, max_wait_s: float = 0.0
    ) -> bool:
        """Take the next slot of ``key`` if it comes within ``max_wait_s`` (waiting for it).

        Returns ``False`` without waiting and without reserving when the slot is further away.
        """
        interval = self._interval(interval_s)
        now = self._clock()
        slot = max(now, self._next_ok.get(key, 0.0))
        wait = slot - now
        if wait > max(0.0, max_wait_s):
            return False
        self._next_ok[key] = slot + interval  # reserved before sleeping: no await in between
        if wait > 0:
            await self._sleep(wait)
        return True

    def _interval(self, interval_s: float | None) -> float:
        interval = self._default if interval_s is None else interval_s
        if interval < 0:
            msg = "interval_s must be >= 0"
            raise ValueError(msg)
        return interval


@dataclass(frozen=True, slots=True)
class ServiceResult:
    """Outcome of a service call: HA's response, the state read back afterwards and the
    context id of the call (``result.context.id`` of the WebSocket reply)."""

    response: Any
    readback: EntityState | None
    context_id: str | None = None


Commander = Callable[[Mapping[str, Any]], Awaitable[Any]]
StateReader = Callable[[str], Awaitable[EntityState | None]]


def _context_id(result: Any) -> str | None:
    """``result.context.id`` of a ``call_service`` reply (research/ha-websocket-api.md)."""
    context = result.get("context") if isinstance(result, Mapping) else None
    value = context.get("id") if isinstance(context, Mapping) else None
    return value if isinstance(value, str) and value else None


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
        context_id = _context_id(result)
        readback = (
            await self._read_state(readback_entity_id) if readback_entity_id is not None else None
        )
        return ServiceResult(response=response, readback=readback, context_id=context_id)

    async def write(
        self,
        domain: str,
        service: str,
        *,
        service_data: Mapping[str, Any] | None = None,
        target: Mapping[str, Any] | None = None,
    ) -> ServiceResult:
        """The narrow form drivers get (``suncore.devices.drivers.base.ServiceWriter``).

        One rate-limited service call without response data or read-back: drivers read
        back from the entity cache, never from ``get_states``.
        """
        return await self.call_service(domain, service, service_data=service_data, target=target)
