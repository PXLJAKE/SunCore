# SPDX-License-Identifier: Apache-2.0
"""FakeHAConnection with injected clock and sleep (no real waiting, no network)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from suncore.core.balance import compute_flows
from suncore.core.signals import Quality
from suncore.ha.cache import EntityCache
from suncore.ha.connection import (
    ConnectionState,
    EntityState,
    EntityUpdate,
    HAConnection,
    NotConnectedError,
)
from suncore.sim.fake_ha import (
    BATTERY_ID,
    GRID_ID,
    HOUSE_ID,
    PV_ID,
    SIM_ENTITY_IDS,
    SOC_ID,
    FakeHAConnection,
    demo_site_config,
)
from suncore.sim.scenario import Scenario, load_scenario

pytestmark = pytest.mark.simulator


class Ticker:
    """Injected clock and sleep: each ``tick`` lets exactly one pending sleep return."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)
        self.requested: list[float] = []
        self._gate = asyncio.Semaphore(0)

    def clock(self) -> datetime:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.requested.append(delay)
        await self._gate.acquire()

    async def tick(self, n: int = 1, *, seconds: float = 30.0) -> None:
        for _ in range(n):
            self.now += timedelta(seconds=seconds)
            self._gate.release()
            for _ in range(5):
                await asyncio.sleep(0)


class Recorder:
    def __init__(self, conn: FakeHAConnection) -> None:
        self.updates: list[EntityUpdate] = []
        self.states: list[ConnectionState] = []
        conn.add_entity_listener(self.updates.append)
        conn.add_state_listener(self.states.append)


@pytest.fixture
def ticker() -> Ticker:
    return Ticker()


@pytest.fixture
def dropout() -> Scenario:
    return load_scenario("sensor_dropout")  # step 30 s


@pytest.fixture
async def conn(dropout: Scenario, ticker: Ticker) -> AsyncIterator[FakeHAConnection]:
    c = FakeHAConnection(dropout, clock=ticker.clock, sleep=ticker.sleep)
    yield c
    await c.close()


async def _start(conn: FakeHAConnection) -> Recorder:
    rec = Recorder(conn)
    await conn.connect()
    await conn.subscribe_entities(SIM_ENTITY_IDS)
    return rec


def _step_for(offset_s: int) -> int:
    return offset_s // 30


def test_protocol_and_ids(conn: FakeHAConnection) -> None:
    assert isinstance(conn, HAConnection)
    assert {demo_site_config().entity_refs()[i].entity_id for i in range(5)} == set(SIM_ENTITY_IDS)
    assert all(e.startswith("sensor.sim_") for e in SIM_ENTITY_IDS)


async def test_snapshot_then_diffs(conn: FakeHAConnection, ticker: Ticker) -> None:
    rec = await _start(conn)
    assert conn.state is ConnectionState.CONNECTED
    assert rec.states == [ConnectionState.CONNECTING, ConnectionState.CONNECTED]
    assert len(rec.updates) == 1
    snap = rec.updates[0]
    assert snap.full
    assert set(snap.changed) == set(SIM_ENTITY_IDS)
    grid = snap.changed[GRID_ID]
    assert grid.attributes["unit_of_measurement"] == "W"
    assert grid.attributes["device_class"] == "power"
    assert grid.attributes["state_class"] == "measurement"
    assert grid.attributes["friendly_name"]
    assert snap.changed[SOC_ID].attributes["device_class"] == "battery"
    assert snap.changed[SOC_ID].attributes["unit_of_measurement"] == "%"

    await ticker.tick()
    assert ticker.requested[0] == 30.0
    assert len(rec.updates) == 2
    diff = rec.updates[1]
    assert not diff.full
    assert diff.removed == ()
    assert set(diff.changed) == set(SIM_ENTITY_IDS)
    assert diff.changed[HOUSE_ID].last_updated == ticker.now.timestamp()
    states = await conn.get_states()
    assert {s.entity_id: s.state for s in states} == {e: s.state for e, s in diff.changed.items()}


async def test_only_subscribed_entities_are_emitted(conn: FakeHAConnection, ticker: Ticker) -> None:
    rec = Recorder(conn)
    await conn.connect()
    await conn.subscribe_entities([PV_ID])
    await ticker.tick()
    assert [set(u.changed) for u in rec.updates] == [{PV_ID}, {PV_ID}]
    await conn.subscribe_entities([])
    await ticker.tick()
    assert len(rec.updates) == 2


