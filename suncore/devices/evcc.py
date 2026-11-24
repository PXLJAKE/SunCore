# SPDX-License-Identifier: Apache-2.0
"""evcc as actor: a loadpoint driven over evcc's MQTT API (``LoadpointControl``, ADR-0017
decision 2; PR 6-J).

SunCore sets what evcc accepts (mode, ``limitSoc``, ``maxCurrent``); evcc regulates the current
and the phases itself. SunCore never writes ``<prefix>/site/*`` (evcc's battery rules stay the
user's, plan §6 question 7) and never ``minCurrent``/``phasesConfigured`` (evcc's own wiring).

Verified against ``evcc-io/evcc`` ``master`` on 2026-11-24 (``server/mqtt.go``,
``server/mqtt_setter.go``, ``core/keys/loadpoint.go``, ``core/loadpoint_api.go``,
``plugin/mqtt/client.go``):

* a loadpoint is addressed as ``<prefix>/loadpoints/<id>`` with ``<id>`` = its index **plus
  one** (ids start at 1); a value is written by publishing to ``<topic>/set`` (the listener
  ignores empty payloads and resets the topic with a retained empty payload afterwards, so SunCore
  publishes **not retained**);
* ``mode``: ``off``, ``now``, ``smart`` (the deprecated ``pv``/``minpv`` are accepted and
  normalised by evcc; SunCore never sends them); ``maxCurrent`` float A (``>= minCurrent`` or
  evcc logs an error and keeps the old value); ``limitSoc`` integer %;
* the values are published retained under the same names without ``/set`` (``mode``,
  ``maxCurrent``, ``minCurrent``, ``limitSoc``, ``connected``, ``charging``, ``chargePower``,
  ``vehicleSoc``, ``phasesActive``, ``enabled``): booleans as ``true``/``false``, floats with
  up to three decimals, the vehicle SoC is ``0`` while no vehicle is connected;
* ``<prefix>/updated`` carries the Unix time (s) of evcc's latest publication, at most once a
  second while anything changes: it is evcc's heartbeat.

``UNVERIFIED``: the vehicle plan (``vehicles/<name>/planSoc/set``) is a JSON ``{"value","time"}``
but evcc's setter accepts nothing to *clear* it (the plan page says ``nil``; the source parses
JSON only), so this driver **refuses** plan writes and SunCore never writes one; evcc's
behaviour at vehicle disconnect (it resets ``mode`` to its configured default) looks like an
override to the gate; how fast evcc reacts to ``maxCurrent`` while charging.
Liveness rests on ``<prefix>/updated``: evcc publishes it on change only (a quiet evcc may
exceed 120 s) and its timestamp is evcc's own clock (a clock running ahead makes an old
retained value look fresh).

The driver is dumb (ADR-0011): ``apply`` publishes only what differs, ``read_back`` reports the
topics, ``release`` restores the takeover baseline and proves it by the echo. It never decides;
only ``safety.gate`` calls ``apply``/``release``.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from typing import Final, Literal, Protocol, runtime_checkable

from suncore.core.control import DeviceState
from suncore.core.decisions import Action, Intent, LoadpointAction
from suncore.core.devices import DeviceConfig, LoadpointControl
from suncore.core.signals import Quality
from suncore.devices.drivers.base import (
    ActionNotSupportedError,
    DriverError,
    WriteRefusedError,
)

HEARTBEAT_MAX_AGE_S: Final = 120.0
"""No evcc publication for this long: evcc is unreachable (plan WP10, ADR-0017 decision 2)."""
ECHO_TIMEOUT_S: Final = 3.0
"""How long a release waits for evcc to show the restored values."""
ECHO_POLL_S: Final = 0.1
CURRENT_TOLERANCE_A: Final = 0.05
LIMIT_SOC_MAX: Final = 100

READ_KEYS: Final = (
    "mode",
    "maxCurrent",
    "minCurrent",
    "limitSoc",
    "connected",
    "charging",
    "chargePower",
    "vehicleSoc",
    "phasesActive",
)
"""The loadpoint topics the link subscribes to and the driver reads."""

Mode = Literal["off", "smart", "now"]
_MODES: Final = ("off", "smart", "now")


class EvccBus(Protocol):
    """What the driver needs from the MQTT side (implemented by ``suncore.app.evcc_link``).

    ``value`` answers from the last retained/live message of a topic (``None`` = never seen or
    erased by an empty payload); ``alive`` says that the broker link is up **and** evcc's
    heartbeat (``<prefix>/updated``) is younger than :data:`HEARTBEAT_MAX_AGE_S`;
    ``publish_set`` sends one not retained message and returns whether the broker took it.
    """

    def value(self, topic: str) -> str | None: ...

    def alive(self, prefix: str) -> bool: ...

    async def publish_set(self, topic: str, payload: str) -> bool: ...


EvccTopicListener = Callable[[str], None]
"""Called with the topic of every message that arrives from evcc."""


@runtime_checkable
class EvccEvents(Protocol):
    """An :class:`EvccBus` that also tells about arriving messages (the override feed)."""

    def add_listener(self, listener: EvccTopicListener) -> Callable[[], None]:
        """Register ``listener``; the result unregisters it."""
        ...


def loadpoint_topic(control: LoadpointControl, key: str) -> str:
    """``<prefix>/loadpoints/<id>/<key>`` (the read topic; append ``/set`` to write)."""
    return f"{control.topic_prefix}/loadpoints/{control.loadpoint_id}/{key}"


def parse_float(payload: str | None) -> float | None:
    if payload is None:
        return None
    try:
        number = float(payload.strip())
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def parse_bool(payload: str | None) -> bool | None:
    if payload is None:
        return None
    text = payload.strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    return None


def mode_of(payload: str | None) -> Mode | None:
    """evcc's mode as one of the three SunCore knows (``None`` for anything else)."""
    if payload is None:
        return None
    text = payload.strip().lower()
    for mode in _MODES:
        if text == mode:
            return mode
    return None


