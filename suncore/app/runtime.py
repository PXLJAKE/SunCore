# SPDX-License-Identifier: Apache-2.0
"""The running application: owns the services and implements ``api.Services``."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from pydantic import BaseModel

from suncore import __version__
from suncore.api.services import (
    EnergyPrefsFailedError,
    EntityQuery,
    HAUnavailableError,
    HealthReport,
    PeerPolicy,
    UnknownRoleError,
    UnknownTemplateError,
)
from suncore.app.cadence import device_cadences, merge_cadences
from suncore.app.control import ControlStack
from suncore.app.modes import ConfigModes
from suncore.app.settings import SUPERVISOR_INGRESS_PEER, Settings
from suncore.core.balance import FlowSnapshot, compute_flows
from suncore.core.config import DevicesConfig, GoalsConfig, OperationConfig, RegionConfig
from suncore.core.control import ControlSettings
from suncore.core.controlstate import (
    ControlState,
    DeviceControlState,
    DeviceIdentityChangedError,
    ModeRaiseRejectedError,
    OverrideCommand,
    OverrideUntilPastError,
    UnknownDeviceError,
    UnknownProposalError,
)
from suncore.core.decisions import Decision, DecisionKind, OperatingMode, Origin
from suncore.core.detection import DetectedEntity, EnergySuggestions
from suncore.core.devices import DeviceConfig
from suncore.core.identity import identity_token
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
from suncore.safety.gate import REASON_OVERRIDE_HELD, Clock, SafetyGate
from suncore.storage import (
    ConfigStore,
    Database,
    DecisionLog,
    HistoryEntry,
    PowerModelStateStore,
    SqliteRuntimeStore,
)
from suncore.templates import TemplateCatalog

LOOPBACK_PEERS: tuple[str, ...] = ("127.0.0.1", "::1")

log = structlog.get_logger(__name__)

DB_FILENAME = "suncore.db"
PRUNE_INTERVAL_S = 3600.0
"""Decision-log retention runs hourly."""


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class Runtime:
    """All long-lived objects of one SunCore process (the composition root's result).

    The control layer is one :class:`~suncore.app.control.ControlStack` (``stack``): it is rebuilt
    when the ``devices``, ``site`` or ``region`` section changes (the old one releases its devices
    first - the safe direction) and keeps running on the entity cache this runtime owns.
    """

    settings: Settings
    db: Database
    config: ConfigStore
    decision_log: DecisionLog
    runtime_store: SqliteRuntimeStore
    power_store: PowerModelStateStore
    connection: HAConnection
    cache: EntityCache
    catalogue: EntityCatalogue
    template_catalog: TemplateCatalog
    clock: Clock
    demo: bool
    supervised: bool
    modes: ConfigModes = field(default_factory=ConfigModes)
    site: SiteConfig | None = None
    devices: DevicesConfig | None = None
    region: RegionConfig | None = None
    goals: GoalsConfig | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    prune_interval_s: float = PRUNE_INTERVAL_S
    real_clock: Clock = utc_now
    """Wall clock for liveness (health age); ``clock`` is simulated time in the demo."""
    control_settings: ControlSettings | None = None
    """Controller and safety settings; default: tick from ``Settings.controller_interval_s``."""
    control_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    """The gate's settle wait and the tick period (accelerated in the demo)."""
    extra_cadences: Mapping[str, float] = field(default_factory=dict)
    """Cadences known better than the configuration (the simulator's slow sensors)."""
    stack: ControlStack | None = None
    _listeners: list[Callable[[], None]] = field(default_factory=list)
    _health_listeners: list[Callable[[], None]] = field(default_factory=list)
    _control_listeners: list[Callable[[], None]] = field(default_factory=list)
    _last_update: datetime | None = None
    _connect_task: asyncio.Task[None] | None = None
    _unsubscribers: list[Callable[[], None]] = field(default_factory=list)
    _tempdir: tempfile.TemporaryDirectory[str] | None = None
    _prune_task: asyncio.Task[None] | None = None
    _stack_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _started: bool = False
    _ha_timezone: str | None = None

    @property
    def gate(self) -> SafetyGate:
        """The current safety gate (it changes when the stack is rebuilt)."""
        if self.stack is None:
            msg = "the control stack is not built yet"
            raise RuntimeError(msg)
        return self.stack.gate

    # --- lifecycle ------------------------------------------------------------

    async def prepare(self) -> None:
        """Load the live sections and build the control stack (no listeners, no connection).

        Called by ``build_runtime`` so that the gate exists before :meth:`start`.
        """
        await self._load_live_sections()
        self.stack = self._new_stack()

    async def start(self) -> None:
        """Wire the control stack and the connection and start connecting in the background.

        The API must come up even if Home Assistant is unreachable (``/health`` reports
        503), so ``connect()`` runs as a task; its failure is logged, and the state is
        visible through the connection. ``gate.recover()`` runs after the first successful
        connect, not here: a release needs Home Assistant (``ControlStack.on_state``).
        """
        if self.stack is None:
            await self.prepare()
        self._started = True
        self._unsubscribers.append(self.connection.add_entity_listener(self._on_entities))
        self._unsubscribers.append(self.connection.add_state_listener(self._on_state))
        await self._resubscribe()
        await self.catalogue.start()
        assert self.stack is not None
        await self.stack.start()
        self.stack.start_ticking()
        self._connect_task = asyncio.create_task(self._connect(), name="suncore-ha-connect")
        self._prune_task = asyncio.create_task(self._prune_loop(), name="suncore-prune")

    async def _load_live_sections(self) -> None:
        self.site = await self.config.get_site()
        devices = await self.config.get("devices")
        assert devices is None or isinstance(devices, DevicesConfig)
        self.devices = devices
        region = await self.config.get("region")
        assert region is None or isinstance(region, RegionConfig)
        self.region = region
        goals = await self.config.get("goals")
        assert goals is None or isinstance(goals, GoalsConfig)
        self.goals = goals
        await self._load_modes()

    async def _load_modes(self) -> None:
        operation = await self.config.get("operation")
        assert operation is None or isinstance(operation, OperationConfig)
        self.modes.update(operation)

    def _timezone(self) -> tzinfo:
        """The site time zone: the region's, else Home Assistant's, else UTC (a warning)."""
        name = self.region.timezone if self.region is not None else self._ha_timezone
        if name is not None:
            try:
                return ZoneInfo(name)
            except (ZoneInfoNotFoundError, ValueError, OSError):
                log.warning("control.unknown_timezone", timezone=name)
        else:
            log.warning("control.timezone_unknown_using_utc")
        return UTC

    def _new_stack(
        self, *, after_handover: bool = False, predecessor: SafetyGate | None = None
    ) -> ControlStack:
        devices = list(self.devices.devices) if self.devices is not None else []
        site = self.site
        battery = None if site is None else site.battery
        settings = self.control_settings or ControlSettings(
            tick_s=float(self.settings.controller_interval_s)
        )
        stack = ControlStack(
            self.connection,
            site=site,
            devices=devices,
            clock=self.clock,
            sleep=self.control_sleep,
            settings=settings,
            modes=self.modes,
            store=self.runtime_store,
            tz=self._timezone(),
            goals=self.goals,
            import_limit_w=None if site is None else site.grid.import_limit_w,
            reserve_soc_pct=None if battery is None else battery.min_soc_pct,
            cadences=merge_cadences(device_cadences(devices), self.extra_cadences),
            sink=self._record_decision,
            cache=self.cache,
            power_store=self.power_store,
            attach=False,
            after_handover=after_handover,
            predecessor=predecessor,
        )
        stack.add_listener(self._notify_control)
        return stack

    async def _rebuild_stack(self) -> None:
        """Replace the control stack after a ``devices``/``site``/``region`` change.

        The old stack stops ticking and releases every device it controls (baseline restored)
        before the new one recovers from the persisted runtime; the emergency-stop flag and the
        device runtime survive in the store. A device that stays the same (identity and control
        unchanged) is released without breaking its minimum run/pause time - the new stack
        executes a deferred release - so a rename or an edit elsewhere cannot short-cycle it.
        """
        async with self._stack_lock:
            old, self.stack = self.stack, None
            if old is not None:
                await old.handover(self._unchanged_devices(old.device_configs))
            new = self._new_stack(
                after_handover=old is not None, predecessor=None if old is None else old.gate
            )
            self.stack = new
            if self._started:
                await new.start()
                new.start_ticking()
        self._notify_control()

    def _unchanged_devices(self, previous: Mapping[str, DeviceConfig]) -> frozenset[str]:
        """Ids of the devices that are the same device, controlled the same way, after a change."""
        current = {d.id: d for d in (self.devices.devices if self.devices is not None else [])}
        return frozenset(
            device_id
            for device_id, before in previous.items()
            if (after := current.get(device_id)) is not None
            and identity_token(after) == identity_token(before)
            and after.control == before.control
        )

    async def _record_decision(self, decision: Decision) -> None:
        await self.decision_log.append(decision)

    async def prune_decisions(self) -> int:
        """One retention pass over the decision log; failures are logged, never raised."""
        try:
            return await self.decision_log.prune(self.clock())
        except Exception:
            log.exception("decision_log.prune_failed")
            return 0

    async def _prune_loop(self) -> None:
        while True:
            await self.prune_decisions()
            await self.sleep(self.prune_interval_s)

    async def _connect(self) -> None:
        try:
            await self.connection.connect()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # AuthError and friends: reported via state, not fatal
            log.error("ha.connect_failed", error_type=type(exc).__name__, error=str(exc))
            return
        await self._learn_ha_timezone()

    async def _learn_ha_timezone(self) -> None:
        """Without a region the override resume time is local to Home Assistant's zone."""
        if self.region is not None:
            return
        try:
            config = await self.connection.get_config()
        except (HAError, TimeoutError) as exc:
            log.warning("ha.get_config_failed", error=str(exc))
            return
        name = config.get("time_zone")
        if isinstance(name, str) and name and name != self._ha_timezone:
            self._ha_timezone = name
            if self.region is None and self._started:
                await self._rebuild_stack()

    async def stop(self) -> None:
        """Close in reverse order: release hardware control, connection, then database.

        ``ControlStack.shutdown()`` stops the tick loop, restores the takeover baselines within
        the gate's deadline while the HA connection is still open (fail-safe path, plan WP3) and
        saves the learned power models.
        """
        self._started = False
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        for task in (self._connect_task, self._prune_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._connect_task = self._prune_task = None
        async with self._stack_lock:
            if self.stack is not None:
                await self.stack.shutdown()
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

    def _notify_control(self) -> None:
        for listener in list(self._control_listeners):
            try:
                listener()
            except Exception:
                log.exception("control listener failed")

    def _on_entities(self, update: EntityUpdate) -> None:
        self.cache.ingest(update)  # the cache first: the stack reads it
        self._last_update = self.real_clock()
        if self.stack is not None:
            self.stack.on_update(update)
        self._notify()

    def _on_state(self, state: ConnectionState) -> None:
        if self.stack is not None:
            self.stack.on_state(state)
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
        return self.modes.global_mode().value

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
            else (self.real_clock() - self._last_update).total_seconds()
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
        """Store any config section and apply it to the running control layer.

        Returns what was stored.

        * ``site``, ``devices``: resubscribe the entities; a change rebuilds the control stack
          (the old one releases its devices first). Putting ``devices`` also drops the stored
          non-shadow mode of devices whose identity changed from ``operation`` (they start in
          shadow);
        * ``region`` (time zone) rebuilds the stack, ``goals`` apply at the next tick;
        * ``operation``: the full document may keep or *lower* modes only. Raising a mode goes
          through :meth:`set_global_mode` / :meth:`set_device_mode`, the latter with the device's
          identity token, so a stale document cannot grant a mode to a device that was re-mapped
          meanwhile (``ModeRaiseRejectedError``). Lowered devices are released.
        """
        if name in ("operation", "devices"):
            # Serialised with the mode changes: the raise check and the identity rule of a
            # devices put judge the modes that are stored when they run.
            async with self._operation_lock:
                stored = await self._put_section(name, model)
        else:
            stored = await self._put_section(name, model)
        self._notify()
        return stored

    async def _put_section(self, name: str, model: BaseModel) -> BaseModel:
        if name == "operation":
            assert isinstance(model, OperationConfig)
            self._reject_raise(model)
        before = (self.site, self.devices, self.region)
        stored = await self.config.put(name, model)
        if name == "operation":
            assert isinstance(stored, OperationConfig)
            await self._apply_operation(stored)
        else:
            await self._apply_stored(name, stored, before)
        return stored

    async def _apply_stored(
        self,
        name: str,
        stored: BaseModel,
        before: tuple[SiteConfig | None, DevicesConfig | None, RegionConfig | None],
    ) -> None:
        if name == "site":
            assert isinstance(stored, SiteConfig)
            self.site = stored
        elif name == "devices":
            assert isinstance(stored, DevicesConfig)
            self.devices = stored
            await self._load_modes()  # the identity rule may have dropped modes
        elif name == "region":
            assert isinstance(stored, RegionConfig)
            self.region = stored
        elif name == "goals":
            assert isinstance(stored, GoalsConfig)
            self.goals = stored
            if self.stack is not None:
                self.stack.set_goals(stored)
        if name in ("site", "devices"):
            await self._resubscribe()
        if (self.site, self.devices, self.region) != before:
            await self._rebuild_stack()
        if name == "devices":
            await self._sync_modes()

    # --- operating modes ----------------------------------------------------------

    def _reject_raise(self, new: OperationConfig) -> None:
        current = self.modes.operation
        if new.mode.rank > current.mode.rank:
            raise ModeRaiseRejectedError("global")
        for device_id, mode in new.device_modes.items():
            if mode.rank > current.device_modes.get(device_id, OperatingMode.SHADOW).rank:
                raise ModeRaiseRejectedError(device_id)

    async def _sync_modes(self) -> None:
        if self.stack is not None:
            await self.stack.gate.sync_modes()
        self._notify_control()

    async def _apply_operation(self, operation: OperationConfig) -> None:
        self.modes.update(operation)
        await self._sync_modes()

    async def set_global_mode(self, mode: OperatingMode) -> OperationConfig:
        async with self._operation_lock:
            current = self.modes.operation
            stored = await self.config.put("operation", current.model_copy(update={"mode": mode}))
            assert isinstance(stored, OperationConfig)
            await self._apply_operation(stored)
        self._notify()
        return stored

    async def set_device_mode(
        self, device_id: str, mode: OperatingMode, identity: str | None
    ) -> OperationConfig:
        # The identity is checked under the lock that also serialises ``devices`` puts: a
        # re-mapping cannot slip in between the check and the stored mode.
        async with self._operation_lock:
            device = next(
                (d for d in (self.devices.devices if self.devices else []) if d.id == device_id),
                None,
            )
            if device is None:
                raise UnknownDeviceError(device_id)
            if mode is not OperatingMode.SHADOW and identity != identity_token(device):
                raise DeviceIdentityChangedError(device_id)
            current = self.modes.operation
            modes = dict(current.device_modes)
            if mode is OperatingMode.SHADOW:
                modes.pop(device_id, None)  # missing = shadow
            else:
                modes[device_id] = mode
            stored = await self.config.put(
                "operation", current.model_copy(update={"device_modes": modes})
            )
            assert isinstance(stored, OperationConfig)
            await self._apply_operation(stored)
        self._notify()
        return stored

    # --- control ----------------------------------------------------------------------

    def control_state(self) -> ControlState:
        if self.stack is None:
            return ControlState(
                ts=None,
                active=False,
                emergency_stop=False,
                global_mode=self.modes.global_mode(),
                site=None,
                devices=[],
                proposals=[],
            )
        return self.stack.control_state()

    def add_control_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._control_listeners.append(listener)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                self._control_listeners.remove(listener)

        return remove

    async def emergency_stop(self) -> None:
        if self.stack is not None:
            await self.stack.gate.emergency_stop()
        else:  # being rebuilt: the next stack loads the persisted flag in `recover()`
            await self.runtime_store.save_emergency_stop(True)
        self._notify_control()

    async def clear_emergency_stop(self) -> None:
        if self.stack is not None:
            await self.stack.gate.clear_emergency_stop()
        else:
            await self.runtime_store.save_emergency_stop(False)
        self._notify_control()

    async def override_device(self, device_id: str, command: OverrideCommand) -> DeviceControlState:
        stack = self.stack
        if stack is None or device_id not in stack.device_configs:
            raise UnknownDeviceError(device_id)
        if command.action == "boost":
            assert command.minutes is not None
            until: datetime | None = self.clock() + timedelta(minutes=command.minutes)
            await stack.gate.hold(device_id, until)
        elif command.action == "pause":
            if command.until is not None and command.until <= self.clock():
                raise OverrideUntilPastError(device_id)
            # "Hands off" gives the device back: a unit SunCore switched on must not keep
            # running on SunCore's behalf for the whole pause (no SunCore-caused import). The
            # release respects the minimum run/pause time (deferred, executed by ``poll``).
            runtime = await stack.gate.hold(device_id, command.until)
            if runtime.under_control and not runtime.release_pending:
                await stack.gate.release_device(
                    device_id, REASON_OVERRIDE_HELD, origin=Origin.USER, urgent=False
                )
        else:
            await stack.gate.resume(device_id)
        self._notify_control()
        state = stack.control_state()
        return next(d for d in state.devices if d.device_id == device_id)

    def proposals(self) -> list[Decision]:
        return [] if self.stack is None else list(self.stack.gate.proposals())

    async def confirm_proposal(self, intent_id: str) -> Decision:
        decision = None if self.stack is None else await self.stack.gate.confirm(intent_id)
        if decision is None:
            raise UnknownProposalError(intent_id)
        self._notify_control()
        return decision

    async def dismiss_proposal(self, intent_id: str) -> None:
        if self.stack is None or not self.stack.gate.dismiss(intent_id):
            raise UnknownProposalError(intent_id)
        self._notify_control()

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
            matches = detect_template_role(
                spec,
                metas,
                same_device_id=query.device_id,
                include_hidden=query.include_hidden,
                name_hint=query.device_name,
                device_name_of=self._device_name,
            )
            return describe(matches, live=self.cache, catalogue=self.catalogue)
        if query.role is None:
            return legacy_entities(
                metas,
                live=self.cache,
                catalogue=self.catalogue,
                include_hidden=query.include_hidden,
            )
        if query.role not in SITE_ROLES:
            raise UnknownRoleError(query.role)
        site_matches = detect_site_role(query.role, metas, include_hidden=query.include_hidden)
        return describe(site_matches, live=self.cache, catalogue=self.catalogue)

    def _unit_of_entity(self, entity_id: str) -> tuple[str, str, str] | None:
        """Registry ``(device id, device name, platform)`` of an entity, if all are known."""
        entity = self.catalogue.snapshot.entities.get(entity_id)
        device = self.catalogue.device(entity.device_id if entity is not None else None)
        if entity is None or device is None or device.name is None or entity.platform is None:
            return None
        return device.device_id, device.name, entity.platform

    def _device_name(self, device_id: str | None) -> str | None:
        device = self.catalogue.device(device_id)
        return device.name if device is not None else None

    def templates(self) -> TemplateCatalog:
        return self.template_catalog

    async def section_history(self, name: str) -> list[HistoryEntry]:
        return await self.config.history(name)

    async def restore_section(self, name: str, entry_id: int) -> BaseModel:
        """Restore a history entry; live sections are reloaded like after a normal save."""
        restored = await self.config.restore(name, entry_id)
        if name in ("site", "devices", "region", "goals", "operation"):
            await self._reload_live_sections(resubscribe=name in ("site", "devices"))
        self._notify()
        return restored

    async def energy_suggestions(self) -> EnergySuggestions:
        try:
            prefs = await self.connection.energy_get_prefs()
        except CommandError as exc:
            if exc.code not in ("not_found", "unknown_command"):
                log.warning("ha.energy_prefs_failed", code=exc.code)
                raise EnergyPrefsFailedError(exc.code) from exc
            prefs = None  # dashboard never saved / energy integration not loaded (UNVERIFIED)
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
        return run_system_check(
            site, self._samples(ids), present, self.devices, self._unit_of_entity
        )

    async def preview(self, fragment: Mapping[str, Any]) -> list[PreviewItem]:
        ids = list(dict.fromkeys(entity_id for _, entity_id, _ in collect_refs(fragment)))
        await self._browse_if_needed(ids)
        return preview(fragment, self._samples(ids))

    def flows(self) -> FlowSnapshot | None:
        if self.site is None:
            return None
        ids = [r.entity_id for r in self.site.entity_refs()]
        return compute_flows(self.site, self.cache.samples(ids))

    async def decisions(
        self,
        limit: int,
        *,
        device_id: str | None = None,
        kinds: Collection[DecisionKind] | None = None,
    ) -> list[Decision]:
        return await self.decision_log.latest(limit, device_id=device_id, kinds=kinds)

    async def export_config(self) -> dict[str, Any]:
        return await self.config.export_json()

    async def import_config(self, data: object) -> list[str]:
        sections = await self.config.import_json(data)
        await self._reload_live_sections(resubscribe=True)
        self._notify()
        return sections

    async def _reload_live_sections(self, *, resubscribe: bool) -> None:
        """After a restore or import: re-read what the control layer runs on and apply it."""
        before = (self.site, self.devices, self.region)
        await self._load_live_sections()
        if resubscribe:
            await self._resubscribe()
        if (self.site, self.devices, self.region) != before:
            await self._rebuild_stack()
        elif self.stack is not None and self.goals is not None:
            self.stack.set_goals(self.goals)
        await self._sync_modes()

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
