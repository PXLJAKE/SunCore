# SPDX-License-Identifier: Apache-2.0
"""Registry parsers and the entity catalogue (snapshot, refresh, degradation)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from suncore.ha.connection import CommandError, ConnectionState, EntityState, NotConnectedError
from suncore.ha.registry import (
    EntityCatalogue,
    RegistryDevice,
    RegistryEntity,
    build_meta,
    parse_area_registry,
    parse_device_registry,
    parse_entity_registry,
)
from suncore.sim import demo_devices as d
from suncore.sim.fake_ha import FakeHAConnection
from suncore.sim.scenario import load_scenario
from tests.ha.fakes import FakeClock, wait_until

pytestmark = pytest.mark.simulator

# --- parsers ----------------------------------------------------------------------


def test_parse_list_for_display_with_category_index_and_optional_keys() -> None:
    raw = {
        "entity_categories": {"0": "config", "1": "diagnostic"},
        "entities": [
            {"ei": "sensor.a", "pl": "lg_thinq", "di": "d1", "ai": "kitchen", "tk": "k", "en": "A"},
            {"ei": "sensor.b", "pl": "x", "ec": 1, "hb": True},
            {"ei": "sensor.c", "ec": 0},
            {"ei": "sensor.d", "ec": 7},  # unknown index
            {"pl": "no_entity_id"},
            "junk",
        ],
    }
    parsed = parse_entity_registry(raw)
    assert set(parsed) == {"sensor.a", "sensor.b", "sensor.c", "sensor.d"}
    a = parsed["sensor.a"]
    assert (a.platform, a.device_id, a.area_id, a.translation_key, a.name) == (
        "lg_thinq",
        "d1",
        "kitchen",
        "k",
        "A",
    )
    assert a.entity_category is None
    assert a.hidden is False
    assert parsed["sensor.b"].entity_category == "diagnostic"
    assert parsed["sensor.b"].hidden is True
    assert parsed["sensor.c"].entity_category == "config"
    assert parsed["sensor.d"].entity_category is None


def test_parse_full_list_shape_reports_disabled_and_hidden() -> None:
    raw = [
        {
            "entity_id": "sensor.a",
            "platform": "p",
            "device_id": None,
            "area_id": "a1",
            "translation_key": None,
            "entity_category": "diagnostic",
            "hidden_by": "user",
            "disabled_by": None,
            "name": None,
            "original_name": "Orig",
        },
        {"entity_id": "sensor.b", "hidden_by": None, "disabled_by": "integration"},
    ]
    parsed = parse_entity_registry(raw)
    assert parsed["sensor.a"].hidden is True
    assert parsed["sensor.a"].disabled is False
    assert parsed["sensor.a"].entity_category == "diagnostic"
    assert parsed["sensor.a"].name == "Orig"
    assert parsed["sensor.b"].disabled is True


def test_parse_garbage_registry_is_empty() -> None:
    assert parse_entity_registry({"entities": "nope"}) == {}
    assert parse_entity_registry({}) == {}
    assert parse_entity_registry([]) == {}


def test_parse_devices_new_old_and_child_shapes() -> None:
    raw = [
        {
            "id": "new",
            "name": "Name",
            "name_by_user": "Mine",
            "manufacturer": "LGE",
            "model": "M",
            "area_id": "living",
            "config_entry_id": "e1",
            "config_entries": ["old1"],
            "entry_type": "service",
        },
        {"id": "old", "name": "Old", "config_entries": ["e2", "e3"]},
        {"id": "child", "name": "Child", "parent_device_id": "new"},  # reduced record
        {"name": "no id"},
        "junk",
    ]
    devices = parse_device_registry(raw)  # type: ignore[arg-type]
    assert set(devices) == {"new", "old", "child"}
    assert devices["new"].name == "Mine"
    assert devices["new"].config_entry_id == "e1"
    assert (devices["new"].manufacturer, devices["new"].model, devices["new"].entry_type) == (
        "LGE",
        "M",
        "service",
    )
    assert devices["old"].config_entry_id == "e2"
    assert devices["child"].manufacturer is None
    assert devices["child"].config_entry_id is None


def test_parse_areas() -> None:
    assert parse_area_registry([{"area_id": "a", "name": "Kitchen"}, {"area_id": "b"}, {}]) == {
        "a": "Kitchen",
        "b": "b",
    }


def test_build_meta_joins_state_registry_and_device_area() -> None:
    state = EntityState(
        "sensor.x",
        "5",
        {
            "device_class": "energy",
            "unit_of_measurement": "Wh",
            "state_class": "total_increasing",
            "friendly_name": "X energy",
        },
    )
    entity = RegistryEntity(
        "sensor.x",
        platform="lg_thinq",
        device_id="d1",
        translation_key="energy_usage_today",
        entity_category="diagnostic",
        hidden=True,
    )
    devices = {"d1": RegistryDevice("d1", area_id="living")}
    meta = build_meta(state, entity, devices)
    assert meta.platform == "lg_thinq"
    assert meta.area_id == "living"  # inherited from the device
    assert meta.device_class == "energy"
    assert meta.unit == "Wh"
    assert meta.state_class == "total_increasing"
    assert meta.translation_key == "energy_usage_today"
    assert meta.entity_category == "diagnostic"
    assert meta.hidden is True
    assert meta.name == "X energy"
    bare = build_meta(state, None, {})
    assert bare.platform is None
    assert bare.device_id is None
    assert bare.hidden is False


# --- catalogue --------------------------------------------------------------------


class Ticker:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)
        self.requested: list[float] = []
        self._gate = asyncio.Semaphore(0)

    def clock(self) -> datetime:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.requested.append(delay)
        await self._gate.acquire()

    async def tick(self) -> None:
        self._gate.release()
        for _ in range(5):
            await asyncio.sleep(0)


@pytest.fixture
async def conn() -> AsyncIterator[FakeHAConnection]:
    ticker = Ticker()
    connection = FakeHAConnection(
        load_scenario("summer_day"), clock=ticker.clock, sleep=ticker.sleep, speed=1.0
    )
    await connection.connect()
    yield connection
    await connection.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def make_catalogue(conn: FakeHAConnection, clock: FakeClock, **kw: float) -> EntityCatalogue:
    return EntityCatalogue(
        conn, clock=clock.monotonic, sleep=clock.sleep, debounce_s=2.0, min_refresh_s=30.0, **kw
    )


async def test_refresh_loads_registry_and_states(conn: FakeHAConnection, clock: FakeClock) -> None:
    cat = make_catalogue(conn, clock)
    assert cat.registry_available is False
    assert cat.metas() == []
    await cat.refresh()
    assert cat.registry_available is True
    by_id = {m.entity_id: m for m in cat.metas()}
    ac = by_id[d.AC_ID]
    assert ac.platform == "lg_thinq"
    assert ac.translation_key == "climate_air_conditioner"
    assert ac.device_id == "sim_dev_living_room_ac"
    assert ac.area_id == "living_room"
    energy = by_id[d.AC_ENERGY_TODAY_ID]
    assert (energy.device_class, energy.unit) == ("energy", "Wh")
    assert cat.device(ac.device_id) is not None
    assert cat.area_name("living_room") == "Living room"
    assert cat.area_name(None) is None
    assert cat.device(None) is None
    # The browse cache is a separate EntityCache holding all states.
    assert d.PRICE_ID in cat.browse


async def test_registry_failure_degrades_to_state_only_metas(
    conn: FakeHAConnection, clock: FakeClock
) -> None:
    conn.registry_error = CommandError("unknown_command", "Unknown command.")
    cat = make_catalogue(conn, clock)
    await cat.refresh()  # must not raise
    assert cat.registry_available is False
    metas = {m.entity_id: m for m in cat.metas()}
    assert metas[d.AC_ID].platform is None
    assert metas[d.AC_ID].device_id is None
    assert metas["sensor.sim_pv_power"].device_class == "power"
    assert metas["sensor.sim_pv_power"].unit == "W"


async def test_registry_recovers_on_ensure_fresh_after_the_interval(
    conn: FakeHAConnection, clock: FakeClock
) -> None:
    conn.registry_error = CommandError("unknown_command", "x")
    cat = make_catalogue(conn, clock)
    await cat.refresh()
    conn.registry_error = None
    await cat.ensure_fresh()  # too early: no retry
    assert cat.registry_available is False
    await clock.advance(31)
    await cat.ensure_fresh()
    assert cat.registry_available is True


async def test_partial_registry_failure_keeps_entity_data(
    conn: FakeHAConnection, clock: FakeClock
) -> None:
    async def boom() -> list[dict[str, object]]:
        raise CommandError("unauthorized", "no")

    conn.device_registry_list = boom  # type: ignore[method-assign,assignment]
    cat = make_catalogue(conn, clock)
    await cat.refresh()
    assert cat.registry_available is True
    ac = next(m for m in cat.metas() if m.entity_id == d.AC_ID)
    assert ac.platform == "lg_thinq"
    assert ac.area_id == "living_room"  # the entity's own area


async def test_ensure_fresh_limits_get_states_to_the_interval(
    conn: FakeHAConnection, clock: FakeClock
) -> None:
    calls = 0
    original = conn.get_states

    async def counting():  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return await original()

    conn.get_states = counting  # type: ignore[method-assign]
    cat = make_catalogue(conn, clock)
    for _ in range(5):
        await cat.ensure_fresh()
    assert calls == 1
    await clock.advance(29)
    await cat.ensure_fresh()
    assert calls == 1
    await clock.advance(2)
    await cat.ensure_fresh()
    assert calls == 2


async def test_ensure_fresh_does_nothing_while_disconnected(
    conn: FakeHAConnection, clock: FakeClock
) -> None:
    cat = make_catalogue(conn, clock)
    await conn.close()
    await cat.ensure_fresh()
    assert cat.has_states is False
    with pytest.raises(NotConnectedError):
        await conn.device_registry_list()


async def test_state_failure_keeps_the_previous_browse_copy(
    conn: FakeHAConnection, clock: FakeClock
) -> None:
    cat = make_catalogue(conn, clock)
    await cat.refresh()
    before = len(cat.browse)

    async def boom():  # type: ignore[no-untyped-def]
        raise CommandError("x", "y")

    conn.get_states = boom  # type: ignore[method-assign]
    await cat.refresh()
    assert len(cat.browse) == before


async def test_registry_events_are_debounced_into_one_refresh(
    conn: FakeHAConnection, clock: FakeClock
) -> None:
    cat = make_catalogue(conn, clock)
    await cat.start()
    await wait_until(lambda: cat.registry_available)  # connected at start -> initial load
    loads = 0
    original = conn.entity_registry_list_for_display

    async def counting():  # type: ignore[no-untyped-def]
        nonlocal loads
        loads += 1
        return await original()

    conn.entity_registry_list_for_display = counting  # type: ignore[method-assign]
    for _ in range(5):
        conn.fire_registry_event("entity_registry_updated")
        await clock.settle()
    assert loads == 0
    assert clock.requested.count(2.0) == 5  # every event restarted the debounce timer
    await clock.advance(2.0)
    await wait_until(lambda: loads == 1)
    await clock.advance(10)
    assert loads == 1
    await cat.stop()


async def test_reconnect_triggers_a_reload(conn: FakeHAConnection, clock: FakeClock) -> None:
    cat = make_catalogue(conn, clock)
    await cat.start()
    await wait_until(lambda: cat.registry_available)
    conn.registry_error = CommandError("x", "y")
    await cat.refresh()
    assert cat.registry_available is False
    conn.registry_error = None
    cat._on_state(ConnectionState.CONNECTED)
    await wait_until(lambda: cat.registry_available)
    await cat.stop()
    await cat.stop()  # idempotent


async def test_start_tolerates_unavailable_event_subscription(
    conn: FakeHAConnection, clock: FakeClock
) -> None:
    async def refuse(_listener):  # type: ignore[no-untyped-def]
        raise CommandError("unauthorized", "no")

    conn.subscribe_registry_updates = refuse  # type: ignore[method-assign]
    cat = make_catalogue(conn, clock)
    await cat.start()
    await wait_until(lambda: cat.registry_available)
    await cat.start()  # second start is a no-op
    await cat.stop()


def count_get_states(conn: FakeHAConnection) -> list[int]:
    calls = [0]
    original = conn.get_states

    async def counting():  # type: ignore[no-untyped-def]
        calls[0] += 1
        return await original()

    conn.get_states = counting  # type: ignore[method-assign]
    return calls


async def test_connect_and_registry_events_load_no_states(
    conn: FakeHAConnection, clock: FakeClock
) -> None:
    calls = count_get_states(conn)
    cat = make_catalogue(conn, clock)
    await cat.start()
    await wait_until(lambda: cat.registry_available)
    conn.fire_registry_event("entity_registry_updated")
    await clock.settle()
    await clock.advance(2.0)
    await clock.advance(10)
    assert calls[0] == 0
    assert cat.has_states is False
    await cat.ensure_fresh()  # the first request loads them, once
    assert calls[0] == 1
    await cat.stop()


async def test_reconnect_reloads_registry_and_states_lazily_once(
    conn: FakeHAConnection, clock: FakeClock
) -> None:
    cat = make_catalogue(conn, clock)
    await cat.start()
    await wait_until(lambda: cat.registry_available)
    await cat.ensure_fresh()
    calls = count_get_states(conn)
    cat._on_state(ConnectionState.CONNECTED)  # reconnect, states loaded before
    await clock.settle()
    assert calls[0] == 0
    await cat.ensure_fresh()  # within the interval, but marked stale by the reconnect
    assert calls[0] == 1
    await cat.ensure_fresh()
    assert calls[0] == 1
    await cat.stop()


async def test_browse_copy_keeps_only_the_attributes_that_are_read(
    conn: FakeHAConnection, clock: FakeClock
) -> None:
    async def rich() -> list[EntityState]:
        return [
            EntityState(
                "sensor.x",
                "1",
                {
                    "device_class": "power",
                    "unit_of_measurement": "W",
                    "state_class": "measurement",
                    "friendly_name": "X",
                    "forecast": [1] * 1000,
                    "icon": "mdi:flash",
                },
            )
        ]

    conn.get_states = rich  # type: ignore[method-assign]
    cat = make_catalogue(conn, clock)
    await cat.refresh()
    cached = cat.browse.get("sensor.x")
    assert cached is not None
    assert dict(cached.state.attributes) == {
        "device_class": "power",
        "unit_of_measurement": "W",
        "state_class": "measurement",
        "friendly_name": "X",
    }
    assert cat.metas()[0].unit == "W"


async def test_registry_event_during_a_running_refresh_reruns_it(
    conn: FakeHAConnection, clock: FakeClock
) -> None:
    cat = make_catalogue(conn, clock)
    await cat.start()
    await wait_until(lambda: cat.registry_available)
    loads = 0
    entered = asyncio.Event()
    release = asyncio.Event()
    original = conn.entity_registry_list_for_display

    async def slow():  # type: ignore[no-untyped-def]
        nonlocal loads
        loads += 1
        entered.set()
        await release.wait()
        return await original()

    conn.entity_registry_list_for_display = slow  # type: ignore[method-assign]
    conn.fire_registry_event("entity_registry_updated")
    await clock.settle()
    await clock.advance(2.0)
    await asyncio.wait_for(entered.wait(), 1)
    conn.fire_registry_event("device_registry_updated")  # arrives mid-load: must not cancel
    await clock.settle()
    assert loads == 1
    release.set()
    await clock.settle(20)
    await clock.advance(2.0)  # the debounce before the rerun
    await wait_until(lambda: loads == 2)
    await cat.stop()
