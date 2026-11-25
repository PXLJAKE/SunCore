# SPDX-License-Identifier: Apache-2.0
"""The demo's flexible loads (Phase 7, WP3): an EV on a wallbox, a washing machine, a heat pump."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from suncore.api import create_app
from suncore.app.main import build_runtime, demo_flex_user_tick
from suncore.app.runtime import Runtime
from suncore.core.devices import DeviceKind
from suncore.sim.demo_devices import DemoDevices, default_demo_devices
from suncore.sim.demo_flex import HEAT_PUMP_ID, WALLBOX_ID, WASHER_DEADLINE_AHEAD, WASHER_ID
from suncore.sim.devices import ProgramParams, SimHeatPump, SimProgram, SimWallbox
from suncore.sim.scenario import load_scenario
from tests.api.conftest import client_for
from tests.app.conftest import RecordingConnection, demo_settings

FLEX_IDS = {WALLBOX_ID, WASHER_ID, HEAT_PUMP_ID}


@pytest.fixture
async def flex_runtime(tmp_path: Path) -> AsyncIterator[Runtime]:
    conn = RecordingConnection(speed=6000.0, demo_flex=True)
    runtime = await build_runtime(demo_settings(tmp_path, demo_flex=True), env={}, connection=conn)
    await runtime.start()
    try:
        yield runtime
    finally:
        await runtime.stop()


async def test_the_demo_has_the_flexible_devices_in_shadow(flex_runtime: Runtime) -> None:
    async with client_for(create_app(flex_runtime)) as client:
        for _ in range(300):
            state = (await client.get("/api/v1/control/state")).json()
            if {d["device_id"] for d in state["devices"]} >= FLEX_IDS:
                break
            await asyncio.sleep(0.01)
        by_id = {d["device_id"]: d for d in state["devices"]}
        assert set(by_id) == {"pool_pump", "living_room_ac", "heating_rod", *FLEX_IDS}
        assert all(by_id[i]["effective_mode"] == "shadow" for i in FLEX_IDS)
        devices = (await client.get("/api/v1/config/devices")).json()["devices"]
        kinds = {d["id"]: d["kind"] for d in devices}
        assert kinds[WALLBOX_ID] == DeviceKind.WALLBOX.value
        assert kinds[WASHER_ID] == DeviceKind.PROGRAM.value
        assert kinds[HEAT_PUMP_ID] == DeviceKind.HEAT_PUMP.value
        templates = {d["id"]: d["template_id"] for d in devices}
        assert templates[WALLBOX_ID] == "generic.wallbox"
        assert templates[WASHER_ID] == "generic.program_plug"
        assert templates[HEAT_PUMP_ID] == "generic.heat_pump_sg_ready"

        car = (await client.get(f"/api/v1/devices/{WALLBOX_ID}/flex")).json()
        assert car["ev"]["mode"] == "target"
        assert car["ev"]["target"]["soc_pct"] == 80.0
        assert car["job"] is None
        washer = (await client.get(f"/api/v1/devices/{WASHER_ID}/flex")).json()
        assert washer["ev"] is None
        assert washer["job"]["device_id"] == WASHER_ID
        # the deadline lies ahead of the demo clock (relative, not a fixed time of day)
        deadline = datetime.fromisoformat(washer["job"]["deadline"])
        assert flex_runtime.clock() < deadline <= flex_runtime.clock() + timedelta(hours=7)


class _Jobs:
    def __init__(self) -> None:
        self.open: str | None = None

    def current(self, device_id: str) -> str | None:
        assert device_id == WASHER_ID
        return self.open


class _FakeRuntime:
    """What ``demo_flex_user_tick`` uses of the runtime, with a clock the test advances."""

    def __init__(self, now: datetime) -> None:
        self.now = now
        self.program_jobs = _Jobs()
        self.armed: list[tuple[str, datetime]] = []

    def clock(self) -> datetime:
        return self.now

    async def arm_program(self, device_id: str, deadline: datetime) -> None:
        job_id = f"{device_id}-{len(self.armed)}"
        self.armed.append((job_id, deadline))
        self.program_jobs.open = job_id


async def test_every_load_of_the_washer_gets_a_new_job() -> None:
    """The demo user arms a job for each load of the washing machine, after each finished
    programme: a different job with a deadline relative to the (scenario) clock. No sleeping."""
    start = datetime(2026, 6, 21, 10, 0, tzinfo=UTC)
    runtime = _FakeRuntime(start)
    washer = SimProgram(
        WASHER_ID,
        ProgramParams(profile_w=(2000.0, 480.0), slot_s=900.0, lag_s=0.0, loaded_at_s=None),
    )
    fake: Any = runtime

    async def tick() -> bool:
        return await demo_flex_user_tick(fake, washer)

    washer.advance(start, 20.0)
    assert not await tick()  # not loaded: nothing to arm

    washer.manual("turn_on", None).steps[0].change()  # the user loads it
    assert washer.loaded
    assert await tick()
    assert not await tick()  # one job per load
    first_id, first_deadline = runtime.armed[0]
    assert first_deadline == start + WASHER_DEADLINE_AHEAD

    # the programme (two 15 min slots) runs and finishes, the job is done
    for minutes in (15, 30, 31):
        runtime.now = start + timedelta(minutes=minutes)
        washer.advance(runtime.now, 20.0)
    assert washer.runs == 1
    assert not washer.loaded
    runtime.program_jobs.open = None
    assert not await tick()  # nothing loaded, nothing armed

    # the next load of laundry gets its own job, relative to the later clock
    washer.manual("turn_on", None).steps[0].change()
    assert await tick()
    second_id, second_deadline = runtime.armed[1]
    assert second_id != first_id
    assert second_deadline == runtime.now + WASHER_DEADLINE_AHEAD > first_deadline


def test_the_sim_user_loads_the_washer_again_after_each_programme() -> None:
    scenario = load_scenario("summer_day")
    house = scenario.build_house()
    demo = DemoDevices(scenario, flex=True)
    for _ in range(18 * 60):  # until 18:00 scenario time, one minute per step
        demo.step(house.step(demo.devices_power_w()))
    washer = demo.devices.devices[WASHER_ID]
    assert isinstance(washer, SimProgram)
    assert washer.runs >= 2  # loaded at 10:30, then again after every two-hour programme


async def test_the_default_demo_has_no_flexible_devices(demo_runtime: Runtime) -> None:
    ids = {d.id for d in (demo_runtime.devices.devices if demo_runtime.devices else [])}
    assert ids.isdisjoint(FLEX_IDS)


def test_the_flexible_devices_have_plausible_state_at_midday() -> None:
    scenario = load_scenario("summer_day")
    house = scenario.build_house()
    demo = DemoDevices(scenario, flex=True)
    state = None
    for _ in range(12 * 60 + 30):  # until 12:30 scenario time (one minute per step)
        state = house.step(demo.devices_power_w())
        demo.step(state)
    assert state is not None
    sims = demo.devices.devices
    car, washer, pump = sims[WALLBOX_ID], sims[WASHER_ID], sims[HEAT_PUMP_ID]
    assert isinstance(car, SimWallbox)
    assert isinstance(washer, SimProgram)
    assert isinstance(pump, SimHeatPump)
    # the car stands plugged in (since 08:00), idle (shadow: nobody charges it), at 30 %
    assert car.connected_at(state.time)
    assert car.power_w() == 0.0
    assert car.soc_pct == pytest.approx(30.0)
    # the washer was loaded at 10:30 and, nobody holding the plug, runs its programme
    assert washer.started_at
    assert washer.loaded or washer.runs >= 1
    # the heat pump's store is between its cold start and the hard limit
    assert 35.0 <= pump.temp_c <= pump.p.max_temp_c
    states = {s.entity_id: s for s in demo.devices.states(state.time)}
    assert states[car.connected_id or ""].state == "on"
    assert float(states[car.soc_id or ""].state) == pytest.approx(30.0, abs=0.1)


def test_flex_entities_are_added_without_collisions() -> None:
    default = set(default_demo_devices().entity_ids())
    flex = set(default_demo_devices(flex=True).entity_ids())
    assert default < flex
    assert "number.sim_wallbox_current" in flex - default


def test_the_registry_groups_the_flex_entities_by_device_only_with_flex() -> None:
    from suncore.sim import registry  # noqa: PLC0415

    plain = {r["entity_id"] for r in registry.entity_registry_list()}
    assert "number.sim_wallbox_current" not in plain
    rows = {r["entity_id"]: r for r in registry.entity_registry_list(flex=True)}
    assert rows["number.sim_wallbox_current"]["device_id"] == "sim_dev_wallbox"
    assert rows["sensor.sim_wallbox_power"]["device_id"] == "sim_dev_wallbox"
    assert rows["sensor.sim_washer_power"]["device_id"] == "sim_dev_washer"
    devices = {r["id"] for r in registry.device_registry_list(flex=True)}
    assert {"sim_dev_wallbox", "sim_dev_washer", "sim_dev_heat_pump"} <= devices
    assert "sim_dev_wallbox" not in {r["id"] for r in registry.device_registry_list()}
