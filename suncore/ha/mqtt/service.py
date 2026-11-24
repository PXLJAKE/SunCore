# SPDX-License-Identifier: Apache-2.0
"""The HA entity surface: broker lookup, session, discovery, states, events (ADR-0016).

``HaEntityService`` owns one MQTT session at a time. It never writes to a device and never
calls the controller or the gate; it only reads what an :class:`EntitySource` offers and
publishes it. Nothing else in SunCore depends on it: no broker means status ``no_broker``
(or ``error``) with a reason key and the retry every ``retry_s`` seconds.

Lifecycle of a session: connect (last will ``offline`` on the status topic), publish
``online``, publish the discovery configs (QoS 1, **not retained**), reset and publish the
states (retained). On every HA birth message (``homeassistant/status`` = ``online``) the
configs and states are published again after a random 1-5 s delay. Devices that vanished since
the last announcement are removed with an empty config payload.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Final, Protocol

import structlog

from suncore.core.config import MqttConfig
from suncore.core.haentities import BrokerState, HaEntitiesStatus
from suncore.ha.mqtt.broker import (
    BrokerLookup,
    BrokerSettings,
    SupervisorServices,
    lookup_broker,
)
from suncore.ha.mqtt.client import LinkState, MqttClient, Will
from suncore.ha.mqtt.commands import CommandHandler, CommandPort
from suncore.ha.mqtt.discovery import (
    BIRTH_PAYLOAD,
    BIRTH_TOPIC,
    PAYLOAD_OFFLINE,
    PAYLOAD_ONLINE,
    HaDevice,
    Topics,
    Topology,
    build_devices,
    device_payload,
    encode,
    new_install_id,
    render_state,
)
from suncore.ha.mqtt.states import MIN_INTERVAL_S, StateItem, StatePublisher
from suncore.i18n import load_catalogue

log = structlog.get_logger(__name__)

RETRY_S: Final = 60.0
SYNC_POLL_S: Final = 5.0
BIRTH_DELAY_S: Final = (1.0, 5.0)


@dataclass(frozen=True, slots=True)
class SiteEvent:
    """One entry for the ``events`` entity (``type`` is one of ``catalogue.EVENT_TYPES``)."""

    type: str
    attrs: Mapping[str, Any] = field(default_factory=dict)


class EntitySource(Protocol):
    """What the surface reads from SunCore (implemented in ``suncore.app``)."""

    def topology(self) -> Topology: ...

    def language(self) -> str:
        """``en`` or ``de`` (already resolved from the region and Home Assistant)."""
        ...

    def values(self) -> Mapping[str, object]:
        """Object id -> value (enum slug, number, bool); ``None`` / absent = unknown."""
        ...

    def drain_events(self) -> list[SiteEvent]:
        """Events since the last call (and forget them)."""
        ...


@dataclass(frozen=True, slots=True)
class InstallRecord:
    iid: str
    published: tuple[str, ...] = ()
    """Discovery object ids announced last time (to remove devices that vanished)."""


class InstallStore(Protocol):
    async def load(self) -> InstallRecord | None: ...

    async def save(self, record: InstallRecord) -> None: ...


@dataclass
class _Session:
    iid: str
    topics: Topics
    client: MqttClient
    publisher: StatePublisher
    settings: BrokerSettings
    fingerprint: str = ""
    devices: list[HaDevice] = field(default_factory=list)
    published: set[str] = field(default_factory=set)
    birth_task: asyncio.Task[None] | None = None
    commands: CommandHandler | None = None
    allow_mode_raise: bool = False


class HaEntityService:
    def __init__(
        self,
        *,
        source: EntitySource,
        load_config: Callable[[], Awaitable[MqttConfig | None]],
        store: InstallStore,
        services: SupervisorServices | None,
        commands: CommandPort | None = None,
        client_factory: Callable[..., Any] | None = None,
        retry_s: float = RETRY_S,
        poll_s: float = SYNC_POLL_S,
        birth_delay: tuple[float, float] = BIRTH_DELAY_S,
        min_interval_s: float = MIN_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
        rng: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._source = source
        self._load_config = load_config
        self._store = store
        self._services = services
        self._commands = commands
        self._client_factory = client_factory
        self._retry_s = retry_s
        self._poll_s = poll_s
        self._birth_delay = birth_delay
        self._min_interval_s = min_interval_s
        self._clock = clock
        self._rng = rng
        self._status = HaEntitiesStatus(BrokerState.DISABLED, None, 0, "mqtt.reason.not_enabled")
        self._task: asyncio.Task[None] | None = None
        self._reconf = asyncio.Event()
        self._dirty = asyncio.Event()
        self._session: _Session | None = None

    # --- public ----------------------------------------------------------------------

    @property
    def status(self) -> HaEntitiesStatus:
        return self._status

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._supervise(), name="suncore-ha-entities")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def refresh(self) -> None:
        """Values or the topology changed: publish at the next opportunity."""
        self._dirty.set()

    def reconfigure(self) -> None:
        """The ``mqtt`` section changed: look up the broker again and restart the session."""
        self._reconf.set()

    async def republish(self) -> bool:
        """Announce everything again now (the settings page's button); ``False`` = no session."""
        session = self._session
        if session is None or not session.client.connected:
            return False
        await self._announce(session)
        return True

    # --- supervision -----------------------------------------------------------------

    def _set(self, state: BrokerState, source: str | None, reason: str | None, count: int) -> None:
        self._status = HaEntitiesStatus(state, source, count, reason)

    async def _supervise(self) -> None:
        while True:
            self._reconf.clear()
            config = await self._load_config() or MqttConfig()
            lookup = await lookup_broker(config, self._services)
            if lookup.settings is None:
                self._set(lookup.state, lookup.source, lookup.reason_key, 0)
                await self._wait_reconf(self._retry_s)
                continue
            self._set(BrokerState.CONNECTING, lookup.source, None, 0)
            try:
                await self._run_session(config, lookup, lookup.settings)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("mqtt.session_failed")
                self._set(BrokerState.ERROR, lookup.source, "mqtt.reason.unknown", 0)
                await self._wait_reconf(self._retry_s)

    async def _wait_reconf(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._reconf.wait(), seconds)

    async def _run_session(
        self, config: MqttConfig, lookup: BrokerLookup, settings: BrokerSettings
    ) -> None:
        record = await self._store.load()
        if record is None:
            record = InstallRecord(new_install_id())
            await self._store.save(record)
        topics = Topics(record.iid, config.discovery_prefix)
        session = _Session(
            iid=record.iid,
            topics=topics,
            client=None,  # type: ignore[arg-type]  # set below, the callbacks need the session
            publisher=None,  # type: ignore[arg-type]
            settings=settings,
            published=set(record.published),
            allow_mode_raise=config.allow_mode_raise,
        )
        if self._commands is not None:
            session.commands = CommandHandler(
                self._commands, allow_mode_raise=config.allow_mode_raise, clock=self._clock
            )
        factory: dict[str, Any] = (
            {} if self._client_factory is None else {"client_factory": self._client_factory}
        )
        client = MqttClient(
            settings,
            client_id=f"suncore-{record.iid}",
            will=Will(topics.status, PAYLOAD_OFFLINE, qos=1, retain=True),
            subscriptions=[
                (BIRTH_TOPIC, 1),
                *([(topics.command_filter, 1)] * (self._commands is not None)),
            ],
            on_connected=lambda: self._connected(session),
            on_message=lambda topic, payload, retain: self._message(
                session, topic, payload, retain
            ),
            on_link=lambda link, reason: self._link(session, lookup, link, reason),
            **factory,
        )
        session.client = client
        session.publisher = StatePublisher(
            topics, client.publish, clock=self._clock, min_interval_s=self._min_interval_s
        )
        self._session = session
        tasks = [
            asyncio.create_task(client.run(), name="suncore-mqtt-client"),
            asyncio.create_task(self._sync_loop(session), name="suncore-mqtt-sync"),
        ]
        try:
            await self._guard(config, session)
        finally:
            if session.birth_task is not None:
                session.birth_task.cancel()
            await self._goodbye(session)
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            self._session = None

    async def _guard(self, config: MqttConfig, session: _Session) -> None:
        """Return when the settings changed (or the broker moved)."""
        while True:
            await self._wait_reconf(self._retry_s)
            if self._reconf.is_set():
                return
            if self._status.state is not BrokerState.CONNECTED:
                again = await lookup_broker(config, self._services)
                if again.settings != session.settings:
                    return

    async def _goodbye(self, session: _Session) -> None:
        if not session.client.connected:
            return
        current = await self._load_config() or MqttConfig()
        if not current.enabled:
            for object_id in sorted(session.published):
                await session.client.publish(session.topics.config(object_id), "", 1, False)
            session.published.clear()
            await self._save(session)
        await session.client.publish(session.topics.status, PAYLOAD_OFFLINE, 1, True)

    # --- callbacks -------------------------------------------------------------------

    def _link(
        self, session: _Session, lookup: BrokerLookup, link: LinkState, reason: str | None
    ) -> None:
        count = sum(len(d.entities) for d in session.devices)
        if link is LinkState.CONNECTED:
            self._set(BrokerState.CONNECTED, lookup.source, None, count)
        elif link is LinkState.CONNECTING:
            self._set(BrokerState.CONNECTING, lookup.source, None, 0)
        else:
            session.fingerprint = ""  # a new connection announces again
            self._set(BrokerState.ERROR, lookup.source, reason, 0)

    async def _connected(self, session: _Session) -> None:
        await session.client.publish(session.topics.status, PAYLOAD_ONLINE, 1, True)
        await self._announce(session)

    async def _message(
        self, session: _Session, topic: str, payload: bytes, retain: bool = False
    ) -> None:
        object_id = session.topics.object_id_of_command(topic)
        if object_id is not None:
            await self._command(session, topic, object_id, payload, retain)
            return
        if topic != BIRTH_TOPIC or payload.decode("utf-8", "replace").strip() != BIRTH_PAYLOAD:
            return
        if session.birth_task is not None:
            session.birth_task.cancel()
        session.birth_task = asyncio.create_task(self._after_birth(session))

    async def _command(
        self, session: _Session, topic: str, object_id: str, payload: bytes, retain: bool
    ) -> None:
        handler = session.commands
        if handler is None:
            return
        if not retain and not payload:
            return  # the broker's echo of a cleared retained command
        result = await handler.handle(object_id, payload, retain=retain)
        if retain:
            # A retained command would replay at every connect: remove it from the broker.
            await session.client.publish(topic, "", 1, True)
        session.publisher.forget(object_id)
        self.refresh()  # the true state (also after a refusal) goes out at the next sync
        if result.refused and not retain:
            log.info("mqtt.command_refused", entity=object_id, reason=result.reason.key)

    async def _after_birth(self, session: _Session) -> None:
        await asyncio.sleep(self._rng(*self._birth_delay))
        await self._announce(session)

    # --- announcing and states -------------------------------------------------------

    def _topology(self, session: _Session) -> Topology:
        """The source's topology plus what the session knows (command port, opt-in)."""
        return replace(
            self._source.topology(),
            commands=self._commands is not None,
            allow_mode_raise=session.allow_mode_raise,
        )

    def _fingerprint(self, topology: Topology, language: str) -> str:
        return json.dumps(
            [
                language,
                topology.commands,
                topology.allow_mode_raise,
                topology.currency,
                topology.has_battery,
                sorted(topology.supported),
                [(d.id, d.name, d.kind.value) for d in topology.devices],
            ],
            sort_keys=True,
        )

    async def _announce(self, session: _Session) -> None:
        topology = self._topology(session)
        language = self._source.language()
        catalogue = load_catalogue(language)
        render = catalogue.render
        devices = build_devices(session.iid, topology, render)
        session.devices = devices
        if session.commands is not None:
            session.commands.bind(
                (e.spec, e.object_id) for d in devices for e in d.entities if e.spec.command
            )
        session.fingerprint = self._fingerprint(topology, language)
        for device in devices:
            payload = device_payload(session.iid, device, session.topics, topology, render)
            await session.client.publish(
                session.topics.config(device.object_id), encode(payload), 1, False
            )
        current = {d.object_id for d in devices}
        for gone in sorted(session.published - current):
            await session.client.publish(session.topics.config(gone), "", 1, False)
        session.published = current
        await self._save(session)
        session.publisher.reset()
        await self._sync(session, render)
        self._set(
            BrokerState.CONNECTED,
            self._status.source,
            None,
            sum(len(d.entities) for d in devices),
        )

    async def _save(self, session: _Session) -> None:
        try:
            await self._store.save(InstallRecord(session.iid, tuple(sorted(session.published))))
        except Exception:
            log.exception("mqtt.install_record_not_saved")

    async def _sync(self, session: _Session, render: Callable[..., str]) -> None:
        values = dict(self._source.values())
        if session.commands is not None:
            values.update(session.commands.local_values())
        items: list[StateItem] = []
        for device in session.devices:
            for entity in device.entities:
                value = values.get(entity.object_id)
                if value is None:
                    continue
                spec = entity.spec
                number = (
                    float(value)
                    if isinstance(value, int | float) and not isinstance(value, bool)
                    else None
                )
                items.append(
                    StateItem(
                        entity.object_id,
                        render_state(spec, value, render),
                        number,
                        spec.deadband_abs,
                        spec.deadband_rel,
                    )
                )
        await session.publisher.sync(items)
        for event in self._source.drain_events():
            body = {"event_type": event.type, **dict(event.attrs)}
            await session.client.publish(
                session.topics.event, json.dumps(body, ensure_ascii=False), 0, False
            )

    async def _sync_loop(self, session: _Session) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._dirty.wait(), self._poll_s)
            self._dirty.clear()
            if not session.client.connected:
                continue
            try:
                language = self._source.language()
                topology = self._topology(session)
                if self._fingerprint(topology, language) != session.fingerprint:
                    await self._announce(session)
                else:
                    await self._sync(session, load_catalogue(language).render)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("mqtt.sync_failed")
