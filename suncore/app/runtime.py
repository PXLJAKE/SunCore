# SPDX-License-Identifier: Apache-2.0
"""The running application: owns the services and implements ``api.Services``."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel

from suncore import __version__
from suncore.api.services import (
    EntityQuery,
    HAUnavailableError,
    HealthReport,
    PeerPolicy,
    UnknownRoleError,
    UnknownTemplateError,
)
from suncore.app.settings import SUPERVISOR_INGRESS_PEER, Settings
from suncore.core.balance import FlowSnapshot, compute_flows
from suncore.core.config import DevicesConfig
from suncore.core.decisions import Decision
from suncore.core.detection import DetectedEntity, EnergySuggestions
from suncore.core.signals import Sample
from suncore.core.site import SiteConfig
from suncore.core.wizard import CheckItem, PreviewItem, collect_refs, preview, run_system_check
from suncore.ha import (
    SITE_ROLES,
    CommandError,
    ConnectionState,
    EntityCache,
    EntityCatalogue,
    EntityUpdate,
    HAConnection,
    HAError,
    describe,
    detect_site_role,
    detect_template_role,
    legacy_entities,
    suggest_from_prefs,
)
from suncore.safety.gate import Clock, SafetyGate
from suncore.storage import ConfigStore, Database, DecisionLog, HistoryEntry
from suncore.templates import TemplateCatalog

LOOPBACK_PEERS: tuple[str, ...] = ("127.0.0.1", "::1")

log = structlog.get_logger(__name__)

DB_FILENAME = "suncore.db"


@dataclass(slots=True)
class Runtime:
    """All long-lived objects of one SunCore process (the composition root's result)."""

    settings: Settings
    db: Database
    config: ConfigStore
    decision_log: DecisionLog
    connection: HAConnection
    cache: EntityCache
    catalogue: EntityCatalogue
    template_catalog: TemplateCatalog
    gate: SafetyGate
    clock: Clock
    demo: bool
    supervised: bool
    site: SiteConfig | None = None
    devices: DevicesConfig | None = None
    _listeners: list[Callable[[], None]] = field(default_factory=list)
    _health_listeners: list[Callable[[], None]] = field(default_factory=list)
    _last_update: datetime | None = None
    _connect_task: asyncio.Task[None] | None = None
    _unsubscribers: list[Callable[[], None]] = field(default_factory=list)
    _tempdir: tempfile.TemporaryDirectory[str] | None = None

    # --- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        """Load the site, wire the connection and start connecting in the background.

        The API must come up even if Home Assistant is unreachable (``/health`` reports
        503), so ``connect()`` runs as a task; its failure is logged, and the state is
        visible through the connection.
        """
        await self._load_live_sections()
        self._unsubscribers.append(self.connection.add_entity_listener(self._on_entities))
        self._unsubscribers.append(self.connection.add_state_listener(self._on_state))
        await self._resubscribe()
        await self.catalogue.start()
        self._connect_task = asyncio.create_task(self._connect(), name="suncore-ha-connect")

    async def _load_live_sections(self) -> None:
        self.site = await self.config.get_site()
        devices = await self.config.get("devices")
        assert devices is None or isinstance(devices, DevicesConfig)
        self.devices = devices

    async def _connect(self) -> None:
        try:
            await self.connection.connect()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # AuthError and friends: reported via state, not fatal
            log.error("ha.connect_failed", error_type=type(exc).__name__, error=str(exc))

    async def stop(self) -> None:
        """Close in reverse order: connection, then database."""
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        task, self._connect_task = self._connect_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.catalogue.stop()
        await self.connection.close()
        await self.db.close()
        if self._tempdir is not None:
            self._tempdir.cleanup()
            self._tempdir = None

    # --- entity feed ----------------------------------------------------------

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                log.exception("change listener failed")

    def _on_entities(self, update: EntityUpdate) -> None:
        self.cache.ingest(update)
        self._last_update = self.clock()
        self._notify()

    def _on_state(self, _state: ConnectionState) -> None:
        self._notify()
        for listener in list(self._health_listeners):
            try:
                listener()
            except Exception:
                log.exception("health listener failed")

    async def _resubscribe(self) -> None:
        refs = list(self.site.entity_refs()) if self.site else []
        if self.devices is not None:
            refs.extend(self.devices.entity_refs())
        ids = list(dict.fromkeys(r.entity_id for r in refs))
        try:
            await self.connection.subscribe_entities(ids)
        except (HAError, TimeoutError) as exc:
            log.warning("ha.subscribe_failed", error=str(exc), reason=type(exc).__name__)

    # --- api.Services -----------------------------------------------------------

    @property
    def version(self) -> str:
        return __version__

    @property
    def mode(self) -> str:
        return self.gate.mode.value

    @property
    def max_import_bytes(self) -> int:
        return self.settings.max_import_bytes

    @property
    def peer_policy(self) -> PeerPolicy:
        # Under Supervisor only the ingress gateway may connect. Without
        # Supervisor (demo, devcontainer, direct mode) the API is loopback-only
        # unless SUNCORE_ALLOW_ANY_PEER=1 is set explicitly (plan WP5).
        if self.settings.allow_any_peer:
            return PeerPolicy(enforce=False)
        if self.supervised:
            return PeerPolicy(enforce=True, allowed_peers=(SUPERVISOR_INGRESS_PEER,))
        return PeerPolicy(enforce=True, allowed_peers=LOOPBACK_PEERS)

    @property
    def static_dir(self) -> Path | None:
        return self.settings.static_dir

    async def health(self) -> HealthReport:
        try:
            db_ok = await self.db.check()
        except Exception:
            db_ok = False
        age = (
            None
            if self._last_update is None
            else (self.clock() - self._last_update).total_seconds()
        )
        return HealthReport(
            ha_state=self.connection.state.value,
            ha_last_update_age_s=age,
            db_ok=db_ok,
            version=__version__,
        )

    async def get_site(self) -> SiteConfig | None:
        return self.site

    async def put_site(self, site: SiteConfig) -> None:
        await self.put_section("site", site)

    async def get_section(self, name: str) -> BaseModel | None:
        """Stored section (``None`` if never stored); raises ``UnknownSectionError``."""
        return await self.config.get(name)

    async def put_section(self, name: str, model: BaseModel) -> BaseModel:
        """Store any config section; ``site`` and ``devices`` also resubscribe entities.

        Returns what was stored. Putting ``devices`` drops the stored non-shadow mode of
        new devices from ``operation`` (they start in shadow).
        Other sections are stored only: ``operation`` is never read by the safety gate.
        """
        stored = await self.config.put(name, model)
        if name == "site":
            assert isinstance(stored, SiteConfig)
            self.site = stored
        elif name == "devices":
            assert isinstance(stored, DevicesConfig)
            self.devices = stored
        if name in ("site", "devices"):
            await self._resubscribe()
        self._notify()
        return stored

    async def entities(self, query: EntityQuery) -> Sequence[DetectedEntity]:
        """Ranked entities from the catalogue (registry snapshot + state cache, ≤ 30 s old)."""
        await self.catalogue.ensure_fresh()
        if not self.catalogue.has_states:
            raise HAUnavailableError("no entity data yet")
        metas = self.catalogue.metas()
        if query.template_id is not None:
            template = self.template_catalog.get(query.template_id)
            if template is None:
                raise UnknownTemplateError(query.template_id)
            roles = getattr(template, "roles", {})
            spec = roles.get(query.role) if query.role is not None else None
            if spec is None:
                raise UnknownRoleError(str(query.role))
            matches = detect_template_role(spec, metas, same_device_id=query.device_id)
            return describe(matches, live=self.cache, catalogue=self.catalogue)
        if query.role is None:
            return legacy_entities(metas, live=self.cache, catalogue=self.catalogue)
        if query.role not in SITE_ROLES:
            raise UnknownRoleError(query.role)
        site_matches = detect_site_role(query.role, metas)
        return describe(site_matches, live=self.cache, catalogue=self.catalogue)

    def templates(self) -> TemplateCatalog:
        return self.template_catalog

    async def section_history(self, name: str) -> list[HistoryEntry]:
        return await self.config.history(name)

    async def restore_section(self, name: str, entry_id: int) -> BaseModel:
        """Restore a history entry; live sections are reloaded like after a normal save."""
        restored = await self.config.restore(name, entry_id)
        if name in ("site", "devices"):
            await self._load_live_sections()
            await self._resubscribe()
        self._notify()
        return restored

    async def energy_suggestions(self) -> EnergySuggestions:
        try:
            prefs = await self.connection.energy_get_prefs()
        except CommandError as exc:
            if exc.code != "unknown_command":  # energy integration not loaded (UNVERIFIED code)
                raise HAUnavailableError(str(exc)) from exc
            prefs = None
        except (HAError, TimeoutError) as exc:
            raise HAUnavailableError(str(exc)) from exc
        await self.catalogue.ensure_fresh()
        known = self.catalogue.browse.ids() if self.catalogue.has_states else None
        return suggest_from_prefs(prefs, known_entity_ids=known)

    def _samples(self, entity_ids: Iterable[str]) -> dict[str, Sample]:
        """Live values where subscribed, otherwise the catalogue's browse copy."""
        ids = list(entity_ids)
        result = self.catalogue.browse.samples(ids)
        result.update(self.cache.samples(ids))
        return result

    async def _browse_if_needed(self, entity_ids: Iterable[str]) -> None:
        if any(eid not in self.cache for eid in entity_ids):
            await self.catalogue.ensure_fresh()

    async def system_check(self, site: SiteConfig | None) -> list[CheckItem]:
        site = site if site is not None else self.site
        ids = [r.entity_id for r in site.entity_refs()] if site is not None else []
        await self._browse_if_needed(ids)
        present = await self.config.stored_sections()
        return run_system_check(site, self._samples(ids), present)

    async def preview(self, fragment: Mapping[str, Any]) -> list[PreviewItem]:
        ids = list(dict.fromkeys(entity_id for _, entity_id, _ in collect_refs(fragment)))
        await self._browse_if_needed(ids)
        return preview(fragment, self._samples(ids))

    def flows(self) -> FlowSnapshot | None:
        if self.site is None:
            return None
        ids = [r.entity_id for r in self.site.entity_refs()]
        return compute_flows(self.site, self.cache.samples(ids))

    async def decisions(self, limit: int) -> list[Decision]:
        return await self.decision_log.latest(limit)

    async def export_config(self) -> dict[str, Any]:
        return await self.config.export_json()

    async def import_config(self, data: object) -> list[str]:
        sections = await self.config.import_json(data)
        await self._load_live_sections()
        await self._resubscribe()
        self._notify()
        return sections

    def add_change_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return remove

    def add_health_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._health_listeners.append(listener)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                self._health_listeners.remove(listener)

        return remove