class EvccDriver:
    """The actuator of one evcc loadpoint (structurally a ``safety.actuator.Actuator``)."""

    def __init__(
        self,
        device: DeviceConfig,
        *,
        bus: EvccBus | None,
        echo_timeout_s: float = ECHO_TIMEOUT_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        control = device.control
        if not isinstance(control, LoadpointControl):
            msg = f"EvccDriver needs a loadpoint control, got {control.kind}"
            raise TypeError(msg)
        self._device = device
        self._control = control
        self._bus = bus
        self._echo_timeout_s = echo_timeout_s
        self._sleep = sleep

    @property
    def asset_id(self) -> str:
        return self._device.id

    @property
    def device(self) -> DeviceConfig:
        return self._device

    @property
    def control(self) -> LoadpointControl:
        return self._control

    # --- reading ------------------------------------------------------------------------

    def _alive(self) -> bool:
        return self._bus is not None and self._bus.alive(self._control.topic_prefix)

    def _read(self, key: str) -> str | None:
        if self._bus is None:
            return None
        return self._bus.value(loadpoint_topic(self._control, key))

    def min_current_a(self) -> float | None:
        """evcc's own ``minCurrent`` (invariant L1 keeps ``maxCurrent`` above it)."""
        return parse_float(self._read("minCurrent")) if self._alive() else None

    async def read_back(self) -> Action | None:
        if not self._alive():
            return None
        mode = mode_of(self._read("mode"))
        max_current = parse_float(self._read("maxCurrent"))
        if mode is None or max_current is None or max_current <= 0:
            return None
        limit = parse_float(self._read("limitSoc"))
        return LoadpointAction(
            mode=mode,
            max_current_a=max_current,
            limit_soc_pct=None if limit is None or not 0 <= limit <= LIMIT_SOC_MAX else limit,
        )

    def device_state(self) -> DeviceState:
        if not self._alive():
            return DeviceState(quality=Quality.UNAVAILABLE)
        mode = mode_of(self._read("mode"))
        if mode is None:
            return DeviceState(quality=Quality.UNAVAILABLE)
        connected = parse_bool(self._read("connected"))
        power = parse_float(self._read("chargePower"))
        charging = parse_bool(self._read("charging"))
        active = parse_float(self._read("phasesActive"))
        soc = parse_float(self._read("vehicleSoc"))
        max_current = parse_float(self._read("maxCurrent"))
        limit = parse_float(self._read("limitSoc"))
        return DeviceState(
            on=charging,
            mode=mode,
            value=None if power is None else max(0.0, power),
            connected=connected,
            phases=1 if active == 1 else (3 if active == 3 else None),
            # evcc publishes 0 while no vehicle is connected: not a reading
            soc_pct=soc if connected and soc is not None and 0 < soc <= LIMIT_SOC_MAX else None,
            max_current_a=max_current if max_current is not None and max_current > 0 else None,
            limit_soc_pct=limit if limit is not None and 0 <= limit <= LIMIT_SOC_MAX else None,
        )

    # --- writing ------------------------------------------------------------------------

    async def apply(self, intent: Intent) -> str | None:
        action = intent.action
        if not isinstance(action, LoadpointAction):
            raise ActionNotSupportedError(f"EvccDriver cannot apply a {action.kind.value} action")
        if action.plan_soc_pct is not None or action.plan_time is not None:
            msg = "a vehicle plan cannot be cleared over evcc's MQTT API (UNVERIFIED): refused"
            raise WriteRefusedError(msg)
        await self._write(action)
        return None

    async def _write(self, action: LoadpointAction) -> None:
        if not self._alive():
            raise WriteRefusedError(f"evcc at {self._control.topic_prefix!r} is not reachable")
        observed_mode = mode_of(self._read("mode"))
        lowering_first = action.mode == "off"
        writes: list[tuple[str, str]] = []
        if action.limit_soc_pct is not None:
            limit = round(action.limit_soc_pct)
            if parse_float(self._read("limitSoc")) is None:
                raise WriteRefusedError("evcc shows no limitSoc: not written")
            if parse_float(self._read("limitSoc")) != limit:
                writes.append(("limitSoc", str(limit)))
        if action.max_current_a is not None:
            current = round(action.max_current_a, 3)
            minimum = parse_float(self._read("minCurrent"))
            if minimum is not None and current < minimum - CURRENT_TOLERANCE_A:
                raise WriteRefusedError(f"maxCurrent {current} A is below evcc's minCurrent")
            seen = parse_float(self._read("maxCurrent"))
            if seen is None or abs(seen - current) > CURRENT_TOLERANCE_A:
                writes.append(("maxCurrent", f"{current:g}"))
        if observed_mode != action.mode:
            mode_write = ("mode", action.mode)
            writes = [mode_write, *writes] if lowering_first else [*writes, mode_write]
        for key, payload in writes:
            await self._send(key, payload)

    async def _send(self, key: str, payload: str) -> None:
        assert self._bus is not None
        topic = f"{loadpoint_topic(self._control, key)}/set"
        if not await self._bus.publish_set(topic, payload):
            raise DriverError(f"publish to {topic} failed")

    async def release(self, baseline: Action | None) -> None:
        """Restore the takeover baseline and prove it by evcc's echo (best effort per field).

        Mode first, then ``maxCurrent`` clamped up to evcc's own ``minCurrent`` (the baseline may
        lie below a minimum evcc has raised since), ``limitSoc`` only while evcc shows one. The
        echo is checked on the mode and ``maxCurrent`` only: a missing ``limitSoc`` or a changed
        minimum must never make "back to normal" impossible. evcc keeps what SunCore wrote (it
        persists it), so the release refuses while evcc is silent - the gate retries.
        """
        if not isinstance(baseline, LoadpointAction):
            return
        if not self._alive():
            raise WriteRefusedError(f"evcc at {self._control.topic_prefix!r} is not reachable")
        target = baseline.max_current_a
        minimum = parse_float(self._read("minCurrent"))
        if target is not None and minimum is not None:
            target = max(target, minimum)
        writes: list[tuple[str, str]] = []
        if mode_of(self._read("mode")) != baseline.mode:
            writes.append(("mode", baseline.mode))
        if target is not None:
            seen = parse_float(self._read("maxCurrent"))
            if seen is None or abs(seen - target) > CURRENT_TOLERANCE_A:
                writes.append(("maxCurrent", f"{round(target, 3):g}"))
        if baseline.limit_soc_pct is not None:
            shown = parse_float(self._read("limitSoc"))
            if shown is not None and shown != round(baseline.limit_soc_pct):
                writes.append(("limitSoc", str(round(baseline.limit_soc_pct))))
        for key, payload in writes:
            await self._send(key, payload)
        waited = 0.0
        while True:
            if self._alive() and self._restored(baseline.mode, target):
                return
            if waited >= self._echo_timeout_s:
                raise DriverError("evcc did not show the restored values")
            await self._sleep(ECHO_POLL_S)
            waited += ECHO_POLL_S

    def _restored(self, mode: str, current: float | None) -> bool:
        if mode_of(self._read("mode")) != mode:
            return False
        if current is None:
            return True
        seen = parse_float(self._read("maxCurrent"))
        return seen is not None and abs(seen - current) <= CURRENT_TOLERANCE_A


__all__ = [
    "CURRENT_TOLERANCE_A",
    "ECHO_TIMEOUT_S",
    "HEARTBEAT_MAX_AGE_S",
    "READ_KEYS",
    "EvccBus",
    "EvccDriver",
    "EvccEvents",
    "EvccTopicListener",
    "loadpoint_topic",
    "mode_of",
    "parse_bool",
    "parse_float",
]
