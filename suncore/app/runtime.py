# SPDX-License-Identifier: Apache-2.0
"""The running application: owns the services and implements ``api.Services``."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta, tzinfo
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
from suncore.app.adapters import build_pv_forecast_service
from suncore.app.cadence import device_cadences, merge_cadences
from suncore.app.control import ControlStack
from suncore.app.history import rebuild_history
from suncore.app.modes import ConfigModes
from suncore.app.recorder import FLUSH_INTERVAL_S, MeasurementRecorder
from suncore.app.settings import SUPERVISOR_INGRESS_PEER, Settings
from suncore.core.balance import FlowSnapshot, compute_flows
from suncore.core.config import (
    DevicesConfig,
    ForecastConfig,
    GoalsConfig,
    OperationConfig,
    RegionConfig,
)
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
from suncore.forecast.accuracy import accuracy_view, load_accuracy, update_accuracy
from suncore.forecast.pv.service import PvForecastService
from suncore.forecast.views import ForecastAccuracyView, PvForecastView
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
    LearnedParamStore,
    PlanSnapshotStore,
    PowerModelStateStore,
    SqliteRuntimeStore,
    TimeseriesStore,
)
from suncore.storage.timeseries import META_HISTORY_REBUILT
from suncore.templates import TemplateCatalog

LOOPBACK_PEERS: tuple[str, ...] = ("127.0.0.1", "::1")

log = structlog.get_logger(__name__)

DB_FILENAME = "suncore.db"
PRUNE_INTERVAL_S = 3600.0
"""Decision-log and plan-snapshot retention run hourly."""
REBUILD_BACKOFF_S = (60.0, 300.0, 900.0, 3600.0)
"""Retry delays of the statistics rebuild after a timeout or Home Assistant error."""
MAINTENANCE_WAKE_S = 3600.0
"""The nightly loop wakes at most this often to re-check the time (zone changes, clock jumps)."""
MAINTENANCE_TIME = time(3, 30)
"""Local time of the nightly time-series job (downsample, retention, vacuum if worth it)."""


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
    forecast: ForecastConfig | None = None
    pv_service: PvForecastService | None = None
    """Refreshes the configured PV forecast sources (``None`` = none configured)."""
    timeseries_db: Database | None = None
    """``timeseries.db``; ``None`` = unavailable (too new, unopenable): control is unaffected."""
    timeseries: TimeseriesStore | None = None
    plans: PlanSnapshotStore | None = None
    learned: LearnedParamStore | None = None
    recorder: MeasurementRecorder = field(default_factory=lambda: MeasurementRecorder(None))
    """Batched writer of the controller's measurements (disabled without ``timeseries``)."""
    flush_interval_s: float = FLUSH_INTERVAL_S
    maintenance_time: time = MAINTENANCE_TIME
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
    _tz_task: asyncio.Future[None] | None = None
    _unsubscribers: list[Callable[[], None]] = field(default_factory=list)
    _tempdir: tempfile.TemporaryDirectory[str] | None = None
    _prune_task: asyncio.Task[None] | None = None
    _flush_task: asyncio.Task[None] | None = None
    _maintenance_task: asyncio.Task[None] | None = None
    _history_task: asyncio.Task[None] | None = None
    _pv_task: asyncio.Task[None] | None = None
    _accuracy_task: asyncio.Future[None] | None = None
    _history_done: bool = False
    """The statistics rebuild ran (or was not needed) in this process."""
    _stack_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _started: bool = False
    _ha_timezone: str | None = None
    _tz_learnt: bool = False
    """Home Assistant's time zone was asked for (or a region makes it irrelevant)."""
    _tz_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

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
        self.pv_service = self._new_pv_service()

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
        self._start_pv_task()
        if self.timeseries is not None:
            self._flush_task = asyncio.create_task(self._flush_loop(), name="suncore-ts-flush")
            self._maintenance_task = asyncio.create_task(
                self._maintenance_loop(), name="suncore-ts-maintenance"
            )

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
        forecast = await self.config.get("forecast")
        assert forecast is None or isinstance(forecast, ForecastConfig)
        self.forecast = forecast
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
            on_measurements=self.recorder.record,
            cache=self.cache,
            power_store=self.power_store,
            attach=False,
            after_handover=after_handover,
            predecessor=predecessor,
            defer_recover=not self._time_zone_settled(),
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
            # Only a recovered stack can have taken anything over: before that the store holds
            # crash leftovers, which the new stack's recover releases at once.
            handed = old is not None and old.recovered
            new = self._new_stack(
                after_handover=handed, predecessor=old.gate if old is not None and handed else None
            )
            self.stack = new
            if self._started:
                await new.start()
                new.start_ticking()
        self._notify_control()
        self._start_history_rebuild()  # a site configured after the first connect

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

    async def prune_plans(self) -> int:
        """One retention pass over the plan snapshots; failures are logged, never raised."""
        if self.plans is None:
            return 0
        try:
            return await self.plans.prune(self.clock())
        except Exception:
            log.exception("plan_snapshot.prune_failed")
            return 0

    async def _prune_loop(self) -> None:
        while True:
            await self.prune_decisions()
            await self.prune_plans()
            await self.sleep(self.prune_interval_s)

    # --- time series (timeseries.db) ---------------------------------------------

    async def _flush_loop(self) -> None:
        while True:
            await self.sleep(self.flush_interval_s)
            await self.recorder.flush()  # logs its own errors

    async def maintain_timeseries(self) -> bool:
        """The nightly job once: flush, downsample, retention, vacuum if worth it.

        Failures are logged, never raised; returns whether the job ran through.
        """
        if self.timeseries is None:
            return False
        try:
            await self.recorder.flush()  # the newest samples are aggregated too
            await self.timeseries.maintain(self.clock())
        except Exception:
            log.exception("timeseries.maintenance_failed")
            return False
        self._schedule_forecast_accuracy()
        return True

    def _schedule_forecast_accuracy(self) -> None:
        """Run the accuracy statistics in the background (nightly timing unchanged)."""
        if (
            not self._started
            or self.pv_service is None
            or (self._accuracy_task is not None and not self._accuracy_task.done())
        ):
            return
        self._accuracy_task = asyncio.ensure_future(self.update_forecast_accuracy())

    async def update_forecast_accuracy(self) -> None:
        """The nightly accuracy statistics of the PV forecast sources (never raises)."""
        if self.timeseries is None or self.learned is None or self.pv_service is None:
            return
        try:
            await update_accuracy(
                self.timeseries, self.learned, self.pv_service.source_ids, self.clock()
            )
        except Exception:
            log.exception("forecast.accuracy_failed")

    # --- PV forecast (Phase 4 WP5) ---------------------------------------------------

    def _new_pv_service(self) -> PvForecastService | None:
        return build_pv_forecast_service(
            self.forecast,
            catalog=self.template_catalog,
            connection=self.connection,
            cache=self.cache,
            clock=self.clock,
            sleep=self.control_sleep,
            store=self.timeseries,
        )

    def _start_pv_task(self) -> None:
        if self.pv_service is not None and (self._pv_task is None or self._pv_task.done()):
            self._pv_task = asyncio.create_task(self.pv_service.run(), name="suncore-pv-forecast")

    async def _rebuild_pv_service(self) -> None:
        """Replace the PV forecast service after a ``forecast`` section change."""
        task, self._pv_task = self._pv_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self.pv_service = self._new_pv_service()
        if self._started:
            self._start_pv_task()

    async def pv_forecast(self, hours: int) -> PvForecastView:
        if self.pv_service is None:
            return PvForecastView(generated_at=self.clock(), hours=hours, sources=[], blended=None)
        return self.pv_service.view(hours)

    async def forecast_accuracy(self) -> ForecastAccuracyView:
        ids = [] if self.pv_service is None else self.pv_service.source_ids
        stored = {} if self.learned is None else await load_accuracy(self.learned, ids)
        return accuracy_view(ids, stored)

    def next_maintenance_after(self, moment: datetime) -> datetime:
        """The first ``maintenance_time`` (site time zone, DST safe) strictly after ``moment``."""
        tz = self._timezone()
        local = moment.astimezone(tz)
        target = datetime.combine(local.date(), self.maintenance_time, tzinfo=tz)
        if target.astimezone(UTC) <= moment.astimezone(UTC):
            target = datetime.combine(
                local.date() + timedelta(days=1), self.maintenance_time, tzinfo=tz
            )
        return target.astimezone(UTC)

    def seconds_until_maintenance(self) -> float:
        """Seconds to the next ``maintenance_time`` in the site's time zone."""
        now = self.clock()
        return (self.next_maintenance_after(now) - now.astimezone(UTC)).total_seconds()

    async def _maintenance_loop(self) -> None:
        """Run at start, then at every ``maintenance_time``; wakes at most hourly.

        The target is always "the first 03:30 after the last run" in the *current* time zone, so
        a zone that becomes known later (Home Assistant's, after the first connect) moves the
        target instead of leaving a stale sleep, and one run can never happen twice.
        """
        await self.maintain_timeseries()
        last_run = self.clock()
        while True:
            remaining = (self.next_maintenance_after(last_run) - self.clock()).total_seconds()
            if remaining <= 0:
                await self.maintain_timeseries()
                last_run = self.clock()
                continue
            await self.sleep(min(remaining, MAINTENANCE_WAKE_S))

    def _start_history_rebuild(self) -> None:
        """Rebuild the 15-minute history from HA's statistics, once, in the background."""
        if (
            self._history_done
            or not self._started
            or self.timeseries is None
            or self.site is None
            or self.connection.state is not ConnectionState.CONNECTED
            or (self._history_task is not None and not self._history_task.done())
        ):
            return
        self._history_task = asyncio.create_task(self._rebuild_history(), name="suncore-history")

    async def _rebuild_history(self) -> None:
        """Rebuild until it succeeded once (marker in ``timeseries.db``), with back-off.

        A timeout or Home Assistant error is retried after 1, 5, 15 and then every 60 minutes;
        ``unknown_command`` (the recorder commands are not available, e.g. a proxy that does not
        forward them - UNVERIFIED) is final for this process and retried at the next start.
        """
        attempt = 0
        while True:
            store, site = self.timeseries, self.site
            if store is None or site is None:
                return
            try:
                if await store.get_meta(META_HISTORY_REBUILT) is None:
                    await rebuild_history(self.connection, store, site, self.clock())
                    await store.set_meta(META_HISTORY_REBUILT, self.clock().isoformat())
            except asyncio.CancelledError:
                raise
            except CommandError as exc:
                log.warning("history.rebuild_failed", reason=exc.code, error=str(exc))
                if exc.code == "unknown_command":
                    break
            except (HAError, TimeoutError) as exc:
                log.warning("history.rebuild_failed", reason=type(exc).__name__, error=str(exc))
            except Exception:
                log.exception("history.rebuild_crashed")
                break
            else:
                break
            delay = REBUILD_BACKOFF_S[min(attempt, len(REBUILD_BACKOFF_S) - 1)]
            attempt += 1
            await self.sleep(delay)
        self._history_done = True

    async def _connect(self) -> None:
        try:
            await self.connection.connect()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # AuthError and friends: reported via state, not fatal
            log.error("ha.connect_failed", error_type=type(exc).__name__, error=str(exc))
            return
        await self._prepare_control()
        self._start_history_rebuild()

    def _time_zone_settled(self) -> bool:
        return self.region is not None or self._tz_learnt

    async def _prepare_control(self) -> None:
        """Learn Home Assistant's time zone, then let the stack recover (once per process).

        A time zone learnt *after* the first ``recover()`` rebuilt the stack in the middle of
        it (final review 3-B, N7); the stack therefore waits (``defer_recover``) until the zone
        is known or asking for it failed.
        """
        async with self._tz_lock:
            if not self._tz_learnt:
                try:
                    await self._learn_ha_timezone()
                finally:
                    self._tz_learnt = True
        if self.stack is not None:
            self.stack.allow_recover()

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
        control_tasks = (self._connect_task, self._tz_task)
        storage_tasks = (
            self._prune_task,
            self._flush_task,
            self._maintenance_task,
            self._history_task,
            self._pv_task,
            self._accuracy_task,
        )
        for task in (*control_tasks, *storage_tasks):
            if task is not None:
                task.cancel()
        for task in control_tasks:
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._connect_task = self._prune_task = self._tz_task = None
        self._flush_task = self._maintenance_task = self._history_task = None
        self._pv_task = None
        # Hardware release comes first: a running downsample or retention statement keeps the
        # (shielded) rollback of its cancelled task waiting, and a Pi can need seconds for it.
        # The storage tasks are awaited only after the devices are released.
        async with self._stack_lock:
            if self.stack is not None:
                await self.stack.shutdown()
        for task in storage_tasks:
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        late, self._accuracy_task = self._accuracy_task, None  # maybe scheduled while stopping
        if late is not None:
            late.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await late
        await self.catalogue.stop()
        await self.connection.close()
        await self.recorder.flush()
        if self.timeseries_db is not None:
            await self.timeseries_db.close()
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
        if self.pv_service is not None:
            self.pv_service.on_entities(update.changed)
        if self.stack is not None:
            self.stack.on_update(update)
        self._notify()

    def _on_state(self, state: ConnectionState) -> None:
        if self.stack is not None:
            self.stack.on_state(state)
        if (
            state is ConnectionState.CONNECTED
            and self._started
            and not self._time_zone_settled()
            and self._tz_task is None
        ):
            self._tz_task = asyncio.ensure_future(self._prepare_control())
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
        if self.forecast is not None:
            refs.extend(self.forecast.entity_refs())
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
        elif name == "forecast":
            assert isinstance(stored, ForecastConfig)
            self.forecast = stored
        if name in ("site", "devices", "forecast"):
            await self._resubscribe()
        if (self.site, self.devices, self.region) != before:
            await self._rebuild_stack()
        if name == "forecast":
            await self._rebuild_pv_service()
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
        if name in ("site", "devices", "region", "goals", "operation", "forecast"):
            await self._reload_live_sections(resubscribe=name in ("site", "devices", "forecast"))
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
        forecast_before = self.forecast
        await self._load_live_sections()
        if resubscribe:
            await self._resubscribe()
        if (self.site, self.devices, self.region) != before:
            await self._rebuild_stack()
        elif self.stack is not None and self.goals is not None:
            self.stack.set_goals(self.goals)
        if self.forecast != forecast_before:
            await self._rebuild_pv_service()
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
