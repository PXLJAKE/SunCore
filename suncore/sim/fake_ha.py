# SPDX-License-Identifier: Apache-2.0
"""A fake Home Assistant connection fed by the simulator (BRIEF 4.14, ADR-0010).

:class:`FakeHAConnection` satisfies :class:`suncore.ha.connection.HAConnection` and, since
Phase 3 WP8, :class:`~suncore.ha.connection.ServiceCallerProvider`: :meth:`service_caller`
hands out a real :class:`~suncore.ha.connection.ServiceCaller` whose commander executes the
verified write services (``switch``/``input_boolean`` turn_on/turn_off/toggle, ``climate``
set_temperature/set_hvac_mode/turn_on/turn_off, ``number.set_value``, ``select.select_option``,
``water_heater.*``) against the simulated devices (:mod:`suncore.sim.devices`) with Home
Assistant's validation and error shapes (research/ha-websocket-api.md "Write services and
contexts"): schema errors are ``invalid_format``, entity-level validation
``service_validation_error``, unknown services ``not_found``, a non-existing or unavailable
entity is skipped *silently* (success without effect). A result carries the call's context id;
the state changes the call causes carry that context (``user_id`` = the Supervisor) when the
push comes within 5 s of the call and a fresh id-only context otherwise (cloud integrations).

The virtual house (``sensor.sim_*``) advances one scenario step per ``step_s / speed`` seconds
of *injected* sleep (``autorun``), or by calling :meth:`advance` (the scenario runner), so
tests never wait and the demo can run accelerated. The devices' draw is part of the house
balance (``grid = load + devices + battery - pv``).

Behaviour per step:

* every site sensor that is not frozen is published each step (like an integration that
  reports regularly; the cache then never mistakes a constant 0 W for a dead sensor); the
  device entities are published when they change (like Home Assistant);
* fault ``unavailable``: the entity's state is ``"unavailable"`` while the window lasts (a
  service call for it succeeds without effect);
* fault ``stale``: the entity is neither updated nor emitted, its last published value
  stays visible (also through :meth:`get_states`);
* fault ``frozen``: the entity keeps reporting the value it had when the fault began (an
  inverter that hangs);
* fault ``disconnect``: the connection goes ``RECONNECTING``, nothing is emitted and
  reads and calls fail; when the window ends it is ``CONNECTED`` again and a full snapshot of
  the subscribed entities is delivered.

Delayed events (the cloud push after a command, a user's remote control) sit in a small
event queue keyed by simulated time (:meth:`run_events_until`, :meth:`next_event_time`); the
scenario runner drives it event by event, the background feed delivers due events at every
step. The scenario restarts from its first step when it ends and ``loop_scenario`` is true;
otherwise the feed stops and the last values stay.
"""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import itertools
import logging
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import partial
from typing import Any, Final

from suncore.core.actions import is_read_only_action
from suncore.core.entities import EntityMeta
from suncore.core.site import BatteryConfig, EntityRef, GridConfig, PvArray, SiteConfig
from suncore.ha.connection import (
    REGISTRY_EVENT_TYPES,
    ActionNotAllowedError,
    ActionRateLimitedError,
    ActionRateLimiter,
    CommandError,
    ConnectionState,
    EntityListener,
    EntityState,
    EntityUpdate,
    HAError,
    NotConnectedError,
    RateLimiter,
    RegistryListener,
    ServiceCaller,
    StateContext,
    StateListener,
    Unsubscribe,
    action_rate_key,
)
from suncore.sim import registry
from suncore.sim.demo_devices import (
    FORECAST_TODAY_ID,
    FORECAST_TOMORROW_ID,
    SIM_DEVICE_ENTITY_IDS,
    WEATHER_ID,
    WORKDAY_ID,
    DemoDevices,
    sim_price,
)
from suncore.sim.devices import (
    CONTEXT_RECENT_S,
    Effect,
    ServiceError,
    SimDevice,
    StateOut,
    validate_schema,
)
from suncore.sim.house import House, HouseState
from suncore.sim.recorder import SimRecorder
from suncore.sim.scenario import ManualEvent, Scenario, SimSignal

_log = logging.getLogger(__name__)

GRID_ID: Final = "sensor.sim_grid_power"
PV_ID: Final = "sensor.sim_pv_power"
BATTERY_ID: Final = "sensor.sim_battery_power"
SOC_ID: Final = "sensor.sim_battery_soc"
HOUSE_ID: Final = "sensor.sim_house_power"

SIM_ENTITY_IDS: Final[tuple[str, ...]] = (GRID_ID, PV_ID, BATTERY_ID, SOC_ID, HOUSE_ID)

