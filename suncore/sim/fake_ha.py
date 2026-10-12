# SPDX-License-Identifier: Apache-2.0
"""A fake Home Assistant connection fed by the simulator (BRIEF 4.14, ADR-0010).

:class:`FakeHAConnection` satisfies :class:`suncore.ha.connection.HAConnection` (read
side only; there is deliberately no write path, so the demo cannot switch anything). It
exposes the virtual house as HA-like sensors with simulator-only ids
(``sensor.sim_*``) and advances the house one scenario step per ``step_s / speed``
seconds of *injected* sleep, so tests never wait and the demo can run accelerated.

Behaviour per step:

* every entity that is not frozen is published each step (like an integration that
  reports regularly; the cache then never mistakes a constant 0 W for a dead sensor);
* fault ``unavailable``: the entity's state is ``"unavailable"`` while the window lasts;
* fault ``stale``: the entity is neither updated nor emitted, its last published value
  stays visible (also through :meth:`get_states`);
* fault ``disconnect``: the connection goes ``RECONNECTING``, nothing is emitted and
  reads fail; when the window ends it is ``CONNECTED`` again and a full snapshot of the
  subscribed entities is delivered.

The scenario restarts from its first step when it ends and ``loop_scenario`` is true;
otherwise the feed stops and the last values stay.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Collection, Mapping
from datetime import datetime
from typing import Any, Final

from suncore.core.site import BatteryConfig, EntityRef, GridConfig, SiteConfig
from suncore.ha.connection import (
    ConnectionState,
    EntityListener,
    EntityState,
    EntityUpdate,
    NotConnectedError,
    StateListener,
    Unsubscribe,
)
from suncore.sim.house import House, HouseState
from suncore.sim.scenario import Scenario, SimSignal

_log = logging.getLogger(__name__)

GRID_ID: Final = "sensor.sim_grid_power"
PV_ID: Final = "sensor.sim_pv_power"
BATTERY_ID: Final = "sensor.sim_battery_power"
SOC_ID: Final = "sensor.sim_battery_soc"
HOUSE_ID: Final = "sensor.sim_house_power"

SIM_ENTITY_IDS: Final[tuple[str, ...]] = (GRID_ID, PV_ID, BATTERY_ID, SOC_ID, HOUSE_ID)

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


def demo_site_config() -> SiteConfig:
    """Site configuration mapping the simulator entities (signed grid, one PV, battery)."""
    return SiteConfig(
        grid=GridConfig(power=EntityRef(entity_id=GRID_ID)),
        pv=[EntityRef(entity_id=PV_ID)],
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


class FakeHAConnection:
    """Simulator-backed :class:`HAConnection`.

    Args:
        scenario: the scenario to play (house, step, faults).
        clock: injected wall clock; used for entity timestamps only.
        sleep: injected sleep; called with ``step_s / speed`` between steps.
        speed: acceleration factor (> 0); 1.0 plays in real time.
        loop_scenario: restart the scenario when it ends.
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
        # Fast-forward silently so the house state (e.g. battery SoC) evolves
        # as if the scenario had run since its start.
        for _ in range(self._start_steps):
            self._house.step()
            self._step_index += 1
        self._apply_step(emit=False)
        self._set_state(ConnectionState.CONNECTED)
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

    # --- internals ------------------------------------------------------------

    @staticmethod
    def _add[T](listeners: list[T], listener: T) -> Unsubscribe:
        listeners.append(listener)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                listeners.remove(listener)

        return remove

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
            if not self._advance():
                return

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

    def _apply_step(self, *, emit: bool) -> None:
        assert self._house is not None
        sc = self._scenario
        offset_s = self._step_index * sc.step_s
        self._step_index += 1
        self._house_state = house_state = self._house.step()
        faults = sc.active_faults(offset_s)
        disconnected = any(f.kind == "disconnect" for f in faults)
        unavailable = {
            _SIGNAL_ENTITY[f.target] for f in faults if f.kind == "unavailable" and f.target
        }
        stale = {_SIGNAL_ENTITY[f.target] for f in faults if f.kind == "stale" and f.target}

        now = self._clock().timestamp()
        changed: dict[str, EntityState] = {}
        for entity_id, value in _values(house_state).items():
            if entity_id in stale and entity_id in self._published:
                continue
            text = "unavailable" if entity_id in unavailable else _format(value)
            previous = self._published.get(entity_id)
            unit, device_class, name = _META[entity_id]
            new = EntityState(
                entity_id=entity_id,
                state=text,
                attributes={
                    "unit_of_measurement": unit,
                    "device_class": device_class,
                    "state_class": "measurement",
                    "friendly_name": name,
                },
                last_changed=previous.last_changed
                if previous is not None and previous.state == text
                else now,
                last_updated=now,
                last_reported=now,
            )
            self._published[entity_id] = new
            changed[entity_id] = new

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
