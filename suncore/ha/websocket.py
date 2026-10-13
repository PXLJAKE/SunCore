# SPDX-License-Identifier: Apache-2.0
"""aiohttp client for the Home Assistant WebSocket API.

Protocol facts relied upon, verified on 2026-10-12 against ``home-assistant/core``
(dev branch) sources:

* ``websocket_api/auth.py`` - server sends ``{"type":"auth_required"}``; client answers
  ``{"type":"auth","access_token":...}``; reply is ``auth_ok`` (with ``ha_version``) or
  ``auth_invalid`` (with ``message``), after which the server disconnects.
* ``websocket_api/commands.py`` - ``ping`` is answered with ``{"id":n,"type":"pong"}``;
  ``supported_features`` takes ``{"features": {name: int}}`` and is answered with a
  result; ``get_states`` / ``get_config`` / ``unsubscribe_events`` as in
  ``connection.py``. ``subscribe_entities`` (optional ``entity_ids``) first sends the
  result, then one ``event`` message ``{"a": {entity_id: compressed_state}}`` with the
  current states, then change events. An empty/absent ``entity_ids`` means all entities,
  hence this client never subscribes with an empty list.
* ``websocket_api/messages.py`` (``_state_diff_event``) - change events carry
  ``a`` (added: full compressed state), ``c`` (changed: ``{"+": additions, "-":
  {"a": [removed attribute keys]}}``) and ``r`` (removed: list of ids). In a diff ``lc``
  is present only if ``last_changed`` changed, ``lu`` only if ``last_updated`` changed
  while ``last_changed`` did not; context (``c``) is ignored by SunCore. Additions hold
  only changed attributes (``a``) and a changed state (``s``).
* ``const.py`` (``homeassistant``) - ``COMPRESSED_STATE_*`` = ``s``, ``a``, ``c``,
  ``lc``, ``lu``; ``websocket_api/const.py`` - ``FEATURE_COALESCE_MESSAGES =
  "coalesce_messages"``.
* ``websocket_api/http.py`` - with ``coalesce_messages`` enabled the server may send a
  JSON *array* of messages in one text frame.
* Error replies: ``{"id", "type":"result", "success":false, "error":{"code","message"}}``.

``UNVERIFIED``: that the Supervisor's Core proxy forwards ``supported_features``
unchanged (it is a transparent WebSocket proxy per ``docs/research/ha-apps.md``); a
failing ``supported_features`` is therefore tolerated and only logged.

Time is injected (``clock`` monotonic seconds, ``sleep``, ``rng``) so back-off,
heartbeat and command timeouts are testable with a fake clock.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, TypeVar, cast

import aiohttp
import structlog

from suncore.ha.connection import (
    AuthError,
    CommandError,
    CommandTimeoutError,
    ConnectionLostError,
    ConnectionState,
    EntityListener,
    EntityState,
    EntityUpdate,
    HeartbeatTimeoutError,
    NotConnectedError,
    ProtocolError,
    RateLimiter,
    ServiceCaller,
    StateListener,
    Unsubscribe,
)
from suncore.ha.redact import DEFAULT_REGISTRY, SecretRegistry
from suncore.ha.transport import Transport

log = structlog.get_logger(__name__)

T = TypeVar("T")

MAX_MESSAGE_BYTES: Final = 32 * 1024 * 1024
"""A full ``get_states`` of a large installation exceeds aiohttp's 4 MiB default."""

# Compressed state keys (homeassistant/const.py COMPRESSED_STATE_*).
_S, _A, _LC, _LU = "s", "a", "lc", "lu"
# Diff keys (websocket_api/messages.py STATE_DIFF_ADDITIONS/REMOVALS).
_ADD, _REMOVE = "+", "-"