ALL_SIM_ENTITY_IDS: Final[tuple[str, ...]] = (*SIM_ENTITY_IDS, *SIM_DEVICE_ENTITY_IDS)
"""The site sensors plus the demo devices, price, forecast and energy counters."""

_SIGNAL_ENTITY: Final[Mapping[SimSignal, str]] = {
    "grid_power": GRID_ID,
    "pv_power": PV_ID,
    "battery_power": BATTERY_ID,
    "battery_soc": SOC_ID,
    "load_power": HOUSE_ID,
}

# entity id -> (unit, device_class, friendly_name)
_META: Final[Mapping[str, tuple[str, str, str]]] = {
    GRID_ID: ("W", "power", "Sim grid power"),
    PV_ID: ("W", "power", "Sim PV power"),
    BATTERY_ID: ("W", "power", "Sim battery power"),
    SOC_ID: ("%", "battery", "Sim battery SoC"),
    HOUSE_ID: ("W", "power", "Sim house power"),
}


SUPERVISOR_USER_ID: Final = "sim-supervisor"
"""``user_id`` of the context of a call made through the Supervisor proxy."""
USER_ID: Final = "sim-user"
"""``user_id`` of a change a person makes in the Home Assistant UI."""
TARGET_KEYS: Final = ("entity_id", "device_id", "area_id", "floor_id", "label_id")


@dataclass(frozen=True, slots=True)
class CallRecord:
    """One ``call_service`` as the fake saw it (evidence for tests)."""

    ts: datetime
    domain: str
    service: str
    entity_ids: tuple[str, ...]
    data: Mapping[str, Any]
    context_id: str | None
    reached: tuple[str, ...] = ()
    """Entities the call acted on (unavailable and unknown entities are skipped silently)."""
    error: str | None = None
    """The error code when the call failed (``not_connected`` while disconnected)."""


def demo_site_config() -> SiteConfig:
    """Site configuration mapping the simulator entities (signed grid, one PV, battery)."""
    return SiteConfig(
        grid=GridConfig(power=EntityRef(entity_id=GRID_ID)),
        # The simulator's plant: 8 kWp following sin(elevation), i.e. a horizontal plane
        # (``PVParams`` defaults; the clear-sky model of the forecast calibration reads this).
        pv=[PvArray(name="PV", power=EntityRef(entity_id=PV_ID), kwp=8.0, tilt_deg=0.0)],
        battery=BatteryConfig(
            power=EntityRef(entity_id=BATTERY_ID), soc=EntityRef(entity_id=SOC_ID)
        ),
        house=EntityRef(entity_id=HOUSE_ID),
    )


def _values(state: HouseState) -> dict[str, float]:
    return {
        GRID_ID: state.grid_w,
        PV_ID: state.pv_w,
        BATTERY_ID: state.battery_w,
        SOC_ID: state.soc_pct,
        HOUSE_ID: state.load_w,
    }


def _format(value: float) -> str:
    return f"{value + 0.0:.1f}" if abs(value) >= 0.05 else "0.0"


def _solcast_query_window(slots: list[Any], data: Mapping[str, Any] | None) -> list[Any]:
    """The slots of ``query_forecast_data`` between ``start_date_time`` and ``end_date_time``.

    Both default to *now* in the real action (an empty range); the fake keeps every slot when a
    bound is absent so older tests that call it without a range still get the day.
    """
    if not data:
        return slots
    out = slots
    start, end = data.get("start_date_time"), data.get("end_date_time")
    if isinstance(start, str):
        lo = datetime.fromisoformat(start)
        out = [s for s in out if datetime.fromisoformat(s["period_start"]) >= lo]
    if isinstance(end, str):
        hi = datetime.fromisoformat(end)
        out = [s for s in out if datetime.fromisoformat(s["period_start"]) < hi]
    return out


