# SPDX-License-Identifier: Apache-2.0
"""The MQTT link to evcc: topic cache, heartbeat and the ``/set`` publisher (PR 6-J).

One connection to the same broker as the HA entity surface (``mqtt`` section, Supervisor
service or manual settings; ADR-0016), with its own client id, subscribed to
``<prefix>/loadpoints/<id>/+`` of every configured evcc loadpoint and to ``<prefix>/updated``.
It implements the narrow bus the evcc driver sees (:class:`suncore.devices.evcc.EvccBus`): the
last value per topic, "is evcc alive" and "publish one not retained ``/set`` message" - the only
write path to evcc, reachable only through the driver and so only through the safety gate
(``tests/ha/mqtt/test_boundary.py``).

evcc's heartbeat is ``<prefix>/updated`` (Unix seconds, published at most once a second while
anything changes). evcc counts as alive only while the link is up **and** that value is younger
than 120 s by evcc's own clock and was received within 120 s; a stale retained value replayed
after a reconnect therefore never makes a dead evcc look alive. A broker without a retained copy
or an evcc that went silent make every read ``None`` / not alive: the driver reports its state
unavailable, the watchdog trips the device and the gate releases it.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import Any, Final

import structlog

from suncore.core.config import MqttConfig
from suncore.core.devices import DeviceConfig, LoadpointControl
from suncore.devices.evcc import HEARTBEAT_MAX_AGE_S, EvccTopicListener
from suncore.ha.mqtt.broker import SupervisorServices, lookup_broker
from suncore.ha.mqtt.client import LinkState, MqttClient

log = structlog.get_logger(__name__)

RETRY_S: Final = 60.0
SET_QOS: Final = 1
HEARTBEAT_KEY: Final = "updated"

Target = tuple[str, int]
"""``(topic prefix, loadpoint id)``."""


def targets_of(devices: Iterable[DeviceConfig]) -> frozenset[Target]:
    """The loadpoints of ``devices`` as ``(prefix, id)`` pairs."""
    return frozenset(
        (d.control.topic_prefix, d.control.loadpoint_id)
        for d in devices
        if isinstance(d.control, LoadpointControl)
    )


def subscriptions_of(targets: Iterable[Target]) -> list[tuple[str, int]]:
    """The topic filters of ``targets`` (one level below each loadpoint, plus the heartbeat)."""
    filters: dict[str, int] = {}
    for prefix, loadpoint in sorted(targets):
        filters[f"{prefix}/loadpoints/{loadpoint}/+"] = 0
        filters[f"{prefix}/{HEARTBEAT_KEY}"] = 0
    return list(filters.items())


class EvccLink:
    """See the module docstring; implements ``EvccBus`` and ``EvccEvents``."""

    def __init__(
        self,
        *,
        load_config: Callable[[], Awaitable[MqttConfig | None]],
        services: SupervisorServices | None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        client_factory: Callable[..., Any] | None = None,
        retry_s: float = RETRY_S,
        max_age_s: float = HEARTBEAT_MAX_AGE_S,
    ) -> None:
        self._load_config = load_config
        self._services = services
        self._clock = clock
        self._client_factory = client_factory
        self._retry_s = retry_s
        self._max_age_s = max_age_s
        self._client_id = f"suncore-evcc-{secrets.token_hex(4)}"
        self._targets: frozenset[Target] = frozenset()
        self._values: dict[str, str] = {}
        self._beats: dict[str, tuple[datetime, datetime]] = {}
        """Prefix -> (evcc's own timestamp, when it was received)."""
        self._listeners: list[EvccTopicListener] = []
        self._client: MqttClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._changed = asyncio.Event()
        self._link = LinkState.CONNECTING
        self.reason: str | None = None
        """Reason key while the link is down (a broker lookup or connection failure)."""

    # --- configuration and lifecycle -------------------------------------------------------

    def configure(self, devices: Iterable[DeviceConfig]) -> None:
        """The loadpoints to follow; a change restarts the session."""
        targets = targets_of(devices)
        if targets != self._targets:
            self._targets = targets
            self._changed.set()

    def reconfigure(self) -> None:
        """The ``mqtt`` section changed: look the broker up again."""
        self._changed.set()

    def ensure_started(self) -> None:
        """Start the session task if it is not running (needs a running event loop)."""
        if self._task is None:
            self._task = asyncio.create_task(self._supervise(), name="suncore-evcc-link")

    async def start(self) -> None:
        self.ensure_started()

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    @property
    def connected(self) -> bool:
        return self._client is not None and self._link is LinkState.CONNECTED

    # --- EvccBus ---------------------------------------------------------------------------

    def value(self, topic: str) -> str | None:
        return self._values.get(topic)

    def alive(self, prefix: str) -> bool:
        if not self.connected:
            return False
        beat = self._beats.get(prefix)
        if beat is None:
            return False
        stamp, seen = beat
        now = self._clock()
        return (now - seen).total_seconds() <= self._max_age_s and (
            (now - stamp).total_seconds() <= self._max_age_s
        )

    async def publish_set(self, topic: str, payload: str) -> bool:
        client = self._client
        if client is None or not self.connected:
            return False
        return await client.publish(topic, payload, SET_QOS, False)

    # --- EvccEvents ------------------------------------------------------------------------

    def add_listener(self, listener: EvccTopicListener) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return remove

    # --- the session -----------------------------------------------------------------------

    async def _supervise(self) -> None:
        while True:
            self._changed.clear()
            config = await self._load_config() or MqttConfig()
            targets = self._targets
            if not targets:
                await self._wait(None)
                continue
            lookup = await lookup_broker(config, self._services)
            if lookup.settings is None:
                self.reason = lookup.reason_key
                await self._wait(self._retry_s)
                continue
            factory: dict[str, Any] = (
                {} if self._client_factory is None else {"client_factory": self._client_factory}
            )
            client = MqttClient(
                lookup.settings,
                client_id=self._client_id,
                will=None,
                subscriptions=subscriptions_of(targets),
                on_connected=self._on_connected,
                on_message=self._on_message,
                on_link=self._on_link,
                **factory,
            )
            self._client = client
            runner = asyncio.create_task(client.run(), name="suncore-evcc-mqtt")
            try:
                await self._wait(None)
            finally:
                runner.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await runner
                self._client = None
                self._link = LinkState.CONNECTING
                self._values.clear()
                self._beats.clear()

    async def _wait(self, seconds: float | None) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._changed.wait(), seconds)

    async def _on_connected(self) -> None:
        """A new connection: the broker replays the retained messages; start from nothing."""
        self._values.clear()
        self._beats.clear()

    def _on_link(self, link: LinkState, reason: str | None) -> None:
        self._link = link
        self.reason = reason

    def _on_message(self, topic: str, payload: bytes) -> None:
        text = payload.decode("utf-8", "replace").strip()
        if not text:
            self._values.pop(topic, None)
        else:
            self._values[topic] = text
        for prefix, _ in self._targets:
            if topic == f"{prefix}/{HEARTBEAT_KEY}":
                self._beat(prefix, text)
                return
        for listener in list(self._listeners):
            try:
                listener(topic)
            except Exception:
                log.exception("evcc.listener_failed")

    def _beat(self, prefix: str, text: str) -> None:
        try:
            stamp = datetime.fromtimestamp(int(text), UTC)
        except (ValueError, OverflowError, OSError):
            self._beats.pop(prefix, None)
            return
        self._beats[prefix] = (stamp, self._clock())


__all__ = ["EvccLink", "subscriptions_of", "targets_of"]