class Backoff:
    """Exponential back-off 1 s -> 60 s with symmetric multiplicative jitter."""

    def __init__(
        self,
        *,
        initial: float = 1.0,
        maximum: float = 60.0,
        factor: float = 2.0,
        jitter: float = 0.25,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._initial = initial
        self._maximum = maximum
        self._factor = factor
        self._jitter = jitter
        self._rng = rng
        self._attempt = 0

    def next_delay(self) -> float:
        base = min(self._maximum, self._initial * self._factor ** min(self._attempt, 32))
        self._attempt += 1
        delay = base * (1.0 + self._jitter * (2.0 * self._rng() - 1.0))
        return min(self._maximum, max(0.0, delay))

    def reset(self) -> None:
        self._attempt = 0


@dataclass(slots=True)
class _Pending:
    future: asyncio.Future[Any]
    on_result: Callable[[Any], None] | None = None


def _iso_to_epoch(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _float_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def state_from_compressed(entity_id: str, data: Mapping[str, Any]) -> EntityState:
    """Build a state from a full compressed state (``a`` entries / initial snapshot)."""
    last_changed = _float_or_none(data.get(_LC))
    last_updated = _float_or_none(data.get(_LU))
    resolved_updated = last_updated if last_updated is not None else last_changed
    attributes = data.get(_A)
    return EntityState(
        entity_id=entity_id,
        state=str(data.get(_S, "")),
        attributes=dict(attributes) if isinstance(attributes, Mapping) else {},
        last_changed=last_changed,
        last_updated=resolved_updated,
        # HA sets last_reported = last_updated on a new state (core.py State.__init__);
        # the stream carries no last_reported, so mirror that to compare like with like.
        last_reported=resolved_updated,
    )


def apply_diff(previous: EntityState, diff: Mapping[str, Any]) -> EntityState:
    """Apply a ``c`` diff (``{"+": ..., "-": ...}``) to the previous state."""
    additions = diff.get(_ADD) or {}
    removals = diff.get(_REMOVE) or {}
    attributes = dict(previous.attributes)
    attributes.update(additions.get(_A) or {})
    for key in removals.get(_A) or []:
        attributes.pop(key, None)
    last_changed = previous.last_changed
    last_updated = previous.last_updated
    if _LC in additions:
        # last_changed moved: HA omits lu because last_updated == last_changed then.
        last_changed = _float_or_none(additions[_LC])
        last_updated = last_changed
    if _LU in additions:
        last_updated = _float_or_none(additions[_LU])
    return EntityState(
        entity_id=previous.entity_id,
        state=str(additions[_S]) if _S in additions else previous.state,
        attributes=attributes,
        last_changed=last_changed,
        last_updated=last_updated,
        last_reported=last_updated,  # see state_from_compressed
    )


def state_from_full(data: Mapping[str, Any]) -> EntityState | None:
    """Build a state from a ``get_states`` item; ``None`` if it has no entity id."""
    entity_id = data.get("entity_id")
    if not isinstance(entity_id, str):
        return None
    return EntityState(
        entity_id=entity_id,
        state=str(data.get("state", "")),
        attributes=dict(data.get("attributes") or {}),
        last_changed=_iso_to_epoch(data.get("last_changed")),
        last_updated=_iso_to_epoch(data.get("last_updated")),
        last_reported=_iso_to_epoch(data.get("last_reported")),
    )


class WebSocketConnection:
    """``HAConnection`` implementation on top of an aiohttp WebSocket client."""

    def __init__(
        self,
        transport: Transport,
        *,
        session: aiohttp.ClientSession | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
        backoff: Backoff | None = None,
        heartbeat_interval: float = 30.0,
        heartbeat_timeout: float = 10.0,
        command_timeout: float = 30.0,
        connect_timeout: float = 10.0,
        resync_interval: float = 60.0,
        stable_after: float = 30.0,
        write_interval: float = 1.0,
        secrets: SecretRegistry = DEFAULT_REGISTRY,
    ) -> None:
        self._transport = transport
        self._session = session
        self._owns_session = session is None
        self._clock = clock
        self._sleep = sleep
        self._backoff = backoff if backoff is not None else Backoff(rng=rng)
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_timeout = heartbeat_timeout
        self._command_timeout = command_timeout
        self._connect_timeout = connect_timeout
        self._resync_interval = resync_interval
        self._stable_after = stable_after
        self._write_limiter = RateLimiter(write_interval, clock=clock, sleep=sleep)

        secrets.register(transport.token)

        self._state = ConnectionState.DISCONNECTED
        self._state_listeners: list[StateListener] = []
        self._entity_listeners: list[EntityListener] = []

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._next_id = 1
        self._pending: dict[int, _Pending] = {}
        self._task: asyncio.Task[None] | None = None
        self._first: asyncio.Future[None] | None = None
        self._closing = False
        self._connected_since: float | None = None
        self.ha_version: str | None = None

        self._wanted: tuple[str, ...] = ()
        self._sub_id: int | None = None
        self._states: dict[str, EntityState] = {}
        self._awaiting_init: asyncio.Event | None = None
        self._sub_lock = asyncio.Lock()
        self._resync_task: asyncio.Task[None] | None = None

    # --- public API ---------------------------------------------------------

    @property
    def state(self) -> ConnectionState:
        return self._state

    def add_state_listener(self, listener: StateListener) -> Unsubscribe:
        return _add_listener(self._state_listeners, listener)

    def add_entity_listener(self, listener: EntityListener) -> Unsubscribe:
        return _add_listener(self._entity_listeners, listener)

    async def connect(self) -> None:
        if self._task is None or self._task.done():
            self._closing = False
            self._first = asyncio.get_running_loop().create_future()
            self._task = asyncio.create_task(self._run(), name="suncore-ha-connection")
        assert self._first is not None
        try:
            await asyncio.shield(self._first)
        except asyncio.CancelledError:
            await self.close()
            raise

    async def close(self) -> None:
        self._closing = True
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if self._first is not None and not self._first.done():
            self._first.cancel()
        self._fail_pending(ConnectionLostError("connection closed"))
        if self._session is not None and self._owns_session:
            await self._session.close()
            self._session = None
        self._set_state(ConnectionState.CLOSED)

    async def subscribe_entities(self, entity_ids: Collection[str]) -> None:
        async with self._sub_lock:
            self._wanted = tuple(sorted(set(entity_ids)))
            if self._state is ConnectionState.CONNECTED:
                await self._subscribe_on_wire()
                await self._resync()

    async def get_states(self) -> list[EntityState]:
        raw = await self._command({"type": "get_states"})
        states = (state_from_full(item) for item in raw if isinstance(item, Mapping))
        return [s for s in states if s is not None]

    async def get_config(self) -> Mapping[str, Any]:
        result = await self._command({"type": "get_config"})
        if not isinstance(result, Mapping):
            msg = "get_config returned a non-object"
            raise ProtocolError(msg)
        return cast("Mapping[str, Any]", result)

    def service_caller(self) -> ServiceCaller:
        """Capability for ``safety`` to write. Not used in Phase 1."""
        return ServiceCaller(self._command, self._read_state, self._write_limiter)

    # --- supervisor loop ------------------------------------------------------

    async def _run(self) -> None:
        while not self._closing:
            self._set_state(ConnectionState.CONNECTING)
            try:
                await self._session_once()
            except AuthError as err:
                log.error("ha.auth_failed", transport=self._transport.name)
                self._set_state(ConnectionState.AUTH_FAILED)
                if self._first is not None and not self._first.done():
                    self._first.set_exception(err)
                return
            except asyncio.CancelledError:
                raise
            except (HeartbeatTimeoutError, ConnectionLostError, ProtocolError, CommandError) as err:
                log.warning("ha.connection_lost", reason=str(err))
            except (aiohttp.ClientError, OSError, TimeoutError) as err:
                log.warning("ha.connect_failed", reason=type(err).__name__, detail=str(err))
            except Exception as err:
                # Anything unexpected must not kill the supervisor task silently: log (no
                # token, no message text) and fall through to back-off and reconnect.
                log.exception("ha.connection_crashed", reason=type(err).__name__)
            finally:
                self._ws = None
                self._sub_id = None
                self._fail_pending(ConnectionLostError("connection lost"))
            if self._closing:
                return
            if (
                self._connected_since is not None
                and self._clock() - self._connected_since >= self._stable_after
            ):
                self._backoff.reset()
            self._connected_since = None
            delay = self._backoff.next_delay()
            self._set_state(ConnectionState.RECONNECTING)
            log.info("ha.reconnect_scheduled", delay_s=round(delay, 3))
            await self._sleep(delay)

    async def _session_once(self) -> None:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        ws = await self._within(
            self._session.ws_connect(
                self._transport.url, heartbeat=None, max_msg_size=MAX_MESSAGE_BYTES
            ),
            self._connect_timeout,
        )
        self._ws = ws
        reader: asyncio.Task[None] | None = None
        background: set[asyncio.Task[None]] = set()
        try:
            await self._authenticate(ws)
            self._next_id = 1
            reader = asyncio.create_task(self._read_loop(ws), name="suncore-ha-reader")
            background.add(reader)
            await self._setup()
            self._connected_since = self._clock()
            background.add(asyncio.create_task(self._heartbeat(), name="suncore-ha-heartbeat"))
            if self._resync_interval > 0:
                background.add(
                    asyncio.create_task(self._periodic_resync(), name="suncore-ha-resync")
                )
            self._set_state(ConnectionState.CONNECTED)
            log.info("ha.connected", transport=self._transport.name)
            if self._first is not None and not self._first.done():
                self._first.set_result(None)
            done, _ = await asyncio.wait(background, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in background:
                task.cancel()
            for task in background:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            if self._resync_task is not None:
                self._resync_task.cancel()
                self._resync_task = None
            await ws.close()

    async def _authenticate(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        first = await self._within(self._receive_one(ws), self._connect_timeout)
        if first.get("type") != "auth_required":
            msg = f"expected auth_required, got {first.get('type')!r}"
            raise ProtocolError(msg)
        await ws.send_json({"type": "auth", "access_token": self._transport.token})
        reply = await self._within(self._receive_one(ws), self._connect_timeout)
        kind = reply.get("type")
        if kind == "auth_ok":
            version = reply.get("ha_version")
            self.ha_version = version if isinstance(version, str) else None
            return
        if kind == "auth_invalid":
            message = reply.get("message")
            raise AuthError(str(message) if message else "invalid access token")
        msg = f"unexpected reply to auth: {kind!r}"
        raise ProtocolError(msg)

    async def _receive_one(self, ws: aiohttp.ClientWebSocketResponse) -> dict[str, Any]:
        """Receive one JSON object (auth phase; coalescing is not enabled yet)."""
        msg = await ws.receive()
        if msg.type is not aiohttp.WSMsgType.TEXT:
            raise ConnectionLostError(f"connection ended during auth ({msg.type.name})")
        data = _loads(msg.data)
        if not isinstance(data, dict):
            msg_ = "auth phase message is not an object"
            raise ProtocolError(msg_)
        return data

    async def _setup(self) -> None:
        try:
            await self._command(
                {"type": "supported_features", "features": {"coalesce_messages": 1}}
            )
        except CommandError as err:
            log.warning("ha.coalesce_unsupported", code=err.code)
        async with self._sub_lock:
            if self._wanted:
                await self._subscribe_on_wire()
                await self._resync()
            else:
                self._states = {}

    # --- reading -----------------------------------------------------------

    async def _read_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while True:
            msg = await ws.receive()
            if msg.type is aiohttp.WSMsgType.TEXT:
                data = _loads(msg.data)
                for item in data if isinstance(data, list) else [data]:
                    if isinstance(item, dict):
                        self._dispatch(item)
            elif msg.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.CLOSED,
            ):
                raise ConnectionLostError("closed by peer")
            elif msg.type is aiohttp.WSMsgType.ERROR:
                raise ConnectionLostError(f"websocket error: {type(ws.exception()).__name__}")

    def _dispatch(self, msg: Mapping[str, Any]) -> None:
        kind = msg.get("type")
        mid = msg.get("id")
        if not isinstance(mid, int):
            return
        if kind in ("result", "pong"):
            pending = self._pending.get(mid)
            if pending is None or pending.future.done():
                return
            if kind == "result" and msg.get("success") is False:
                error = msg.get("error")
                err = error if isinstance(error, Mapping) else {}
                pending.future.set_exception(
                    CommandError(str(err.get("code", "unknown")), str(err.get("message", "")))
                )
                return
            result = msg.get("result")
            if pending.on_result is not None:
                self._safely(pending.on_result, result)
            pending.future.set_result(result)
        elif kind == "event" and mid == self._sub_id:
            event = msg.get("event")
            if isinstance(event, Mapping):
                self._on_entities_event(event)

    # --- entity stream ---------------------------------------------------------

    async def _subscribe_on_wire(self) -> None:
        if self._sub_id is not None:
            old, self._sub_id = self._sub_id, None
            with contextlib.suppress(CommandError, CommandTimeoutError):
                await self._command({"type": "unsubscribe_events", "subscription": old})
        if not self._wanted:
            self._states = {}
            self._emit(EntityUpdate(full=True))
            return
        init = asyncio.Event()
        self._awaiting_init = init
        mid = self._next_id  # the id _command will use; events carry it
        self._sub_id = mid
        try:
            await self._command({"type": "subscribe_entities", "entity_ids": list(self._wanted)})
            try:
                await self._within(init.wait(), self._command_timeout)
            except TimeoutError as err:
                msg = "no initial entity event for subscribe_entities"
                raise CommandTimeoutError(msg) from err
        except CommandTimeoutError:
            # The server may well hold the subscription: keep its id so that the next
            # subscribe unsubscribes it instead of leaking it.
            raise
        except BaseException:
            self._sub_id = None
            raise
        finally:
            self._awaiting_init = None

    def _on_entities_event(self, event: Mapping[str, Any]) -> None:
        changed: dict[str, EntityState] = {}
        removed: list[str] = []
        full = self._awaiting_init is not None and not self._awaiting_init.is_set()
        if full:
            self._states = {}
        added, diffs, gone, malformed = _split_event(event)
        inconsistent = malformed
        for entity_id, comp in added.items():
            if not isinstance(entity_id, str) or not isinstance(comp, Mapping):
                inconsistent = True
                continue
            changed[entity_id] = self._states[entity_id] = state_from_compressed(entity_id, comp)
        for entity_id, diff in diffs.items():
            previous = self._states.get(entity_id) if isinstance(entity_id, str) else None
            if previous is None or not isinstance(diff, Mapping):
                inconsistent = True  # diff for an entity we never saw (R5) or malformed
                continue
            try:
                changed[entity_id] = self._states[entity_id] = apply_diff(previous, diff)
            except (AttributeError, TypeError, ValueError):
                inconsistent = True
        for entity_id in gone:
            if not isinstance(entity_id, str):
                inconsistent = True
                continue
            if self._states.pop(entity_id, None) is not None:
                changed.pop(entity_id, None)
                removed.append(entity_id)
        if full:
            self._emit(EntityUpdate(changed=dict(self._states), full=True))
            assert self._awaiting_init is not None
            self._awaiting_init.set()
        elif changed or removed:
            self._emit(EntityUpdate(changed=changed, removed=tuple(removed)))
        if inconsistent:
            log.warning("ha.diff_inconsistent_resync")
            self._request_resync()

    async def _resync(self) -> None:
        """Full ``get_states`` resync restricted to the subscription.

        The snapshot is applied synchronously inside the read loop, so it is ordered
        correctly against the events before and after it.
        """
        if not self._wanted:
            return
        await self._command({"type": "get_states"}, on_result=self._apply_snapshot)

    def _apply_snapshot(self, raw: Any) -> None:
        if not isinstance(raw, list):
            return
        wanted = set(self._wanted)
        snapshot: dict[str, EntityState] = {}
        for item in raw:
            if isinstance(item, Mapping):
                state = state_from_full(item)
                if state is not None and state.entity_id in wanted:
                    snapshot[state.entity_id] = state
        self._states = dict(snapshot)
        self._emit(EntityUpdate(changed=snapshot, full=True))

    def _request_resync(self) -> None:
        if self._resync_task is None or self._resync_task.done():
            self._resync_task = asyncio.create_task(self._resync_safely(), name="suncore-ha-resync")

    async def _resync_safely(self) -> None:
        try:
            await self._resync()
        except (CommandError, ConnectionLostError, NotConnectedError, TimeoutError) as err:
            log.warning("ha.resync_failed", reason=str(err))

    async def _periodic_resync(self) -> None:
        while True:
            await self._sleep(self._resync_interval)
            await self._resync_safely()

    # --- commands --------------------------------------------------------------

    async def _command(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_s: float | None = None,
        on_result: Callable[[Any], None] | None = None,
    ) -> Any:
        ws = self._ws
        if ws is None or ws.closed:
            raise NotConnectedError("not connected to Home Assistant")
        mid = self._next_id
        self._next_id += 1
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[mid] = _Pending(future, on_result)
        try:
            await ws.send_json({"id": mid, **payload})
            return await self._within(future, timeout_s or self._command_timeout)
        except TimeoutError as err:
            if isinstance(err, CommandTimeoutError):
                raise
            raise CommandTimeoutError(f"no result for {payload.get('type')!r}") from err
        finally:
            self._pending.pop(mid, None)

    async def _heartbeat(self) -> None:
        while True:
            await self._sleep(self._heartbeat_interval)
            try:
                await self._command({"type": "ping"}, timeout_s=self._heartbeat_timeout)
            except CommandTimeoutError as err:
                raise HeartbeatTimeoutError("no pong from Home Assistant") from err

    async def _read_state(self, entity_id: str) -> EntityState | None:
        for state in await self.get_states():
            if state.entity_id == entity_id:
                return state
        return None

    # --- helpers ---------------------------------------------------------------

    async def _within(self, aw: Awaitable[T], timeout_s: float) -> T:
        """Await ``aw`` for at most ``timeout_s`` seconds of the *injected* clock."""
        task = asyncio.ensure_future(aw)
        timer = asyncio.ensure_future(self._sleep(timeout_s))
        try:
            done, _ = await asyncio.wait({task, timer}, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                return task.result()
            raise TimeoutError
        finally:
            for t in (task, timer):
                if not t.done():
                    t.cancel()

    def _fail_pending(self, error: Exception) -> None:
        pending, self._pending = self._pending, {}
        for item in pending.values():
            if not item.future.done():
                item.future.set_exception(error)
                item.future.exception()  # mark retrieved; the awaiter re-raises

    def _set_state(self, state: ConnectionState) -> None:
        if state is self._state:
            return
        self._state = state
        for listener in list(self._state_listeners):
            self._safely(listener, state)

    def _emit(self, update: EntityUpdate) -> None:
        for listener in list(self._entity_listeners):
            self._safely(listener, update)

    @staticmethod
    def _safely(callback: Callable[[Any], None], value: Any) -> None:
        try:
            callback(value)
        except Exception:
            log.exception("ha.listener_failed")


def _split_event(
    event: Mapping[str, Any],
) -> tuple[Mapping[Any, Any], Mapping[Any, Any], list[Any], bool]:
    """Pick ``a`` / ``c`` / ``r`` of an entity event; wrongly typed parts count as malformed."""
    added, diffs, gone = event.get("a"), event.get("c"), event.get("r")
    malformed = False
    if added is not None and not isinstance(added, Mapping):
        added, malformed = None, True
    if diffs is not None and not isinstance(diffs, Mapping):
        diffs, malformed = None, True
    if gone is not None and not isinstance(gone, list):
        gone, malformed = None, True
    return added or {}, diffs or {}, gone or [], malformed


def _loads(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as err:
        msg = "invalid JSON from Home Assistant"
        raise ProtocolError(msg) from err


def _add_listener(listeners: list[Any], listener: Any) -> Unsubscribe:
    listeners.append(listener)

    def remove() -> None:
        with contextlib.suppress(ValueError):
            listeners.remove(listener)

    return remove
