# SPDX-License-Identifier: Apache-2.0
"""The running application: owns the services and implements ``api.Services``."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from suncore import __version__
from suncore.api.services import CandidateInfo, HAUnavailableError, HealthReport, PeerPolicy
from suncore.app.settings import SUPERVISOR_INGRESS_PEER, Settings
from suncore.core.balance import FlowSnapshot, compute_flows
from suncore.core.decisions import Decision
from suncore.core.site import SiteConfig
from suncore.ha import (
    ConnectionState,
    EntityCache,
    EntityUpdate,
    HAConnection,
    HAError,
    entity_candidates,
)
from suncore.safety.gate import Clock, SafetyGate
from suncore.storage import ConfigStore, Database, DecisionLog

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
    gate: SafetyGate
    clock: Clock
    demo: bool
    supervised: bool
    site: SiteConfig | None = None
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
        self.site = await self.config.get_site()
        self._unsubscribers.append(self.connection.add_entity_listener(self._on_entities))
        self._unsubscribers.append(self.connection.add_state_listener(self._on_state))
        await self._resubscribe()
        self._connect_task = asyncio.create_task(self._connect(), name="suncore-ha-connect")

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
        ids = [r.entity_id for r in self.site.entity_refs()] if self.site else []
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
        await self.config.put_site(site)
        self.site = site
        await self._resubscribe()
        self._notify()

    async def candidates(self) -> Sequence[CandidateInfo]:
        try:
            states = await self.connection.get_states()
        except HAError as exc:
            raise HAUnavailableError(str(exc)) from exc
        return [
            CandidateInfo(
                entity_id=c.entity_id,
                name=c.name,
                device_class=c.device_class,
                unit=c.unit.value,
                state=c.state,
                available=c.available,
            )
            for c in entity_candidates(states)
        ]

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
        self.site = await self.config.get_site()
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
