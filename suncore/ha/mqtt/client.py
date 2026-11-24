# SPDX-License-Identifier: Apache-2.0
"""The MQTT client: connect, back-off, last will, subscriptions, publish (ADR-0016 decision 2).

A thin wrapper on ``aiomqtt`` (``paho-mqtt`` 2.x). :meth:`MqttClient.run` connects, announces
itself through ``on_connected``, then dispatches incoming messages until the connection ends,
and reconnects with exponential back-off. MQTT 3.1.1 (or 3.1 when the Supervisor says so).
The client never raises into the caller: a failed publish returns ``False``.
"""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

import aiomqtt
import structlog

from suncore.ha.mqtt.broker import BrokerSettings
from suncore.ha.websocket import Backoff

log = structlog.get_logger(__name__)

CONNECT_TIMEOUT_S: Final = 10.0
PUBLISH_TIMEOUT_S: Final = 10.0
KEEPALIVE_S: Final = 30


class LinkState(StrEnum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Will:
    topic: str
    payload: str
    qos: int = 1
    retain: bool = True


def classify_error(exc: BaseException) -> str:
    """Reason key for a connection failure (looks through ``__cause__``/``__context__``)."""
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    for item in chain:
        if isinstance(item, ssl.SSLError):
            return "mqtt.reason.tls_failed"
    text = " ".join(str(item).lower() for item in chain)
    if any(w in text for w in ("not authori", "bad user", "bad username", "credentials")):
        return "mqtt.reason.auth_failed"
    if any(isinstance(item, TimeoutError) for item in chain) or "timed out" in text:
        return "mqtt.reason.timeout"
    if any(isinstance(item, OSError) for item in chain) or "refused" in text:
        return "mqtt.reason.connect_refused"
    return "mqtt.reason.unknown"


OnMessage = Callable[[str, bytes], Awaitable[None] | None]
OnLink = Callable[[LinkState, str | None], None]


class MqttClient:
    def __init__(
        self,
        settings: BrokerSettings,
        *,
        client_id: str,
        will: Will | None,
        subscriptions: Sequence[tuple[str, int]],
        on_connected: Callable[[], Awaitable[None]],
        on_message: OnMessage,
        on_link: OnLink,
        backoff: Backoff | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        client_factory: Callable[..., Any] = aiomqtt.Client,
    ) -> None:
        self._settings = settings
        self._client_id = client_id
        self._will = will
        self._subscriptions = tuple(subscriptions)
        self._on_connected = on_connected
        self._on_message = on_message
        self._on_link = on_link
        self._backoff = backoff if backoff is not None else Backoff()
        self._sleep = sleep
        self._factory = client_factory
        self._client: Any = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    def _build(self) -> Any:
        s = self._settings
        tls_context = ssl.create_default_context() if s.tls else None
        return self._factory(
            hostname=s.host,
            port=s.port,
            username=s.username,
            password=s.password,
            identifier=self._client_id,
            protocol=aiomqtt.ProtocolVersion.V31
            if s.protocol == "3.1"
            else aiomqtt.ProtocolVersion.V311,
            keepalive=KEEPALIVE_S,
            timeout=CONNECT_TIMEOUT_S,
            tls_context=tls_context,
            will=None
            if self._will is None
            else aiomqtt.Will(
                topic=self._will.topic,
                payload=self._will.payload,
                qos=self._will.qos,
                retain=self._will.retain,
            ),
        )

    async def run(self) -> None:
        """Connect and serve until cancelled; reconnects after every failure."""
        while True:
            self._on_link(LinkState.CONNECTING, None)
            reason: str | None = None
            try:
                async with self._build() as client:
                    for topic, qos in self._subscriptions:
                        await client.subscribe(topic, qos=qos)
                    self._client = client
                    self._backoff.reset()
                    self._on_link(LinkState.CONNECTED, None)
                    await self._on_connected()
                    async for message in client.messages:
                        result = self._on_message(message.topic.value, bytes(message.payload))
                        if result is not None:
                            await result
                    reason = "mqtt.reason.connect_refused"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reason = classify_error(exc)
                log.warning("mqtt.connection_failed", reason=reason, error=type(exc).__name__)
            finally:
                self._client = None
            self._on_link(LinkState.ERROR, reason)
            await self._sleep(self._backoff.next_delay())

    async def publish(
        self, topic: str, payload: str | bytes, qos: int = 0, retain: bool = False
    ) -> bool:
        client = self._client
        if client is None:
            return False
        try:
            await client.publish(
                topic, payload=payload, qos=qos, retain=retain, timeout=PUBLISH_TIMEOUT_S
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("mqtt.publish_failed", error=type(exc).__name__)
            return False
        return True
