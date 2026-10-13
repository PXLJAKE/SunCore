# SPDX-License-Identifier: Apache-2.0
"""Registry snapshot and entity catalogue (Phase 2 WP5).

Home Assistant's registries say which integration (``platform``), device, area and
translation key an entity has; the state machine says device class, unit and state class.
Template matching and role detection need both. :class:`EntityCatalogue` joins them into
:class:`~suncore.core.entities.EntityMeta` records without asking Home Assistant per request:

* the registry (three list commands) is loaded on connect and after every reconnect, and
  refreshed - debounced - on the three ``*_registry_updated`` events; those reload only the
  registries;
* the states of *all* entities come from one ``get_states`` the first time someone asks (the
  setup wizard) and are refreshed at most every :data:`MIN_REFRESH_S` seconds, only on
  demand; after a reconnect they count as stale but are not fetched before the next request.
  Only the attributes in :data:`BROWSE_ATTRIBUTES` are kept.
  The alternative - ``subscribe_entities`` without ``entity_ids`` for every entity - was
  rejected: it streams every change of a house with thousands of entities to a process that
  needs the metadata of a handful (Pi budget), while the wizard tolerates 30 s old
  attributes. Values of the *configured* entities come from the live
  :class:`~suncore.ha.cache.EntityCache`; the catalogue is for browsing.

Degradation (risk R1): if the registry commands fail (unknown command, denied by a proxy,
old core), metas are built from states only - Phase 1 behaviour - and
:attr:`EntityCatalogue.registry_available` is false until a later refresh succeeds.

Parsers accept the verified shapes (``docs/research/ha-websocket-api.md``) and tolerate
older ones: ``list_for_display`` entries with short keys, full ``list`` entries with long keys
(``disabled_by`` / ``hidden_by`` there), devices with ``config_entry_id`` or only the
deprecated ``config_entries``, child devices without manufacturer/model.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

import structlog

from suncore.core.entities import EntityMeta
from suncore.ha.cache import EntityCache, utc_now
from suncore.ha.connection import (
    ConnectionState,
    EntityState,
    HAConnection,
    HAError,
    Unsubscribe,
)

log = structlog.get_logger(__name__)

BROWSE_ATTRIBUTES: Final = ("device_class", "unit_of_measurement", "state_class", "friendly_name")
"""Attributes the browse copy keeps: what :func:`build_meta`, the picker candidates and the
unit conversion of :class:`~suncore.ha.cache.EntityCache` read. Everything else (forecast
lists, icons, ...) is dropped to keep the memory of a house with thousands of entities small."""

MIN_REFRESH_S: Final = 30.0
"""At most one ``get_states`` (and one registry retry) per this many seconds."""
DEBOUNCE_S: Final = 2.0
"""Registry events arrive in bursts (a reload creates many entries); refresh once after this."""


# --- parsed registry ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegistryEntity:
    entity_id: str
    platform: str | None = None
    device_id: str | None = None
    area_id: str | None = None
    translation_key: str | None = None
    entity_category: str | None = None
    hidden: bool = False
    disabled: bool = False
    name: str | None = None


@dataclass(frozen=True, slots=True)
class RegistryDevice:
    device_id: str
    name: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    area_id: str | None = None
    config_entry_id: str | None = None
    entry_type: str | None = None


@dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    """Parsed registries; empty when they could not be read."""

    entities: Mapping[str, RegistryEntity] = field(default_factory=dict)
    devices: Mapping[str, RegistryDevice] = field(default_factory=dict)
    areas: Mapping[str, str] = field(default_factory=dict)
    """Area id to area name."""


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _category(raw: Mapping[str, Any], categories: Mapping[str, str]) -> str | None:
    """``ec`` is an index into the delivered category map; the long form is the name."""
    value = raw.get("ec", raw.get("entity_category"))
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return categories.get(str(value))
    if isinstance(value, str):
        return categories.get(value, value) if value.isdigit() else value
    return None


def parse_entity_registry(
    raw: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> dict[str, RegistryEntity]:
    """Parse ``list_for_display`` (``{entity_categories, entities}``) or the full ``list``.

    Unknown or malformed entries are skipped, never fatal.
    """
    categories: Mapping[str, str] = {}
    if isinstance(raw, Mapping):
        entries = raw.get("entities")
        cats = raw.get("entity_categories")
        if isinstance(cats, Mapping):
            categories = {str(k): str(v) for k, v in cats.items()}
        rows: Sequence[object] = entries if isinstance(entries, list) else []
    else:
        rows = raw
    result: dict[str, RegistryEntity] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        entity_id = _str(row.get("ei")) or _str(row.get("entity_id"))
        if entity_id is None:
            continue
        name = _str(row.get("en")) or _str(row.get("name")) or _str(row.get("original_name"))
        result[entity_id] = RegistryEntity(
            entity_id=entity_id,
            platform=_str(row.get("pl")) or _str(row.get("platform")),
            device_id=_str(row.get("di")) or _str(row.get("device_id")),
            area_id=_str(row.get("ai")) or _str(row.get("area_id")),
            translation_key=_str(row.get("tk")) or _str(row.get("translation_key")),
            entity_category=_category(row, categories),
            hidden=bool(row.get("hb")) or row.get("hidden_by") not in (None, ""),
            disabled=row.get("disabled_by") not in (None, ""),
            name=name,
        )
    return result


def parse_device_registry(raw: Sequence[Mapping[str, Any]]) -> dict[str, RegistryDevice]:
    """Parse ``config/device_registry/list`` (also the reduced child-device records)."""
    result: dict[str, RegistryDevice] = {}
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        device_id = _str(row.get("id"))
        if device_id is None:
            continue
        entry = _str(row.get("config_entry_id"))
        if entry is None:
            entries = row.get("config_entries")
            if isinstance(entries, list) and entries and isinstance(entries[0], str):
                entry = entries[0]  # older cores: several config entries per device
        result[device_id] = RegistryDevice(
            device_id=device_id,
            name=_str(row.get("name_by_user")) or _str(row.get("name")),
            manufacturer=_str(row.get("manufacturer")),
            model=_str(row.get("model")),
            area_id=_str(row.get("area_id")),
            config_entry_id=entry,
            entry_type=_str(row.get("entry_type")),
        )
    return result


def parse_area_registry(raw: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        area_id = _str(row.get("area_id"))
        if area_id is not None:
            result[area_id] = _str(row.get("name")) or area_id
    return result


def _attr_str(attributes: Mapping[str, Any], key: str) -> str | None:
    value = attributes.get(key)
    return value if isinstance(value, str) and value else None


def build_meta(
    state: EntityState, registry: RegistryEntity | None, devices: Mapping[str, RegistryDevice]
) -> EntityMeta:
    """One entity's metadata from its state and (optionally) its registry entry."""
    attrs = state.attributes
    device_id = registry.device_id if registry is not None else None
    area_id = registry.area_id if registry is not None else None
    if area_id is None and device_id is not None:
        device = devices.get(device_id)
        area_id = device.area_id if device is not None else None
    return EntityMeta.of(
        state.entity_id,
        platform=registry.platform if registry is not None else None,
        device_class=_attr_str(attrs, "device_class"),
        unit=_attr_str(attrs, "unit_of_measurement"),
        translation_key=registry.translation_key if registry is not None else None,
        state_class=_attr_str(attrs, "state_class"),
        area_id=area_id,
        device_id=device_id,
        entity_category=registry.entity_category if registry is not None else None,
        hidden=registry.hidden if registry is not None else False,
        disabled=registry.disabled if registry is not None else False,
        name=_attr_str(attrs, "friendly_name") or (registry.name if registry is not None else None),
    )


