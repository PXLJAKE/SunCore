# SPDX-License-Identifier: Apache-2.0
"""Commands from Home Assistant: parse, validate, rate-limit, dispatch (ADR-0016 decision 5).

The handler turns a message on ``suncore/<iid>/<object_id>/set`` into exactly one call of an
injected :class:`CommandPort`. The port is implemented in ``suncore.app`` over the application
services (the code the web UI uses), so config validation, the mode rules and the safety gate
apply unchanged. This module never touches a device, the gate or the controller.

Rules, in the order they are applied to every message:

1. a **retained** message is a replay, not a command: ignored (and removed from the broker by
   the caller);
2. the object id must be an announced command entity, the payload at most
   :data:`MAX_PAYLOAD_BYTES` of UTF-8 and valid for the platform (``ON``/``OFF``, ``PRESS``, a
   finite number inside the entity's range);
3. the **emergency stop is never rate-limited and never refused**; everything else is limited to
   one command per second per entity (burst 3) and :data:`GLOBAL_BURST` per
   :data:`GLOBAL_WINDOW_S` over all entities;
4. **raising** autonomy (a mode above the stored one, clearing the emergency stop) needs
   ``mqtt.allow_mode_raise`` (default off) and is otherwise refused with
   ``safety.mqtt_mode_raise_disabled``. Lowering a mode, pause, resume, boost and replan always
   work.

Every message produces a :class:`CommandResult` with a :class:`~suncore.core.decisions.Reason`
that is logged; the caller republishes the entity's true state after a refusal. Accepted
commands are logged one by one; refusals go through :class:`RefusalLogThrottle` (SEC-16): the
first per entity and reason in full, then a count every 60 s.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Protocol

import structlog

from suncore.core.controlstate import OverrideCommand
from suncore.core.decisions import OperatingMode, Reason
from suncore.ha.mqtt.catalogue import EntitySpec, Platform, Scope
from suncore.ha.mqtt.refusals import RefusalLogThrottle

log = structlog.get_logger(__name__)

MAX_PAYLOAD_BYTES: Final = 64
ENTITY_RATE_PER_S: Final = 1.0
ENTITY_BURST: Final = 3
GLOBAL_BURST: Final = 20
GLOBAL_WINDOW_S: Final = 10.0
DEFAULT_BOOST_MINUTES: Final = 60
DISPATCH_TIMEOUT_S: Final = 5.0
"""A non-priority command waits at most this long for the service (e.g. a held operation lock)."""
NUMBER_RE: Final = re.compile(r"-?[0-9]+(\.[0-9]+)?")

PAYLOAD_ON: Final = "ON"
PAYLOAD_OFF: Final = "OFF"
PAYLOAD_PRESS: Final = "PRESS"
"""Defaults of the MQTT switch and button platforms (SunCore does not override them); the
defaults themselves are ``UNVERIFIED`` (see ``docs/research/mqtt-discovery.md``)."""

REASON_ACCEPTED: Final = "mqtt.command.accepted"
REASON_UNCHANGED: Final = "mqtt.command.unchanged"
REASON_INVALID: Final = "mqtt.command.invalid_payload"
REASON_OUT_OF_RANGE: Final = "mqtt.command.out_of_range"
REASON_RATE_LIMITED: Final = "mqtt.command.rate_limited"
REASON_RETAINED: Final = "mqtt.command.retained_ignored"
REASON_UNKNOWN: Final = "mqtt.command.unknown_entity"
REASON_FAILED: Final = "mqtt.command.failed"
REASON_RAISE_DISABLED: Final = "safety.mqtt_mode_raise_disabled"


class CommandPort(Protocol):
    """What a command may do. Implemented in ``suncore.app`` over the application services.

    The method names say *command*: they are the only way this package changes anything and
    each of them is a call of the same service method the web UI uses.
    """

    def stored_global_mode(self) -> OperatingMode: ...

    def stored_device_mode(self, device_id: str) -> OperatingMode | None:
        """``None`` = no such device."""
        ...

    def stored_emergency_settled(self) -> bool:
        """The stop is set and no device still waits to be released (a press changes nothing)."""
        ...

    async def command_emergency_stop(self) -> None: ...

    async def command_clear_stop(self) -> None: ...

    async def command_global_mode(self, mode: OperatingMode) -> None: ...

    async def command_device_mode(self, device_id: str, mode: OperatingMode) -> None: ...

    async def command_override(self, device_id: str, command: OverrideCommand) -> None: ...

    async def command_replan(self) -> None: ...


@dataclass(frozen=True, slots=True)
class CommandResult:
    object_id: str
    accepted: bool
    reason: Reason

    @property
    def refused(self) -> bool:
        return not self.accepted


class TokenBucket:
    """``burst`` tokens, refilled at ``rate`` per second (monotonic clock)."""

    def __init__(self, rate: float, burst: int, clock: Callable[[], float]) -> None:
        self._rate = rate
        self._burst = float(burst)
        self._clock = clock
        self._tokens = float(burst)
        self._at = clock()

    def take(self) -> bool:
        now = self._clock()
        self._tokens = min(self._burst, self._tokens + (now - self._at) * self._rate)
        self._at = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class _SlidingWindow:
    def __init__(self, limit: int, window_s: float, clock: Callable[[], float]) -> None:
        self._limit = limit
        self._window = window_s
        self._clock = clock
        self._stamps: deque[float] = deque()

    def take(self) -> bool:
        now = self._clock()
        while self._stamps and now - self._stamps[0] >= self._window:
            self._stamps.popleft()
        if len(self._stamps) >= self._limit:
            return False
        self._stamps.append(now)
        return True


@dataclass(frozen=True, slots=True)
class _Target:
    spec: EntitySpec
    device_id: str | None


def _result(object_id: str, accepted: bool, key: str, **args: str | int | float) -> CommandResult:
    return CommandResult(object_id, accepted, Reason(key=key, args=args))


class CommandHandler:
    def __init__(
        self,
        port: CommandPort,
        *,
        allow_mode_raise: bool,
        clock: Callable[[], float] = time.monotonic,
        timeout_s: float = DISPATCH_TIMEOUT_S,
    ) -> None:
        self._timeout_s = timeout_s
        self._port = port
        self._allow_raise = allow_mode_raise
        self._clock = clock
        self._targets: dict[str, _Target] = {}
        self._buckets: dict[str, TokenBucket] = {}
        self._global = _SlidingWindow(GLOBAL_BURST, GLOBAL_WINDOW_S, clock)
        self._boost_minutes: dict[str, int] = {}
        self._refusal_log = RefusalLogThrottle(clock)

    def revoke_raise(self) -> None:
        """The opt-in was withdrawn: from now on every raise is refused (also mid-session)."""
        self._allow_raise = False

    def is_priority(self, object_id: str) -> bool:
        """The emergency stop: never queued behind other commands, never rate-limited."""
        target = self._targets.get(object_id)
        return target is not None and target.spec.key == "emergency_stop_button"

    def is_valid_payload(self, object_id: str, payload: bytes) -> bool:
        """The payload parses for the entity (no effect, no rate-limit token taken)."""
        target = self._targets.get(object_id)
        return target is not None and not isinstance(
            self._parse(target.spec, payload), CommandResult
        )

    # --- what is commandable -----------------------------------------------------------

    def bind(self, entities: Iterable[tuple[EntitySpec, str]]) -> None:
        """The announced command entities as ``(spec, object_id)``; replaces the old set."""
        targets: dict[str, _Target] = {}
        for spec, object_id in entities:
            if not spec.command:
                continue
            device_id = None
            if spec.scope is Scope.DEVICE:
                device_id = object_id[: -len(spec.suffix) - 1]
            targets[object_id] = _Target(spec, device_id)
        self._targets = targets
        self._buckets = {k: v for k, v in self._buckets.items() if k in targets}
        self._boost_minutes = {
            k: v
            for k, v in self._boost_minutes.items()
            if k in {t.device_id for t in targets.values()}
        }

    def local_values(self) -> Mapping[str, object]:
        """States the handler owns (the boost duration each device's boost button uses)."""
        out: dict[str, object] = {}
        for object_id, target in self._targets.items():
            if target.spec.key == "device_boost_minutes" and target.device_id is not None:
                out[object_id] = self._boost_minutes.get(target.device_id, DEFAULT_BOOST_MINUTES)
        return out

    # --- entry point ---------------------------------------------------------------------

    async def handle(self, object_id: str, payload: bytes, *, retain: bool) -> CommandResult:
        result = await self._handle(object_id, payload, retain)
        if result.accepted:
            log.info(
                "mqtt.command",
                entity=object_id,
                accepted=True,
                reason=result.reason.key,
                args=dict(result.reason.args),
            )
        elif self._refusal_log.record(object_id, result.reason.key):
            log.warning(
                "mqtt.command",
                entity=object_id,
                accepted=False,
                reason=result.reason.key,
                args=dict(result.reason.args),
                retained=retain,
            )
        self.log_refusal_summary()
        return result

    def log_refusal_summary(self) -> None:
        """Log the count of refusals not logged one by one, once per interval (SEC-16).

        Called after every command and from the caller's periodic sync, so the count of a
        flood that has stopped still appears."""
        counts = self._refusal_log.summary()
        if counts:
            log.warning(
                "mqtt.command_refused_summary",
                suppressed=[
                    {"entity": c.entity, "reason": c.reason, "count": c.suppressed} for c in counts
                ],
                total=sum(c.suppressed for c in counts),
            )

    async def _handle(  # noqa: PLR0911 - one verdict per refusal
        self, object_id: str, payload: bytes, retain: bool
    ) -> CommandResult:
        if retain:
            return _result(object_id, False, "mqtt.command.retained_ignored")
        target = self._targets.get(object_id)
        if target is None:
            return _result(object_id, False, "mqtt.command.unknown_entity")
        spec = target.spec
        parsed = self._parse(spec, payload)
        if isinstance(parsed, CommandResult):
            return CommandResult(object_id, False, parsed.reason)
        priority = self.is_priority(object_id)
        if not priority and not self._admit(object_id):
            return _result(object_id, False, "mqtt.command.rate_limited")
        try:
            if priority:
                return await self._dispatch(object_id, target, parsed)
            async with asyncio.timeout(self._timeout_s):
                return await self._dispatch(object_id, target, parsed)
        except TimeoutError:
            log.warning("mqtt.command_timeout", entity=object_id)
            return _result(object_id, False, "mqtt.command.failed", error="timeout")
        except Exception as exc:
            log.exception("mqtt.command_failed", entity=object_id)
            return _result(object_id, False, "mqtt.command.failed", error=type(exc).__name__)

    def _admit(self, object_id: str) -> bool:
        bucket = self._buckets.get(object_id)
        if bucket is None:
            bucket = self._buckets[object_id] = TokenBucket(
                ENTITY_RATE_PER_S, ENTITY_BURST, self._clock
            )
        return bucket.take() and self._global.take()

    # --- parsing ---------------------------------------------------------------------------

    @staticmethod
    def _parse(  # noqa: PLR0911 - one verdict per platform and failure
        spec: EntitySpec, payload: bytes
    ) -> bool | float | CommandResult:
        """The typed value (switch: ``True`` = ON; button: ``True``; number: the float)."""
        invalid = CommandResult("", False, Reason(key=REASON_INVALID))
        if len(payload) > MAX_PAYLOAD_BYTES:
            return invalid
        try:
            text = payload.decode("utf-8").strip()
        except UnicodeDecodeError:
            return invalid
        if spec.platform is Platform.SWITCH:
            if text in (PAYLOAD_ON, PAYLOAD_OFF):
                return text == PAYLOAD_ON
            return invalid
        if spec.platform is Platform.BUTTON:
            return True if text == PAYLOAD_PRESS else invalid
        if spec.platform is Platform.NUMBER:
            if NUMBER_RE.fullmatch(text) is None:
                return invalid
            value = float(text)
            if not math.isfinite(value):
                return invalid
            low = spec.min_value if spec.min_value is not None else -math.inf
            high = spec.max_value if spec.max_value is not None else math.inf
            if not low <= value <= high or (spec.step == 1 and value != int(value)):
                return CommandResult("", False, Reason(key=REASON_OUT_OF_RANGE))
            return value
        return invalid  # select and text commands arrive with the entities that need them

    # --- dispatch --------------------------------------------------------------------------

    async def _dispatch(  # noqa: PLR0911, PLR0912 - one branch per command
        self, object_id: str, target: _Target, value: bool | float
    ) -> CommandResult:
        key = target.spec.key
        port = self._port
        device_id = target.device_id
        if key == "emergency_stop_button":
            if port.stored_emergency_settled():
                return _result(object_id, True, REASON_UNCHANGED, entity=object_id)
            await port.command_emergency_stop()
        elif key == "emergency_stop_clear":
            if not self._allow_raise:
                return _result(object_id, False, REASON_RAISE_DISABLED)
            await port.command_clear_stop()
        elif key == "replan":
            await port.command_replan()
        elif key == "auto":
            return await self._mode(object_id, None, bool(value), port.stored_global_mode())
        elif device_id is None:
            return _result(object_id, False, "mqtt.command.unknown_entity")
        elif key == "device_auto":
            return await self._mode(
                object_id, device_id, bool(value), port.stored_device_mode(device_id)
            )
        elif key == "device_boost":
            minutes = self._boost_minutes.get(device_id, DEFAULT_BOOST_MINUTES)
            await port.command_override(device_id, OverrideCommand(action="boost", minutes=minutes))
        elif key == "device_pause":
            await port.command_override(device_id, OverrideCommand(action="pause"))
        elif key == "device_resume":
            await port.command_override(device_id, OverrideCommand(action="resume"))
        elif key == "device_boost_minutes":
            self._boost_minutes[device_id] = int(float(value))  # a stored parameter, no effect
        else:
            return _result(object_id, False, "mqtt.command.unknown_entity")
        return _result(object_id, True, REASON_ACCEPTED, entity=object_id)

    async def _mode(
        self, object_id: str, device_id: str | None, on: bool, current: OperatingMode | None
    ) -> CommandResult:
        """``ON`` = automatic (a raise), ``OFF`` = shadow (always allowed)."""
        if current is None:
            return _result(object_id, False, "mqtt.command.unknown_entity")
        target = OperatingMode.AUTOMATIC if on else OperatingMode.SHADOW
        if target is current:
            return _result(object_id, True, REASON_UNCHANGED, entity=object_id)
        if target.rank > current.rank and not self._allow_raise:
            return _result(object_id, False, REASON_RAISE_DISABLED)
        if device_id is None:
            await self._port.command_global_mode(target)
        else:
            await self._port.command_device_mode(device_id, target)
        return _result(object_id, True, REASON_ACCEPTED, entity=object_id)
