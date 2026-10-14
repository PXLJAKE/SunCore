# SPDX-License-Identifier: Apache-2.0
"""The scenario runner, virtual time, device configs and the demo composition (Phase 3 WP8)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from suncore.core.decisions import (
    DecisionKind,
    OperatingMode,
    PowerSetpointAction,
    StepAction,
    SwitchAction,
)
from suncore.core.devices import DeviceConfig
from suncore.sim.demo_config import demo_devices
from suncore.sim.demo_devices import AC_DEVICE_ID, POOL_DEVICE_ID, ROD_DEVICE_ID
from suncore.sim.runner import (
    DeviceLog,
    RunResult,
    ScenarioRunner,
    StepLog,
    VirtualTime,
    build_device_config,
    build_device_configs,
    turns_on,
)
from suncore.sim.scenario import load_scenario, parse_scenario
from suncore.sim.stack import demo_stack
from tests.sim.helpers import catalog, run_scenario

pytestmark = pytest.mark.simulator

T0 = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)

SC = """
name: t
start: "2026-07-15T12:00:00+02:00"
duration_s: 600
step_s: 5
house: {battery: {capacity_wh: 0}}
devices:
  - id: pool
    template: generic.switch
    physics: {kind: switch, nominal_w: 750, power_sensor: true}
    control:
      priority: 3
      min_run_s: 120
      power_model: {kind: measured, power_role: power, nominal_w: 750}
  - id: free
    physics: {kind: switch}