def _slim(state: EntityState) -> EntityState:
    """The state with only the attributes the browse copy needs."""
    kept = {k: v for k in BROWSE_ATTRIBUTES if (v := state.attributes.get(k)) is not None}
    return EntityState(
        entity_id=state.entity_id,
        state=state.state,
        attributes=kept,
        last_changed=state.last_changed,
        last_updated=state.last_updated,
        last_reported=state.last_reported,
    )


# --- the catalogue ----------------------------------------------------------------


class EntityCatalogue:
    """Registry snapshot plus a browse copy of all states; see the module docstring."""

    def __init__(
        self,
        connection: HAConnection,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        wall_clock: Callable[[], datetime] = utc_now,
        debounce_s: float = DEBOUNCE_S,
        min_refresh_s: float = MIN_REFRESH_S,
    ) -> None:
        self._connection = connection
        self._clock = clock
        self._sleep = sleep
        self._debounce_s = debounce_s
        self._min_refresh_s = min_refresh_s
        self.browse = EntityCache(clock=wall_clock)
        self._snapshot = RegistrySnapshot()
        self._registry_ok = False
        self._registry_attempt: float | None = None
        self._states_loaded: float | None = None
        self._states_dirty = False
        self._sleeping = False
        self._rerun = False
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._unsubscribers: list[Unsubscribe] = []
        self._started = False

    # --- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        """Hook the connection: reload on (re)connect, refresh on registry events."""
        if self._started:
            return
        self._started = True
        self._unsubscribers.append(self._connection.add_state_listener(self._on_state))
        try:
            self._unsubscribers.append(
                await self._connection.subscribe_registry_updates(self._on_registry_event)
            )
        except HAError as exc:
            log.warning("ha.registry_events_unavailable", reason=str(exc))
        if self._connection.state is ConnectionState.CONNECTED:
            self._schedule(0.0)

    async def stop(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._started = False

    def _on_state(self, state: ConnectionState) -> None:
        if state is ConnectionState.CONNECTED:
            # The cached states may have missed changes while disconnected.
            self._states_dirty = self._states_loaded is not None
            self._schedule(0.0)

    def _on_registry_event(self, _event_type: str) -> None:
        self._schedule(self._debounce_s)

    def _schedule(self, delay_s: float) -> None:
        """Request a registry reload after ``delay_s``.

        A request during the debounce sleep postpones the pending reload. A request while a
        reload is already running cannot cancel it (it would leave the snapshot half
        loaded); it marks "run again after completion" instead.
        """
        if self._task is not None and not self._task.done():
            if not self._sleeping:
                self._rerun = True
                return
            self._task.cancel()
        self._task = asyncio.get_running_loop().create_task(
            self._delayed_refresh(delay_s), name="suncore-registry-refresh"
        )

    async def _delayed_refresh(self, delay_s: float) -> None:
        while True:
            if delay_s > 0:
                self._sleeping = True
                try:
                    await self._sleep(delay_s)
                finally:
                    self._sleeping = False
            self._rerun = False
            await self.refresh_registry()
            if not self._rerun:
                return
            delay_s = self._debounce_s

    # --- loading --------------------------------------------------------------

    @property
    def registry_available(self) -> bool:
        """Whether the last registry load succeeded (metas then carry registry fields)."""
        return self._registry_ok

    @property
    def snapshot(self) -> RegistrySnapshot:
        return self._snapshot

    @property
    def has_states(self) -> bool:
        return self._states_loaded is not None

    async def refresh(self) -> None:
        """Reload registries and states now. Failures degrade, they never raise."""
        async with self._lock:
            await self._load_registry()
            await self._load_states()

    async def refresh_registry(self) -> None:
        """Reload only the registries (connect, reconnect, registry events)."""
        async with self._lock:
            await self._load_registry()

    async def _load_registry(self) -> None:
        self._registry_attempt = self._clock()
        try:
            display = await self._connection.entity_registry_list_for_display()
            entities = parse_entity_registry(display)
        except (HAError, TimeoutError) as exc:
            if self._registry_ok:
                log.warning("ha.registry_refresh_failed", reason=type(exc).__name__)
            else:
                log.info("ha.registry_unavailable", reason=type(exc).__name__, detail=str(exc))
            self._registry_ok = False
            return
        devices: dict[str, RegistryDevice] = {}
        areas: dict[str, str] = {}
        try:
            devices = parse_device_registry(await self._connection.device_registry_list())
            areas = parse_area_registry(await self._connection.area_registry_list())
        except (HAError, TimeoutError) as exc:
            # Names and device areas are nice to have; entity data alone still ranks.
            log.info("ha.registry_partial", reason=type(exc).__name__, detail=str(exc))
        self._snapshot = RegistrySnapshot(entities, devices, areas)
        self._registry_ok = True

    async def _load_states(self) -> None:
        try:
            states = await self._connection.get_states()
        except (HAError, TimeoutError) as exc:
            log.info("ha.catalogue_states_failed", reason=type(exc).__name__)
            return
        self.browse.ingest_states(_slim(s) for s in states)
        self._states_loaded = self._clock()
        self._states_dirty = False

    async def ensure_fresh(self) -> None:
        """Refresh states (and retry a failed registry) if older than the minimum interval."""
        now = self._clock()
        stale_states = (
            self._states_loaded is None
            or self._states_dirty
            or now - self._states_loaded >= self._min_refresh_s
        )
        retry_registry = not self._registry_ok and (
            self._registry_attempt is None or now - self._registry_attempt >= self._min_refresh_s
        )
        if not stale_states and not retry_registry:
            return
        if self._connection.state is not ConnectionState.CONNECTED:
            return
        async with self._lock:
            now = self._clock()
            if retry_registry and (
                self._registry_attempt is None
                or now - self._registry_attempt >= self._min_refresh_s
            ):
                await self._load_registry()
            if (
                self._states_loaded is None
                or self._states_dirty
                or now - self._states_loaded >= self._min_refresh_s
            ):
                await self._load_states()

    # --- reading --------------------------------------------------------------

    def metas(self) -> list[EntityMeta]:
        """Metadata of every entity with a known state, sorted by entity id."""
        snap = self._snapshot
        result: list[EntityMeta] = []
        for entity_id in sorted(self.browse.ids()):
            cached = self.browse.get(entity_id)
            if cached is None:
                continue
            result.append(build_meta(cached.state, snap.entities.get(entity_id), snap.devices))
        return result

    def device(self, device_id: str | None) -> RegistryDevice | None:
        return None if device_id is None else self._snapshot.devices.get(device_id)

    def area_name(self, area_id: str | None) -> str | None:
        return None if area_id is None else self._snapshot.areas.get(area_id)
