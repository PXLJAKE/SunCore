# SPDX-License-Identifier: Apache-2.0
"""The services interface the API depends on.

The API never touches Home Assistant, the safety gate or the database directly; it
talks to this protocol. ``suncore.app.runtime`` implements it for the real process
(and the demo), tests implement it with small fakes.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from pydantic import BaseModel

from suncore.core.balance import FlowSnapshot
from suncore.core.config import OperationConfig
from suncore.core.controlstate import ControlState, DeviceControlState, OverrideCommand
from suncore.core.decisions import Decision, DecisionKind, OperatingMode
from suncore.core.detection import DetectedEntity, EnergySuggestions
from suncore.core.site import SiteConfig
from suncore.core.wizard import CheckItem, PreviewItem
from suncore.storage import HistoryEntry
from suncore.templates import TemplateCatalog

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
class EntityQuery:
    """What ``GET /api/v1/entities`` asks for.

    * nothing: the Phase 1 picker (all power and battery sensors);
    * ``role``: a site role (``grid_power``, ``battery_soc``, ...);
    * ``template_id`` and ``role``: a role of a template; ``device_id`` is the registry device
      of the entity already chosen for the role named by ``same_device_as``.
    """

    role: str | None = None
    template_id: str | None = None
    device_id: str | None = None
    device_name: str | None = None
    """Name of the device of an entity already chosen; ranks same-named candidates first."""
    include_hidden: bool = False
    """Also hidden, diagnostic and config entities (ranked lower); disabled stay excluded."""


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

    async def entities(self, query: EntityQuery) -> Sequence[DetectedEntity]:
        """Ranked entities from the registry snapshot and state cache (no per-request HA call).

        Raises :class:`HAUnavailableError` while nothing is known yet,
        :class:`UnknownRoleError` / :class:`UnknownTemplateError` for bad queries.
        """
        ...

    def templates(self) -> TemplateCatalog:
        """The template catalogue loaded at startup (built-in plus user directory)."""
        ...

    async def section_history(self, name: str) -> list[HistoryEntry]:
        """Replaced bodies of a section, newest first; ``UnknownSectionError`` if not a section."""
        ...

    async def restore_section(self, name: str, entry_id: int) -> BaseModel:
        """Make a history entry current again; ``HistoryEntryNotFoundError`` if it is gone."""
        ...

    async def energy_suggestions(self) -> EnergySuggestions:
        """Energy dashboard suggestions; ``HAUnavailableError`` if Home Assistant is unreachable."""
        ...

    async def system_check(self, site: SiteConfig | None) -> list[CheckItem]:
        """System check of ``site`` (``None`` = the stored site) against the current values."""
        ...

    async def preview(self, fragment: Mapping[str, Any]) -> list[PreviewItem]:
        """Live values of an unsaved fragment; ``PreviewTooLargeError`` if it is too big."""
        ...

    def flows(self) -> FlowSnapshot | None:
        """Current flows, ``None`` while no site is configured."""
        ...

    async def decisions(
        self,
        limit: int,
        *,
        device_id: str | None = None,
        kinds: Collection[DecisionKind] | None = None,
    ) -> list[Decision]:
        """Newest first; ``device_id`` filters by the decision's device, ``kinds`` by kind."""
        ...

    # --- control (Phase 3 WP9) ---------------------------------------------------------

    def control_state(self) -> ControlState:
        """Per-device and site state of the control layer as of the latest tick."""
        ...

    async def emergency_stop(self) -> None:
        """Release every device and refuse all writes until cleared; persisted, idempotent."""
        ...

    async def clear_emergency_stop(self) -> None:
        """Allow control again (modes are unchanged); idempotent."""
        ...

    async def override_device(self, device_id: str, command: OverrideCommand) -> DeviceControlState:
        """Boost / pause / resume one device; raises :class:`UnknownDeviceError`."""
        ...

    def proposals(self) -> list[Decision]:
        """Open assisted-mode proposals, oldest first."""
        ...

    async def confirm_proposal(self, intent_id: str) -> Decision:
        """Confirm a proposal (it is re-submitted as the user's intent) and return the gate's
        decision; raises :class:`UnknownProposalError`."""
        ...

    async def dismiss_proposal(self, intent_id: str) -> None:
        """Drop a proposal; raises :class:`UnknownProposalError`."""
        ...

    async def set_global_mode(self, mode: OperatingMode) -> OperationConfig:
        """Set the global operating mode and apply it (devices that fall to shadow are released)."""
        ...

    async def set_device_mode(
        self, device_id: str, mode: OperatingMode, identity: str | None
    ) -> OperationConfig:
        """Set one device's mode. Raising it above shadow needs the device's current identity
        token (``DeviceControlState.identity``): raises :class:`UnknownDeviceError`,
        :class:`DeviceIdentityChangedError`."""
        ...

    def add_control_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` whenever the control state may have changed (tick, decision, mode)."""
        ...

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


class EnergyPrefsFailedError(RuntimeError):
    """Home Assistant answered ``energy/get_prefs`` with an error (not "not configured")."""


class UnknownRoleError(LookupError):
    """The requested role does not exist (site role or role of the template)."""


class UnknownTemplateError(LookupError):
    """No template with the requested id is loaded."""