"""


# --- virtual time ----------------------------------------------------------------------


async def test_virtual_time_wakes_sleepers_in_time_order() -> None:
    vt = VirtualTime(T0)
    order: list[tuple[str, float]] = []

    async def sleeper(name: str, seconds: float) -> None:
        await vt.sleep(seconds)
        order.append((name, vt.monotonic()))

    tasks = [asyncio.ensure_future(sleeper(n, s)) for n, s in (("b", 5.0), ("a", 2.0), ("c", 9.0))]
    await vt.settle()
    assert order == []  # nothing moves without the clock
    await vt.advance_to(T0 + timedelta(seconds=6))
    assert order == [("a", 2.0), ("b", 5.0)]  # woken at their own instants, not at the target
    assert vt.now == T0 + timedelta(seconds=6)
    await vt.advance_to(T0 + timedelta(seconds=10))
    assert [n for n, _ in order] == ["a", "b", "c"]
    await asyncio.gather(*tasks)
    assert vt.next_wake() is None


async def test_virtual_time_runs_device_events_in_between_and_chains_new_sleeps() -> None:
    vt = VirtualTime(T0)
    fired: list[float] = []
    events = [T0 + timedelta(seconds=3), T0 + timedelta(seconds=7)]

    def next_event() -> datetime | None:
        return events[0] if events else None

    def run_events(until: datetime) -> None:
        while events and events[0] <= until:
            fired.append((events.pop(0) - T0).total_seconds())

    async def chained() -> None:
        await vt.sleep(1)
        await vt.sleep(1)  # registered while time advances: still honoured

    task = asyncio.ensure_future(chained())
    await vt.advance_to(T0 + timedelta(seconds=10), events=next_event, run_events=run_events)
    assert fired == [3.0, 7.0]
    assert task.done()
    await vt.sleep(0)  # a zero sleep returns at once
    assert vt.monotonic() == 10.0


def test_virtual_time_needs_an_aware_start() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        VirtualTime(datetime(2026, 1, 1))  # noqa: DTZ001


# --- device configs from a scenario ----------------------------------------------------


def test_device_configs_come_from_the_templates_with_the_scenario_overrides() -> None:
    sc = parse_scenario(SC)
    runner = ScenarioRunner(sc, catalog())
    asyncio.run(runner.start())
    try:
        assert set(runner.configs) == {"pool"}  # the device without a template is not controlled
        pool = runner.configs["pool"]
        assert pool.template_id == "generic.switch"
        assert pool.priority == 3
        assert pool.min_run_s == 120  # overridden
        assert pool.min_pause_s == 300  # the template's default
        assert pool.settle_s == 3  # the template's default
        assert pool.power_model.nominal_w == 750
        assert pool.entities["power"].entity_id == "sensor.sim_pool_power"
        assert pool.entities["switch"].entity_id == "switch.sim_pool"
        assert runner.modes.device_mode("pool") is OperatingMode.AUTOMATIC
    finally:
        asyncio.run(runner.close())


def test_a_non_device_template_is_refused() -> None:
    text = SC.replace("generic.switch", "tibber.price")
    runner = ScenarioRunner(parse_scenario(text), catalog())

    async def go() -> None:
        await runner.conn.connect()
        build_device_configs(runner.scenario, catalog(), runner.sim_devices)

    with pytest.raises(TypeError, match="not a device template"):
        asyncio.run(go())


def test_build_device_config_replaces_the_power_model_and_merges_the_rest() -> None:
    sc = load_scenario("climate_counter_only")
    runner = ScenarioRunner(sc, catalog())

    async def go() -> DeviceConfig:
        await runner.conn.connect()
        spec = sc.devices[0]
        template = catalog().get(spec.template or "")
        assert template is not None
        return build_device_config(
            template,  # type: ignore[arg-type]
            spec,
            runner.sim_devices["ac"],
        )

    config = asyncio.run(go())
    assert config.power_model.kind.value == "counter_derived"
    assert config.power_model.energy_role == "energy_today"
    assert config.power_model.counter_update_s == 3600
    assert config.comfort is not None
    assert (config.comfort.min_c, config.comfort.max_c) == (21, 27)
    assert config.control.kind == "setpoint"


# --- evidence helpers ------------------------------------------------------------------


def synthetic(on: list[bool]) -> RunResult:
    sc = parse_scenario(SC)
    steps = [
        StepLog(sc.start, 100.0 if i % 2 else -50.0, 0.0, 0.0, 50.0, 0.0) for i in range(len(on))
    ]
    log = DeviceLog(on=on, power_w=[750.0 if o else 0.0 for o in on])
    return RunResult(sc, steps, {"pool": log}, {}, [])


def test_switch_counting_and_hold_checks_on_a_known_series() -> None:
    #           step:  0      1      2      3      4      5      6      7
    series = [False, True, True, False, True, True, True, False]
    result = synthetic(series)
    assert result.transitions("pool") == [(1, True), (3, False), (4, True), (7, False)]
    assert result.switches_in_window("pool", window_s=3600) == 4
    assert result.switches_in_window("pool", window_s=10) == 2  # two switches within 10 s
    assert result.device_wh("pool") == pytest.approx(5 * 750 * 5 / 3600)
    # on 1..3 (10 s), off 3..4 (5 s), on 4..7 (15 s)
    bad = result.hold_violations("pool", min_run_s=12, min_pause_s=8, tol_s=0)
    assert len(bad) == 2
    assert result.hold_violations("pool", min_run_s=12, min_pause_s=8, tol_s=5) == []
    ends = [result.scenario.start + timedelta(seconds=15)]  # the off at step 3 was a release
    assert (
        len(result.hold_violations("pool", min_run_s=12, min_pause_s=8, tol_s=0, exempt=ends)) == 1
    )


def test_import_is_integrated_over_a_window() -> None:
    result = synthetic([False] * 4)
    assert result.import_wh() == pytest.approx(2 * 100.0 * 5 / 3600)
    assert result.import_wh(start_s=10) == pytest.approx(100.0 * 5 / 3600)
    assert result.import_wh(end_s=5) == 0.0


def test_turns_on_reads_the_action_kinds() -> None:
    sc = load_scenario("water_heater_steps")
    runner = ScenarioRunner(sc, catalog())

    async def go() -> DeviceConfig:
        await runner.conn.connect()
        return build_device_configs(sc, catalog(), runner.sim_devices)[0]

    boiler = asyncio.run(go())
    assert turns_on(StepAction(step=2), boiler) is True
    assert turns_on(StepAction(step=0), boiler) is False
    assert turns_on(SwitchAction(on=False), boiler) is False
    assert turns_on(PowerSetpointAction(power_w=0.0), boiler) is False
    assert turns_on(PowerSetpointAction(power_w=500.0), boiler) is True


# --- running ---------------------------------------------------------------------------


def test_a_runner_reports_the_devices_of_the_scenario() -> None:
    sc = parse_scenario(SC)
    result, runner = run_scenario(sc)
    assert len(result.steps) == sc.steps
    assert set(result.devices) == {"pool", "free"}  # the logs cover every simulated device
    assert result.step_s == 5
    assert runner.stack is not None
    assert runner.stack.entity_ids
    assert result.configs["pool"].id == "pool"


def test_actions_run_at_their_step_in_order() -> None:
    sc = parse_scenario(SC)
    seen: list[tuple[str, float]] = []

    def setup(runner: ScenarioRunner) -> None:
        async def mark(name: str) -> None:
            seen.append((name, runner.vt.monotonic()))

        async def late(_: ScenarioRunner) -> None:
            await mark("late")

        async def early(_: ScenarioRunner) -> None:
            await mark("early")

        runner.at(30, late)
        runner.at(12, early)

    run_scenario(sc, setup=setup)
    assert seen == [("early", 15.0), ("late", 30.0)]  # the first step at or after the request


# --- demo mode: the demo devices are physical and the controller runs on them in shadow ---


def demo_runner(
    modes: dict[str, OperatingMode] | None, global_mode: OperatingMode
) -> ScenarioRunner:
    return ScenarioRunner(
        load_scenario("summer_day"),
        catalog(),
        configs=demo_devices(catalog()).devices,
        device_modes=modes,
        global_mode=global_mode,
    )


async def test_the_demo_shadow_run_makes_would_have_entries_and_writes_nothing() -> None:
    runner = demo_runner(None, OperatingMode.SHADOW)
    try:
        result = await runner.run()
    finally:
        await runner.close()
    assert runner.conn.call_log == []  # nothing reached the (fake) Home Assistant
    kinds = {d.kind for d in result.decisions}
    assert kinds == {DecisionKind.SHADOW}
    devices = {d.intent.asset_id for d in result.decisions}
    assert {AC_DEVICE_ID, ROD_DEVICE_ID} <= devices
    # the demo user ran the pump and the unit by the clock: the house really draws them
    assert max(result.devices[POOL_DEVICE_ID].power_w) == 750.0
    assert max(result.devices[AC_DEVICE_ID].power_w) > 200.0
    assert max(s.devices_w for s in result.steps) > 900.0  # the balance includes the devices


async def test_one_demo_device_can_be_switched_to_automatic_against_the_simulated_house() -> None:
    runner = demo_runner({AC_DEVICE_ID: OperatingMode.AUTOMATIC}, OperatingMode.AUTOMATIC)
    try:
        result = await runner.run()
    finally:
        await runner.close()
    written = {c.entity_ids for c in runner.conn.call_log if c.error is None}
    assert written == {("climate.sim_living_room_ac",)}  # only the one device, only its entity
    assert result.accepted(AC_DEVICE_ID)
    assert all(
        d.kind is DecisionKind.SHADOW for d in result.decisions if d.intent.asset_id != AC_DEVICE_ID
    )
    # the unit's set-point really moved in the simulated house, inside its comfort band
    shifted = [s for s in result.devices[AC_DEVICE_ID].setpoint_c if s is not None]
    assert min(shifted) < 24.0
    comfort = result.configs[AC_DEVICE_ID].comfort
    assert comfort is not None
    assert min(shifted) >= comfort.min_c


async def test_the_demo_stack_helper_wires_the_controller_on_the_fake() -> None:
    """What WP9 uses: a connected fake, the demo devices, a clock, a sleep, the modes."""
    from suncore.safety.modes import StaticModes  # noqa: PLC0415

    runner = ScenarioRunner(load_scenario("summer_day"), catalog())
    await runner.conn.connect()
    stack = demo_stack(
        runner.conn,
        demo_devices(catalog()).devices,
        clock=runner.vt.clock,
        sleep=runner.vt.sleep,
        modes=StaticModes(OperatingMode.SHADOW),
    )
    await stack.start()
    for _ in range(3):
        await runner.vt.advance_to(
            runner.vt.now + timedelta(seconds=60),
            events=runner.conn.next_event_time,
            run_events=runner.conn.run_events_until,
        )
        runner.conn.advance()
        await runner.vt.settle()
        result = await stack.tick()
    assert result is not None
    assert result.ts == runner.vt.now
    assert set(stack.estimates) == {AC_DEVICE_ID, POOL_DEVICE_ID, ROD_DEVICE_ID}
    assert "sensor.sim_living_room_ac_energy_current" in stack.watchdog._cadence
    await stack.shutdown()
    await runner.close()