async def test_speed_scales_sleep(dropout: Scenario, ticker: Ticker) -> None:
    c = FakeHAConnection(dropout, clock=ticker.clock, sleep=ticker.sleep, speed=60.0)
    await c.connect()
    await ticker.tick()
    assert ticker.requested[0] == pytest.approx(0.5)
    await c.close()
    with pytest.raises(ValueError, match="speed"):
        FakeHAConnection(dropout, clock=ticker.clock, speed=0)


async def test_entity_cache_balance_closes(conn: FakeHAConnection, ticker: Ticker) -> None:
    cache = EntityCache(clock=ticker.clock)
    cache.attach(conn)
    await conn.connect()
    await conn.subscribe_entities(SIM_ENTITY_IDS)
    site = demo_site_config()
    for _ in range(40):
        await ticker.tick()
        flows = compute_flows(site, cache.samples())
        for flow in (flows.grid_w, flows.pv_w, flows.battery_w, flows.house_w):
            assert flow.quality is Quality.OK
            assert flow.value is not None
        assert flows.grid_w.value is not None
        assert flows.pv_w.value is not None
        assert flows.battery_w.value is not None
        assert flows.house_w.value is not None
        # pv + grid == house + battery, up to the 0.1 W rounding of four values
        assert flows.pv_w.value + flows.grid_w.value == pytest.approx(
            flows.house_w.value + flows.battery_w.value, abs=0.3
        )
        assert flows.battery_soc_pct.value is not None
        assert 0 <= flows.battery_soc_pct.value <= 100


async def test_unavailable_fault_window(conn: FakeHAConnection, ticker: Ticker) -> None:
    rec = await _start(conn)
    start, end = _step_for(7200), _step_for(9000)  # grid_power unavailable [240, 300)
    await ticker.tick(start - 1)  # step index start-1 published
    assert rec.updates[-1].changed[GRID_ID].state != "unavailable"
    await ticker.tick()  # step index == start
    last = rec.updates[-1]
    assert last.changed[GRID_ID].state == "unavailable"
    assert last.changed[PV_ID].state != "unavailable"
    await ticker.tick(end - start - 1)
    assert rec.updates[-1].changed[GRID_ID].state == "unavailable"
    await ticker.tick()  # first step after the window
    assert rec.updates[-1].changed[GRID_ID].state != "unavailable"
    float(rec.updates[-1].changed[GRID_ID].state)


async def test_unavailable_reaches_flows(conn: FakeHAConnection, ticker: Ticker) -> None:
    cache = EntityCache(clock=ticker.clock)
    cache.attach(conn)
    await _start(conn)
    await ticker.tick(_step_for(7200))
    flows = compute_flows(demo_site_config(), cache.samples())
    assert flows.grid_w.quality is Quality.UNAVAILABLE
    assert flows.grid_w.value is None


async def test_stale_fault_freezes_value(conn: FakeHAConnection, ticker: Ticker) -> None:
    rec = await _start(conn)
    start, end = _step_for(14400), _step_for(18000)  # battery_soc stale [480, 600)
    await ticker.tick(start - 1)
    frozen = rec.updates[-1].changed[SOC_ID]
    await ticker.tick()
    during = rec.updates[-1]
    assert SOC_ID not in during.changed
    assert GRID_ID in during.changed
    await ticker.tick(end - start - 2)
    assert all(SOC_ID not in u.changed for u in rec.updates[start + 1 : end])
    (still,) = (s for s in await conn.get_states() if s.entity_id == SOC_ID)
    assert still == frozen
    await ticker.tick(2)
    assert SOC_ID in rec.updates[-1].changed


async def test_stale_entity_ages_in_cache(conn: FakeHAConnection, ticker: Ticker) -> None:
    cache = EntityCache(clock=ticker.clock, max_age_s=120.0)
    cache.attach(conn)
    await _start(conn)
    await ticker.tick(_step_for(14400) - 1)
    assert cache.sample(SOC_ID) is not None
    await ticker.tick(10)  # 300 s without SoC updates
    soc = cache.sample(SOC_ID)
    pv = cache.sample(PV_ID)
    assert soc is not None
    assert soc.quality is Quality.STALE
    assert soc.value is not None
    assert pv is not None
    assert pv.quality is Quality.OK