class FakeHAConnection:
    """Simulator-backed :class:`HAConnection`.

    Args:
        scenario: the scenario to play (house, step, faults).
        clock: injected wall clock; used for entity timestamps only.
        sleep: injected sleep; called with ``step_s / speed`` between steps.
        speed: acceleration factor (> 0); 1.0 plays in real time.
        loop_scenario: restart the scenario when it ends.
        start_offset_s: fast-forward this far into the scenario on connect.
        energy_prefs_configured: whether the fake Energy dashboard has preferences
            (``False`` = ``energy/get_prefs`` would answer ``not_found``).
        sim_clock: source of the *simulated* time for service calls and events (the scenario
            runner's virtual clock); default: the time of the latest house step.
        autorun: run the background feed; ``False`` = the owner calls :meth:`advance`.
        write_limiter: the flood floor of the :class:`ServiceCaller` (default 0.25 s, real time;
            the runner injects a virtual-time one).

    The registry and Energy dashboard accessors mirror the WebSocket commands of the same name
    (all raise :class:`NotConnectedError` while not connected, like the real commands would;
    set :attr:`registry_error` to make the registry commands fail, e.g. to test degradation):

    * :meth:`entity_registry_list_for_display` - ``config/entity_registry/list_for_display``
    * :meth:`entity_registry_list` - ``config/entity_registry/list``
    * :meth:`device_registry_list` - ``config/device_registry/list``
    * :meth:`area_registry_list` - ``config/area_registry/list``
    * :meth:`energy_get_prefs` - ``energy/get_prefs`` (``None`` = not configured)
    * :meth:`subscribe_registry_updates` / :meth:`fire_registry_event` - the three registry
      update events
    * :meth:`call_action_response` - read-only actions (``tibber.get_prices``, ...)
    * :meth:`entity_metas` - registry joined with the current states as ``EntityMeta``
    """

    def __init__(
        self,
        scenario: Scenario,
        *,
        clock: Callable[[], datetime],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        speed: float = 1.0,
        loop_scenario: bool = True,
        start_offset_s: float = 0.0,
        energy_prefs_configured: bool = True,
        sim_clock: Callable[[], datetime] | None = None,
        autorun: bool = True,
        write_limiter: RateLimiter | None = None,
    ) -> None:
        if speed <= 0:
            msg = "speed must be positive"
            raise ValueError(msg)
        if not 0 <= start_offset_s < scenario.duration_s:
            msg = "start_offset_s must lie within the scenario"
            raise ValueError(msg)
        self._scenario = scenario
        self._clock = clock
        self._sleep = sleep
        self._speed = speed
        self._loop = loop_scenario
        self._start_steps = int(start_offset_s // scenario.step_s)
        self._state = ConnectionState.DISCONNECTED
        self._state_listeners: list[StateListener] = []
        self._entity_listeners: list[EntityListener] = []
        self._subscribed: frozenset[str] = frozenset()
        self._published: dict[str, EntityState] = {}
        self._house: House | None = None
        self._step_index = 0
        self._task: asyncio.Task[None] | None = None
        self._house_state: HouseState | None = None
        self._demo: DemoDevices | None = None
        self._energy_prefs_configured = energy_prefs_configured
        self.registry_error: HAError | None = None
        self._registry_listeners: list[RegistryListener] = []
        self._sim_clock = sim_clock
        self._autorun = autorun
        self._write_limiter = write_limiter if write_limiter is not None else RateLimiter()
        self._reported: dict[str, datetime] = {}
        self._heartbeats: dict[str, float] = {}
        self._events: list[tuple[datetime, int, Callable[[], None]]] = []
        self._seq = itertools.count()
        self._context_seq = itertools.count(1)
        self._event_time: datetime | None = None
        self._offset_s = 0
        self._unavailable: frozenset[str] = frozenset()
        self._action_limiter = ActionRateLimiter(
            clock=lambda: self._clock().timestamp(), sleep=sleep
        )
        self.call_log: list[CallRecord] = []
        """Every service call seen, oldest first."""
        self.recorder = SimRecorder(
            scenario,
            {
                GRID_ID: lambda s: s.grid_w,
                PV_ID: lambda s: s.pv_w,
                BATTERY_ID: lambda s: s.battery_w,
                SOC_ID: lambda s: s.soc_pct,
                HOUSE_ID: lambda s: s.load_w,
            },
        )
        """Hourly statistics behind :meth:`statistics_during_period`."""
        self.statistics_error: HAError | None = None
        """Make ``statistics_during_period`` fail (e.g. a proxy that blocks ``recorder/*``)."""

    # --- HAConnection ---------------------------------------------------------

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def sim_time(self) -> datetime | None:
        """Simulated time of the latest published step (``None`` before connect)."""
        return None if self._house_state is None else self._house_state.time

    def add_state_listener(self, listener: StateListener) -> Unsubscribe:
        return self._add(self._state_listeners, listener)

    def add_entity_listener(self, listener: EntityListener) -> Unsubscribe:
        return self._add(self._entity_listeners, listener)

    async def connect(self) -> None:
        if self._state is ConnectionState.CONNECTED:
            return
        if self._state is ConnectionState.CLOSED:
            msg = "connection is closed"
            raise NotConnectedError(msg)
        self._set_state(ConnectionState.CONNECTING)
        self._house = self._scenario.build_house()
        self._step_index = 0
        self._published = {}
        self._events = []
        self._demo = DemoDevices(self._scenario)
        self._heartbeats = self._demo.devices.heartbeats()
        for event in self._scenario.manual:
            self._at(
                self._scenario.start + timedelta(seconds=event.at_s), partial(self._manual, event)
            )
        # Fast-forward silently so the house state (e.g. battery SoC) evolves
        # as if the scenario had run since its start.
        for _ in range(self._start_steps):
            self._step_house()
            self._step_index += 1
        self._apply_step(emit=False)
        if self._subscribed:
            # Like the WebSocket client: the snapshot of the subscription arrives before the
            # connection counts as connected (the control stack recovers on that state).
            self._emit_snapshot()
        self._set_state(ConnectionState.CONNECTED)
        if self._autorun:
            self._task = asyncio.get_running_loop().create_task(self._run(), name="fake-ha-feed")

    async def close(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._state is not ConnectionState.CLOSED:
            self._set_state(ConnectionState.CLOSED)

    async def subscribe_entities(self, entity_ids: Collection[str]) -> None:
        self._subscribed = frozenset(entity_ids)
        if not self._subscribed or self._state is not ConnectionState.CONNECTED:
            return  # delivered as a full snapshot once the connection is back
        self._emit_snapshot()

    async def get_states(self) -> list[EntityState]:
        self._require_connected()
        return list(self._published.values())

    async def get_config(self) -> Mapping[str, Any]:
        self._require_connected()
        tz_key = getattr(self._scenario.start.tzinfo, "key", None)
        return {
            "time_zone": tz_key if isinstance(tz_key, str) else "UTC",
            "unit_system": {
                "length": "km",
                "mass": "g",
                "temperature": "°C",
                "volume": "L",
                "wind_speed": "m/s",
                "accumulated_precipitation": "mm",
                "pressure": "Pa",
                "area": "m²",
            },
            "currency": "EUR",
            "version": "sim",
            "location_name": "SunCore demo",
            "latitude": self._scenario.house.pv.latitude,
            "longitude": self._scenario.house.pv.longitude,
        }

    # --- registries and Energy dashboard (WP5 wires these to the WS commands) ------

    async def entity_registry_list_for_display(self) -> dict[str, Any]:
        """Payload of ``config/entity_registry/list_for_display``."""
        self._require_registry()
        return registry.entity_registry_list_for_display()

    async def entity_registry_list(self) -> list[dict[str, Any]]:
        """Payload of ``config/entity_registry/list``."""
        self._require_connected()
        return registry.entity_registry_list()

    async def device_registry_list(self) -> list[dict[str, Any]]:
        """Payload of ``config/device_registry/list``."""
        self._require_registry()
        return registry.device_registry_list()

    async def area_registry_list(self) -> list[dict[str, Any]]:
        """Payload of ``config/area_registry/list``."""
        self._require_registry()
        return registry.area_registry_list()

    async def energy_get_prefs(self) -> dict[str, Any] | None:
        """Payload of ``energy/get_prefs``; ``None`` where HA answers ``not_found`` ("No prefs")."""
        self._require_connected()
        if not self._energy_prefs_configured:
            return None
        return registry.energy_prefs(self._scenario.house.battery.capacity_wh / 1000)

    async def subscribe_registry_updates(self, listener: RegistryListener) -> Unsubscribe:
        """Register for registry events; :meth:`fire_registry_event` delivers them."""
        return self._add(self._registry_listeners, listener)

    def fire_registry_event(self, event_type: str = REGISTRY_EVENT_TYPES[0]) -> None:
        """Deliver a registry update event to the listeners (tests and demo hooks)."""
        for listener in list(self._registry_listeners):
            try:
                listener(event_type)
            except Exception:
                _log.exception("registry listener failed")

    async def energy_solar_forecast(self) -> Mapping[str, Mapping[str, float]]:
        """``energy/solar_forecast``: the entries linked in the demo's Energy dashboard.

        ``sim_entry_forecast_solar`` (hourly Wh, a zero at midnight dropped, like Forecast.Solar)
        and ``sim_entry_solcast_solar`` (half-hourly Wh of the P50). ``{}`` while the dashboard is
        not configured (:meth:`energy_get_prefs` returns ``None``).
        """
        self._require_connected()
        if self.registry_error is not None:
            raise self.registry_error
        if not self._energy_prefs_configured or self._demo is None or self._house_state is None:
            return {}
        return self._demo.solar_forecast(self._house_state.time)

    async def statistics_during_period(
        self,
        statistic_ids: Sequence[str],
        start: datetime,
        end: datetime | None = None,
        *,
        period: str = "hour",
        types: Sequence[str] | None = None,
        units: Mapping[str, str] | None = None,  # noqa: ARG002 - the fake always answers in W
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        """``recorder/statistics_during_period`` from :attr:`recorder` (hourly means in W)."""
        self._require_connected()
        if self.statistics_error is not None:
            raise self.statistics_error
        if not statistic_ids:
            return {}
        return self.recorder.query(statistic_ids, start, end, period, types)

    async def call_action_response(
        self,
        domain: str,
        service: str,
        data: Mapping[str, Any] | None = None,
        *,
        target: Mapping[str, Any] | None = None,
        min_interval_s: float | None = None,
        max_wait_s: float = 0.0,
        rate_key: str | None = None,
    ) -> dict[str, Any]:
        """Read-only action responses built from the demo data (allowlist like the real one)."""
        if not is_read_only_action(domain, service):
            msg = f"{domain}.{service} is not a read-only action"
            raise ActionNotAllowedError(msg)
        self._require_connected()
        key = action_rate_key(domain, service, rate_key)
        if not await self._action_limiter.acquire(key, min_interval_s, max_wait_s=max_wait_s):
            raise ActionRateLimitedError(key, self._action_limiter.retry_after(key))
        now = self._house_state.time if self._house_state is not None else self._clock()
        if (domain, service) == ("tibber", "get_prices"):
            start = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
            slots = [start + timedelta(minutes=15 * i) for i in range(96)]
            prices = [{"start_time": t.isoformat(), "price": sim_price(t)} for t in slots]
            return {"prices": {"Home": prices}}
        if (domain, service) == ("solcast_solar", "query_forecast_data"):
            slots_data: list[Any] = []
            for entity_id in (FORECAST_TODAY_ID, FORECAST_TOMORROW_ID):
                day = self._published.get(entity_id)
                if day is not None:
                    slots_data.extend(day.attributes.get("detailedForecast", []))
            return {"data": _solcast_query_window(slots_data, data)}
        entity_id = str((target or {}).get("entity_id", ""))
        demo = self.demo
        if (domain, service) == ("weather", "get_forecasts") and entity_id == WEATHER_ID:
            return {entity_id: {"forecast": demo.hourly_weather(now)}}
        if (domain, service) == ("workday", "check_date") and entity_id == WORKDAY_ID:
            asked = date.fromisoformat(str((data or {}).get("check_date")))
            return {entity_id: {"workday": demo.is_workday(asked)}}
        raise CommandError("not_found", f"Service {domain}.{service} not found.")

    def entity_metas(self) -> list[EntityMeta]:
        """Registry entries joined with the published states (what detection ranks)."""
        self._require_connected()
        metas: list[EntityMeta] = []
        for entry in registry.ENTITIES:
            state = self._published.get(entry.entity_id)
            attrs = {} if state is None else state.attributes
            unit = attrs.get("unit_of_measurement")
            device_class = attrs.get("device_class")
            state_class = attrs.get("state_class")
            metas.append(
                EntityMeta.of(
                    entry.entity_id,
                    platform=entry.platform,
                    device_class=device_class if isinstance(device_class, str) else None,
                    unit=unit if isinstance(unit, str) else None,
                    translation_key=entry.translation_key,
                    state_class=state_class if isinstance(state_class, str) else None,
                    area_id=entry.area_id,
                    device_id=entry.device_id,
                )
            )
        return metas

    # --- internals ------------------------------------------------------------

    @staticmethod
    def _add[T](listeners: list[T], listener: T) -> Unsubscribe:
        listeners.append(listener)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                listeners.remove(listener)

        return remove

    def _require_registry(self) -> None:
        self._require_connected()
        if self.registry_error is not None:
            raise self.registry_error

    def _require_connected(self) -> None:
        if self._state is not ConnectionState.CONNECTED:
            msg = f"fake HA is {self._state.value}"
            raise NotConnectedError(msg)

    def _set_state(self, state: ConnectionState) -> None:
        if state is self._state:
            return
        self._state = state
        for listener in list(self._state_listeners):
            try:
                listener(state)
            except Exception:
                _log.exception("state listener failed")

    def _notify(self, update: EntityUpdate) -> None:
        for listener in list(self._entity_listeners):
            try:
                listener(update)
            except Exception:
                _log.exception("entity listener failed")

    def _emit_snapshot(self) -> None:
        changed = {e: s for e, s in self._published.items() if e in self._subscribed}
        self._notify(EntityUpdate(changed=changed, full=True))

    async def _run(self) -> None:
        delay = self._scenario.step_s / self._speed
        while True:
            await self._sleep(delay)
            if not self.advance():
                return

    # --- the simulated clock and the event queue -----------------------------------------

    @property
    def scenario(self) -> Scenario:
        return self._scenario

    @property
    def demo(self) -> DemoDevices:
        """The simulated devices and demo entities (after :meth:`connect`)."""
        assert self._demo is not None, "connect() first"
        return self._demo

    def _sim_now(self) -> datetime:
        if self._sim_clock is not None:
            return self._sim_clock()
        if self._event_time is not None:
            return self._event_time
        if self._house_state is not None:
            return self._house_state.time
        return self._clock()

    def _at(self, when: datetime, action: Callable[[], None]) -> None:
        heapq.heappush(self._events, (when, next(self._seq), action))

    def next_event_time(self) -> datetime | None:
        """Simulated time of the next queued event (a push, a manual change) or ``None``."""
        return self._events[0][0] if self._events else None

    def run_events_until(self, when: datetime) -> None:
        """Run every queued event due at or before ``when``, in time order."""
        while self._events and self._events[0][0] <= when:
            at, _, action = heapq.heappop(self._events)
            self._event_time = at
            try:
                action()
            finally:
                self._event_time = None

    def _outdoor_c(self, when: datetime) -> float:
        return self._scenario.outdoor.at(when)

    def _advance_device(self, device: SimDevice, when: datetime) -> None:
        device.advance(when, self._outdoor_c(when))

    # --- write path ---------------------------------------------------------------------

    def service_caller(self) -> ServiceCaller:
        """The write capability (a real ``ServiceCaller`` over the fake's commander)."""

        async def read_state(entity_id: str) -> EntityState | None:
            return self._published.get(entity_id)

        return ServiceCaller(self._command, read_state, self._write_limiter)

    def _new_context(self, *, user_id: str | None = None) -> StateContext:
        return StateContext(id=f"01SIM{next(self._context_seq):021d}", user_id=user_id)

    def _record(self, record: CallRecord) -> None:
        self.call_log.append(record)

    async def _command(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Execute one ``call_service`` message and answer like the WebSocket API does."""
        domain = str(payload.get("domain", ""))
        service = str(payload.get("service", ""))
        now = self._sim_now()
        merged = {**dict(payload.get("service_data") or {}), **dict(payload.get("target") or {})}
        raw_ids = merged.get("entity_id")
        entity_ids: tuple[str, ...] = (
            (raw_ids,)
            if isinstance(raw_ids, str)
            else tuple(str(e) for e in raw_ids)
            if isinstance(raw_ids, list | tuple)
            else ()
        )
        fields = {k: v for k, v in merged.items() if k not in TARGET_KEYS}
        if self._state is not ConnectionState.CONNECTED:
            self._record(
                CallRecord(now, domain, service, entity_ids, fields, None, (), "not_connected")
            )
            msg = f"fake HA is {self._state.value}"
            raise NotConnectedError(msg)
        try:
            clean = validate_schema(domain, service, fields)
            if not any(k in merged for k in TARGET_KEYS):
                msg = (
                    "must contain at least one of entity_id, device_id, area_id, "
                    "floor_id, label_id."
                )
                raise ServiceError("invalid_format", msg)
            if payload.get("return_response"):
                raise ServiceError(
                    "service_validation_error",
                    "Validation error: An action which does not return responses "
                    "can't be called with return_response=True",
                )
            planned = self._plan(now, domain, service, entity_ids, clean)
        except ServiceError as exc:
            self._record(CallRecord(now, domain, service, entity_ids, fields, None, (), exc.code))
            raise CommandError(exc.code, exc.message) from exc
        context = self._new_context(user_id=SUPERVISOR_USER_ID)
        self._record(
            CallRecord(
                now,
                domain,
                service,
                entity_ids,
                fields,
                context.id,
                tuple(e for e, _, _ in planned),
            )
        )

        def context_at(
            t: datetime, _call: datetime = now, _ctx: StateContext = context
        ) -> StateContext:
            # HA keeps the call's context on a write within 5 s, else the state gets a fresh one.
            if (t - _call).total_seconds() <= CONTEXT_RECENT_S:
                return _ctx
            return self._new_context()

        blocking = 0.0
        for _, device, effect in planned:
            self._schedule(device, effect, now, context_at)
            blocking = max(blocking, effect.blocking_s)
        self.run_events_until(now)
        if blocking > 0:
            await self._sleep(blocking / self._speed)
        return {"context": {"id": context.id, "parent_id": None, "user_id": context.user_id}}

    def _plan(
        self,
        now: datetime,
        domain: str,
        service: str,
        entity_ids: tuple[str, ...],
        data: dict[str, Any],
    ) -> list[tuple[str, SimDevice, Effect]]:
        """Resolve the targets like ``entity_service_call`` and validate against each entity.

        Entities that do not exist, are of another domain or are unavailable are dropped without
        an error (research: "a successful result never proves a write").
        """
        assert self._demo is not None
        planned: list[tuple[str, SimDevice, Effect]] = []
        for entity_id in entity_ids:
            device = self._demo.devices.of_entity(entity_id)
            if device is None or entity_id.split(".", 1)[0] != domain:
                continue
            published = self._published.get(entity_id)
            if entity_id in self._unavailable or (
                published is not None and published.state == "unavailable"
            ):
                continue
            self._advance_device(device, now)
            planned.append((entity_id, device, device.call(now, domain, service, entity_id, data)))
        return planned

    def _schedule(
        self,
        device: SimDevice,
        effect: Effect,
        start: datetime,
        context_at: Callable[[datetime], StateContext],
    ) -> None:
        for step in effect.steps:
            self._at(
                start + timedelta(seconds=step.at_s),
                partial(self._physical, device, step.change, effect.push_delay_s, context_at),
            )

    def _physical(
        self,
        device: SimDevice,
        change: Callable[[], None],
        push_delay_s: float,
        context_at: Callable[[datetime], StateContext],
    ) -> None:
        when = self._event_time
        assert when is not None
        self._advance_device(device, when)
        change()
        if push_delay_s <= 0:
            self._push(device, context_at(when))
        else:
            self._at(
                when + timedelta(seconds=push_delay_s),
                partial(self._delayed_push, device, context_at),
            )

    def _delayed_push(
        self, device: SimDevice, context_at: Callable[[datetime], StateContext]
    ) -> None:
        when = self._event_time
        assert when is not None
        self._push(device, context_at(when))

    def _push(self, device: SimDevice, context: StateContext) -> None:
        """The device reports its state: the entities change in Home Assistant."""
        when = self._sim_now()
        self._advance_device(device, when)
        device.report()
        self._commit(self._device_rows(device.states(when)), context=context, emit=True)

    def _manual(self, event: ManualEvent) -> None:
        """A user changes a device outside SunCore (remote control, vendor app, HA UI)."""
        assert self._demo is not None
        when = self._event_time
        assert when is not None
        device = self._demo.devices.devices[event.device]
        self._advance_device(device, when)
        effect = device.manual(event.action, event.value)
        context = (
            self._new_context(user_id=USER_ID) if event.source == "ui" else self._new_context()
        )

        def same_context(_t: datetime) -> StateContext:
            return context

        self._schedule(device, effect, when, same_context)

    # --- publishing -----------------------------------------------------------------------

    @staticmethod
    def _device_rows(outs: list[StateOut]) -> list[tuple[str, str, dict[str, Any]]]:
        return [(o.entity_id, o.state, o.attributes) for o in outs]

    def _commit(
        self,
        rows: list[tuple[str, str, dict[str, Any]]],
        *,
        context: StateContext | None,
        emit: bool,
        faults: tuple[frozenset[str], frozenset[str], frozenset[str]] | None = None,
        only_changes: bool = True,
    ) -> dict[str, EntityState]:
        """Publish ``rows``; device entities only when they changed (like Home Assistant).

        Returns the states that were published; with ``emit`` they are also delivered to the
        listeners (subscribed entities only, and only while connected).
        """
        unavailable, stale, frozen = faults or (self._unavailable, frozenset(), frozenset())
        now = self._clock().timestamp()
        sim_now = self._sim_now()
        changed: dict[str, EntityState] = {}
        for entity_id, new_text, new_attributes in rows:
            previous = self._published.get(entity_id)
            if previous is not None and (entity_id in stale):
                continue
            text, attributes = new_text, new_attributes
            if entity_id in unavailable:
                text = "unavailable"
            elif entity_id in frozen and previous is not None:
                text, attributes = previous.state, dict(previous.attributes)
            elif (
                only_changes
                and previous is not None
                and previous.state == text
                and dict(previous.attributes) == attributes
                and not self._heartbeat_due(entity_id, sim_now)
            ):
                continue
            self._reported[entity_id] = sim_now
            new = EntityState(
                entity_id=entity_id,
                state=text,
                attributes=attributes,
                last_changed=previous.last_changed
                if previous is not None and previous.state == text
                else now,
                last_updated=now,
                last_reported=now,
                context=context if context is not None else self._new_context(),
            )
            self._published[entity_id] = new
            changed[entity_id] = new
        if emit and changed and self._state is ConnectionState.CONNECTED:
            visible = {e: s for e, s in changed.items() if e in self._subscribed}
            if visible:
                self._notify(EntityUpdate(changed=visible))
        return changed

    def _heartbeat_due(self, entity_id: str, now: datetime) -> bool:
        """A slow sensor (polled every 5 min, hourly counter) reports again after its cadence.

        Home Assistant fires no ``state_changed`` for an unchanged value, but the polled
        sensor's ``last_reported`` moves and the client's periodic resync delivers it; the
        simulator emits the unchanged state instead.
        """
        assert self._demo is not None
        cadence = self._heartbeats.get(entity_id)
        last = self._reported.get(entity_id)
        return cadence is not None and last is not None and (now - last).total_seconds() >= cadence

    # --- the house feed ---------------------------------------------------------------------

    def advance(self) -> bool:
        """Move to the next scenario step; ``False`` when it ended and does not loop."""
        return self._advance()

    def _advance(self) -> bool:
        """Move to the next step; ``False`` when the scenario ended and does not loop."""
        assert self._house is not None
        if self._step_index >= self._scenario.steps:
            if not self._loop:
                return False
            self._house = self._scenario.build_house()
            self._step_index = 0
        self._apply_step(emit=True)
        return True

    def _step_house(self) -> HouseState:
        """Deliver due events, advance the devices, step the house with their draw."""
        assert self._house is not None
        assert self._demo is not None
        when = self._house.time
        self.run_events_until(when)
        self._demo.advance_devices(when)
        state = self._house.step(self._demo.devices_power_w())
        self._demo.step(state)
        return state

    def _faults_at(
        self, offset_s: int
    ) -> tuple[bool, tuple[frozenset[str], frozenset[str], frozenset[str]]]:
        """``(disconnected, (unavailable, stale, frozen))`` entity sets of the active faults."""
        assert self._demo is not None
        sets: dict[str, set[str]] = {"unavailable": set(), "stale": set(), "frozen": set()}
        disconnected = False
        for fault in self._scenario.active_faults(offset_s):
            if fault.kind == "disconnect":
                disconnected = True
                continue
            assert fault.target is not None
            if fault.target in _SIGNAL_ENTITY:
                sets[fault.kind].add(_SIGNAL_ENTITY[fault.target])  # type: ignore[index]
            elif fault.target.startswith("device:"):
                device = self._demo.devices.devices.get(fault.target[7:])
                if device is not None:
                    sets[fault.kind].update(device.entity_ids())
            else:
                sets[fault.kind].add(fault.target)
        return disconnected, (
            frozenset(sets["unavailable"]),
            frozenset(sets["stale"]),
            frozenset(sets["frozen"]),
        )

    def _apply_step(self, *, emit: bool) -> None:
        assert self._house is not None
        sc = self._scenario
        offset_s = self._step_index * sc.step_s
        self._step_index += 1
        self._house_state = house_state = self._step_house()
        assert self._demo is not None
        disconnected, faults = self._faults_at(offset_s)
        self._offset_s = offset_s
        self._unavailable = faults[0]

        rows: list[tuple[str, str, dict[str, Any]]] = []
        for entity_id, value in _values(house_state).items():
            unit, device_class, name = _META[entity_id]
            attributes = {
                "unit_of_measurement": unit,
                "device_class": device_class,
                "state_class": "measurement",
                "friendly_name": name,
            }
            rows.append((entity_id, _format(value), attributes))
        changed = self._commit(rows, context=None, emit=False, faults=faults, only_changes=False)
        device_ids = self._demo.device_entity_ids()
        demo_rows = [(e.entity_id, e.state, e.attributes) for e in self._demo.entities()]
        changed.update(
            self._commit(
                [r for r in demo_rows if r[0] not in device_ids],
                context=None,
                emit=False,
                faults=faults,
                only_changes=False,
            )
        )
        # Device entities: Home Assistant only reports changes (slow sensors, a drifting
        # room temperature), so only those are emitted.
        changed.update(
            self._commit(
                [r for r in demo_rows if r[0] in device_ids],
                context=None,
                emit=False,
                faults=faults,
                only_changes=True,
            )
        )
        if not emit:
            return
        if disconnected:
            self._set_state(ConnectionState.RECONNECTING)
            return
        if self._state is ConnectionState.RECONNECTING:
            self._set_state(ConnectionState.CONNECTED)
            if self._subscribed:
                self._emit_snapshot()
            return
        visible = {e: s for e, s in changed.items() if e in self._subscribed}
        if visible:
            self._notify(EntityUpdate(changed=visible))
