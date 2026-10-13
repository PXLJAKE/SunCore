# SPDX-License-Identifier: Apache-2.0
"""The services interface the API depends on.

The API never touches Home Assistant, the safety gate or the database directly; it
talks to this protocol. ``suncore.app.runtime`` implements it for the real process
(and the demo), tests implement it with small fakes.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from pydantic import BaseModel

from suncore.core.balance import FlowSnapshot
from suncore.core.decisions import Decision
from suncore.core.site import SiteConfig

FATAL_HA_STATES: Final = frozenset({"auth_failed", "closed"})
"""Connection states that a restart of the app may fix or that mean it is shutting down."""


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Liveness facts for ``/health`` and the live feed.

    ``/health`` is the Supervisor watchdog's liveness signal, not a readiness probe:
    a Home Assistant core restart (``connecting``/``reconnecting``/``disconnected``) must
    not make the watchdog restart this app, so only a failed database check or a
    terminal connection state (``auth_failed``, ``closed``) turns ``ok`` false (503).
    The connection state itself is always in the body.
    """

    ha_state: str
    """``suncore.ha.ConnectionState`` value (``connected``, ``reconnecting``, ...)."""
    ha_last_update_age_s: float | None
    db_ok: bool
    version: str

    @property
    def ok(self) -> bool:
        return self.db_ok and self.ha_state not in FATAL_HA_STATES


@dataclass(frozen=True, slots=True)
class CandidateInfo:
    """One entity the settings picker may offer."""

    entity_id: str
    name: str
    device_class: str
    unit: str
    state: str
    available: bool


@dataclass(frozen=True, slots=True)
class PeerPolicy:
    """Who may talk to the API (see ``suncore.api.security``)."""

    enforce: bool
    allowed_peers: tuple[str, ...] = ("172.30.32.2",)
    health_local_peers: tuple[str, ...] = ("127.0.0.1", "::1")


class Services(Protocol):
    """Everything the HTTP/WS layer needs."""

    @property
    def version(self) -> str: ...

    @property
    def demo(self) -> bool: ...

    @property
    def mode(self) -> str:
        """Global operating mode value (``shadow`` in Phase 1)."""
        ...

    @property
    def max_import_bytes(self) -> int: ...

    @property
    def peer_policy(self) -> PeerPolicy: ...

    @property
    def static_dir(self) -> Path | None: ...

    async def health(self) -> HealthReport: ...

    async def get_site(self) -> SiteConfig | None: ...

    async def put_site(self, site: SiteConfig) -> None:
        """Store the site configuration and resubscribe the entities."""
        ...

    async def get_section(self, name: str) -> BaseModel | None:
        """Any config section by name; ``None`` if never stored."""
        ...

    async def put_section(self, name: str, model: BaseModel) -> BaseModel:
        """Store a config section, return what was stored (see ``Runtime.put_section``)."""
        ...

    async def candidates(self) -> Sequence[CandidateInfo]:
        """Picker candidates; raises :class:`HAUnavailableError` without a connection."""
        ...

    def flows(self) -> FlowSnapshot | None:
        """Current flows, ``None`` while no site is configured."""
        ...

    async def decisions(self, limit: int) -> list[Decision]: ...

    async def export_config(self) -> dict[str, Any]: ...

    async def import_config(self, data: object) -> list[str]:
        """Validate and replace; raises ``ImportRejectedError`` on bad input."""
        ...

    def add_change_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` whenever entity data or the connection state changes."""
        ...

    def add_health_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` only when the connection state changes (not per entity event)."""
        ...


class HAUnavailableError(RuntimeError):
    """Home Assistant cannot be reached right now."""