async def test_disconnect_fault_and_resnapshot(conn: FakeHAConnection, ticker: Ticker) -> None:
    rec = await _start(conn)
    start, end = _step_for(28800), _step_for(29100)  # [960, 970)
    await ticker.tick(start - 1)
    n_before = len(rec.updates)
    await ticker.tick()
    assert conn.state is ConnectionState.RECONNECTING
    assert rec.states[-1] is ConnectionState.RECONNECTING
    with pytest.raises(NotConnectedError):
        await conn.get_states()
    with pytest.raises(NotConnectedError):
        await conn.get_config()
    await ticker.tick(end - start - 1)
    assert len(rec.updates) == n_before  # silent while disconnected
    await ticker.tick()
    assert conn.state is ConnectionState.CONNECTED
    assert rec.states[-2:] == [ConnectionState.RECONNECTING, ConnectionState.CONNECTED]
    resync = rec.updates[-1]
    assert resync.full
    assert set(resync.changed) == set(SIM_ENTITY_IDS)
    assert len(await conn.get_states()) == 5


async def test_cache_survives_reconnect(conn: FakeHAConnection, ticker: Ticker) -> None:
    cache = EntityCache(clock=ticker.clock)
    cache.attach(conn)
    await _start(conn)
    await ticker.tick(_step_for(29100))
    assert conn.state is ConnectionState.CONNECTED
    assert len(cache) == 5


async def test_scenario_loops(ticker: Ticker) -> None:
    sc = load_scenario("summer_day")
    c = FakeHAConnection(sc, clock=ticker.clock, sleep=ticker.sleep)
    await c.connect()
    await c.subscribe_entities([SOC_ID])
    rec = Recorder(c)
    (first,) = (s for s in await c.get_states() if s.entity_id == SOC_ID)
    await ticker.tick(sc.steps - 1)
    await ticker.tick(1)  # restart: step 0 again
    assert len(rec.updates) == sc.steps
    assert rec.updates[-1].changed[SOC_ID].state == first.state
    assert c.sim_time == sc.start
    await c.close()


async def test_no_loop_stops_feed(ticker: Ticker) -> None:
    sc = load_scenario("summer_day")
    c = FakeHAConnection(sc, clock=ticker.clock, sleep=ticker.sleep, loop_scenario=False)
    await c.connect()
    rec = Recorder(c)
    await c.subscribe_entities([PV_ID])
    await ticker.tick(sc.steps + 3)
    assert len(rec.updates) == sc.steps  # snapshot + (steps - 1) diffs
    assert c._task is not None
    assert c._task.done()
    await c.close()


async def test_get_config(conn: FakeHAConnection) -> None:
    await conn.connect()
    cfg = await conn.get_config()
    assert cfg["currency"] == "EUR"
    assert cfg["version"] == "sim"
    assert isinstance(cfg["time_zone"], str)
    assert cfg["unit_system"]["temperature"] == "°C"


async def test_close_stops_task_cleanly(conn: FakeHAConnection, ticker: Ticker) -> None:
    rec = await _start(conn)
    task = conn._task
    assert task is not None
    assert not task.done()
    await conn.close()
    assert task.cancelled() or task.done()
    assert conn.state is ConnectionState.CLOSED
    assert rec.states[-1] is ConnectionState.CLOSED
    n = len(rec.updates)
    await ticker.tick(3)
    assert len(rec.updates) == n
    await conn.close()  # idempotent
    with pytest.raises(NotConnectedError):
        await conn.connect()
    with pytest.raises(NotConnectedError):
        await conn.get_states()


async def test_listener_removal_and_failure_isolation(
    conn: FakeHAConnection, ticker: Ticker
) -> None:
    seen: list[EntityUpdate] = []

    def boom(_: EntityUpdate) -> None:
        raise RuntimeError("listener bug")

    conn.add_entity_listener(boom)
    remove = conn.add_entity_listener(seen.append)
    await _start(conn)
    await ticker.tick()
    assert len(seen) == 2
    remove()
    remove()  # idempotent
    await ticker.tick()
    assert len(seen) == 2


def test_demo_site_config_is_valid() -> None:
    site = demo_site_config()
    assert site.grid.power is not None
    assert not site.grid.is_pair
    assert site.battery is not None
    assert site.house is not None
    assert [r.entity_id for r in site.pv] == [PV_ID]
    assert site.battery.power.entity_id == BATTERY_ID
    s = EntityState(entity_id=PV_ID, state="1.0")
    assert s.entity_id in SIM_ENTITY_IDS
