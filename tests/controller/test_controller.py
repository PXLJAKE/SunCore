# SPDX-License-Identifier: Apache-2.0
"""``Controller.step`` and ``ControllerLoop``: gate inputs, intents on change, the tick's I/O."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta

import pytest

from suncore.controller import (
    ConfigQuantiser,
    Controller,
    ControllerInputs,
    ControllerLoop,
    DeviationEvent,
    Phase,
)
from suncore.controller import reasons as rs
from suncore.core.config import BatteryTarget, GoalsConfig
from suncore.core.control import (
    ControlSettings,
    DeviceBand,
    DeviceState,
    Plan,
    PlanInterval,
    PowerEstimate,
)
from suncore.core.decisions import (
    Decision,
    DecisionKind,
    Intent,
    OperatingMode,
    Reason,
    SetpointAction,
    SwitchAction,
)
from suncore.core.devices import TimeWindow
from suncore.core.signals import Quality
from tests.controller.sim import Battery, build_sim
from tests.safety.helpers import T0, climate_device, flows, steps_device, switch_device

GOALS = GoalsConfig(climate_before_export=True)
POOL = switch_device("pool", nominal_w=1000)


def inputs(
    grid_w: float = -2000.0,
    *,
    on: bool = False,
    estimate: PowerEstimate | None = None,
    **kw: object,
) -> ControllerInputs:
    est = estimate or PowerEstimate(value_w=1000.0 if on else 0.0, uncertainty_w=10.0)
    return ControllerInputs(
        flows=flows(grid_w=grid_w),
        states={"pool": DeviceState(on=on)},
        estimates={"pool": est},
        goals=GOALS,
        **kw,  # type: ignore[arg-type]
    )


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def decide(intent: Intent, kind: DecisionKind = DecisionKind.ACCEPTED) -> Decision:
    return Decision(intent=intent, kind=kind, ts=intent.ts, mode=OperatingMode.AUTOMATIC)


# --- Controller.step -------------------------------------------------------------------


def test_step_needs_an_aware_clock() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Controller([POOL]).step(datetime(2026, 1, 1), inputs())  # noqa: DTZ001


def test_gate_inputs_are_stamped_and_carry_the_free_surplus() -> None:
    controller = Controller([POOL])
    result = controller.step(T0, inputs(import_limit_w=5000.0, reserve_soc_pct=20.0))
    gi = result.gate_inputs
    assert gi.ts == T0
    assert gi.import_limit_w == 5000.0
    assert gi.reserve_soc_pct == 20.0
    assert gi.estimates["pool"].value_w == 0.0
    assert gi.surplus is not None
    # free = export 2000 - reserve 100 - committed 0
    assert gi.surplus.value_w == 1900.0
    assert gi.surplus.quality is Quality.OK
    assert result.free_w == 1900.0
    assert result.total_w == 1900.0


def test_free_surplus_excludes_what_runs_but_total_includes_it() -> None:
    controller = Controller([POOL])
    result = controller.step(T0, inputs(-1000.0, on=True))
    assert result.total_w == 1900.0  # export 1000 + pool 1000 - reserve
    assert result.free_w == 900.0
    assert result.gate_inputs.surplus is not None
    assert result.gate_inputs.surplus.value_w == 900.0


def test_intents_only_on_change() -> None:
    controller = Controller([POOL])
    sent: list[Intent] = []
    for i in range(30):
        now = at(5 * i)
        result = controller.step(now, inputs(on=bool(sent)))
        for intent in result.intents:
            sent.append(intent)
            controller.report(decide(intent))
    assert len(sent) == 1
    assert sent[0].action == SwitchAction(on=True)
    assert sent[0].ts == at(60)  # after the 60 s hysteresis delay
    assert sent[0].reasons[0].key == rs.SURPLUS_ON
    view = controller.step(at(200), inputs(on=True)).views["pool"]
    assert (view.phase, view.level, view.top) == (Phase.STEADY, 1, 1)
    assert view.committed_w == 1000.0


def test_an_unanswered_intent_stays_in_flight() -> None:
    controller = Controller([POOL])
    first: list[Intent] = []
    for i in range(14):
        first += controller.step(at(5 * i), inputs()).intents
    assert len(first) == 1
    view = controller.step(at(70), inputs()).views["pool"]
    assert view.phase is Phase.IN_FLIGHT
    assert not controller.report(
        decide(first[0]).model_copy(
            update={"intent": first[0].model_copy(update={"asset_id": "x"})}
        )
    )


def test_unknown_surplus_proposes_nothing() -> None:
    controller = Controller([POOL])
    for i in range(30):
        result = controller.step(at(5 * i), dataclasses.replace(inputs(), flows=None))
        assert result.intents == ()
        assert result.gate_inputs.surplus is None
        assert result.total_w is None
    assert result.views["pool"].reasons == (rs.plain(rs.SURPLUS_UNKNOWN),)


def test_blocked_and_unknown_devices_are_left_alone() -> None:
    controller = Controller([POOL])
    held = Reason(key="safety.override_held")
    result = controller.step(T0, inputs(blocked={"pool": held}))
    assert result.views["pool"].phase is Phase.IDLE
    assert result.views["pool"].reasons == (held,)
    unknown = dataclasses.replace(
        inputs(), states={"pool": DeviceState(on=None, quality=Quality.UNAVAILABLE)}
    )
    result = controller.step(at(5), unknown)
    assert result.views["pool"].reasons == (rs.plain(rs.STATE_UNKNOWN),)
    result = controller.step(at(10), dataclasses.replace(inputs(), states={}))
    assert result.views["pool"].phase is Phase.IDLE


def test_climate_reasons_when_not_controllable() -> None:
    ac = climate_device("ac")
    controller = Controller([ac])
    off = ControllerInputs(
        flows=flows(), states={"ac": DeviceState(on=False, mode="off", setpoint_c=21.0)}
    )
    assert controller.step(T0, off).views["ac"].reasons == (rs.plain(rs.CLIMATE_OFF),)
    fan = ControllerInputs(
        flows=flows(), states={"ac": DeviceState(on=True, mode="fan_only", setpoint_c=21.0)}
    )
    assert controller.step(at(5), fan).views["ac"].reasons == (
        rs.climate_mode_unsupported("fan_only"),
    )
    outside = ControllerInputs(
        flows=flows(), states={"ac": DeviceState(on=True, mode="heat", setpoint_c=18.0)}
    )
    assert climate_device("ac").comfort is not None
    assert Controller([ac]).step(at(10), outside).views["ac"].reasons == (
        rs.base_outside_comfort(18.0, 20.0, 26.0),
    )
    measured = switch_device("plug").model_copy(
        update={"power_model": POOL.power_model.model_copy(update={"nominal_w": None})}
    )
    controller = Controller([measured])
    result = controller.step(
        T0, ControllerInputs(flows=flows(), states={"plug": DeviceState(on=False)})
    )
    assert result.views["plug"].reasons == (rs.plain(rs.POWER_UNKNOWN),)


def test_battery_target_keeps_the_charging_power_for_the_battery() -> None:
    goals = GoalsConfig(battery_target=BatteryTarget(soc_pct=80, time="18:00"))
    controller = Controller([POOL])
    charging = ControllerInputs(
        flows=flows(grid_w=0.0, battery_w=2500.0, soc_pct=40.0),
        states={"pool": DeviceState(on=False)},
        estimates={"pool": PowerEstimate(value_w=0.0, uncertainty_w=5.0)},
        goals=goals,
    )
    for i in range(30):
        result = controller.step(at(5 * i), charging)
        assert result.intents == ()
    reasons = result.views["pool"].reasons
    assert reasons[0].key == rs.NO_SURPLUS
    assert reasons[-1] == rs.battery_target(40.0, 80)
    # Reached: the same charging power is now available.
    reached = dataclasses.replace(charging, flows=flows(grid_w=0.0, battery_w=2500.0, soc_pct=85.0))
    sent = [i for k in range(30) for i in controller.step(at(200 + 5 * k), reached).intents]
    assert [i.action for i in sent] == [SwitchAction(on=True)]


def test_plan_band_caps_and_blocks() -> None:
    heater = steps_device("heater")
    est = PowerEstimate(value_w=0.0, uncertainty_w=5.0)
    plan = Plan(
        intervals=(
            PlanInterval(
                start=T0,
                end=T0 + timedelta(hours=1),
                devices={"heater": DeviceBand(max_power_w=1500.0)},
            ),
            PlanInterval(
                start=T0 + timedelta(hours=1),
                end=T0 + timedelta(hours=2),
                devices={"heater": DeviceBand(allowed=False)},
            ),
        )
    )
    controller = Controller([heater])
    step = 0

    def tick(now: datetime) -> list[Intent]:
        nonlocal step
        state = DeviceState(step=step)
        result = controller.step(
            now,
            ControllerInputs(
                flows=flows(grid_w=-6000.0),
                states={"heater": state},
                estimates={"heater": est},
                goals=GOALS,
                plan=plan,
            ),
        )
        for intent in result.intents:
            controller.report(decide(intent))
            step = intent.action.step  # type: ignore[union-attr]
        return list(result.intents)

    sent = [i for k in range(30) for i in tick(at(5 * k))]
    assert [i.action.step for i in sent] == [1]  # type: ignore[union-attr]
    sent = [i for k in range(5) for i in tick(at(3600 + 5 * k))]
    # Forced at once (no delay) when the plan forbids the device.
    assert [i.action.step for i in sent] == [0]  # type: ignore[union-attr]
    assert sent[0].ts == at(3600)
    assert sent[0].reasons[0].key == rs.PLAN_BLOCKED


def test_quiet_hours_switch_off_without_delay() -> None:
    quiet = TimeWindow(start=time(12, 30), end=time(13, 0), mode="block")
    pool = POOL.model_copy(update={"windows": (quiet,)})
    sim = build_sim([pool], pv=3000.0)
    sim.run_sync(1800)
    assert sim.devices["pool"].state.on
    sim.run_sync(10)
    assert not sim.devices["pool"].state.on
    off = sim.accepted("pool")[-1]
    assert off.ts == T0 + timedelta(minutes=30)
    assert off.intent.reasons[0].key == rs.QUIET_HOURS
    sim.run_sync(1700)
    assert not sim.devices["pool"].state.on  # 12:58:30
    sim.run_sync(180)
    assert sim.devices["pool"].state.on  # back after the quiet hours and the delay


def test_shadow_mode_logs_would_have_once_per_change() -> None:
    heater = steps_device("heater").model_copy(update={"priority": 1})
    sim = build_sim([POOL, heater], pv=3800.0, mode=OperatingMode.SHADOW)
    sim.run_sync(3600)
    kinds = {d.kind for d in sim.decisions}
    assert kinds == {DecisionKind.SHADOW}
    assert len(sim.decisions) == 2  # one "would have" per device, not one per tick
    assert not sim.devices["pool"].state.on  # nothing written
    views = sim.loop.views
    assert (views["pool"].level, views["heater"].level) == (1, 1)  # the virtual levels


def test_assisted_proposals_are_not_repeated_while_valid() -> None:
    sim = build_sim([POOL], pv=3000.0, mode=OperatingMode.ASSISTED)
    sim.run_sync(600)
    proposed = [d for d in sim.decisions if d.kind is DecisionKind.PROPOSED]
    assert len(proposed) == 1


def test_climate_shifts_up_and_returns_to_base_when_the_surplus_goes() -> None:
    battery = Battery(soc_pct=60.0)

    def pv(now: datetime) -> float:
        return 3000.0 if (now - T0).total_seconds() < 1800 else 400.0

    sim = build_sim([climate_device("ac", nominal_w=800)], pv=pv, battery=battery)
    sim.run_sync(1800)
    assert sim.devices["ac"].state.setpoint_c == 23.0
    sim.run_sync(1800)
    assert sim.devices["ac"].state.setpoint_c == 21.0
    setpoints = [
        d.intent.action.setpoint_c
        for d in sim.accepted("ac")
        if isinstance(d.intent.action, SetpointAction)
    ]
    assert setpoints == [21.5, 22.0, 22.5, 23.0, 22.5, 22.0, 21.5, 21.0]
    keys = [d.intent.reasons[0].key for d in sim.accepted("ac")]
    assert keys[:4] == [rs.SETPOINT_SHIFT] * 4
    assert set(keys[4:]) <= {
        rs.SETPOINT_BASE,
        rs.SETPOINT_SHIFT,
        rs.DEFICIT_BACKOFF,
        rs.DEFICIT_BUDGET,
    }
    assert sim.rejected() == []


def test_climate_switch_on_with_the_opt_in() -> None:
    ac = climate_device("ac", nominal_w=800, allow_switch_on=True)
    sim = build_sim(
        [ac], pv=3000.0, states={"ac": DeviceState(on=False, mode="off", setpoint_c=24.0)}
    )
    sim.run_sync(600)
    first = sim.accepted("ac")[0]
    assert first.intent.action == SwitchAction(on=True)
    assert first.intent.reasons[0].key == rs.SWITCH_ON
    assert sim.devices["ac"].state.mode == "cool"
    assert sim.devices["ac"].state.setpoint_c is not None
    assert sim.devices["ac"].state.setpoint_c < 24.0  # cooling shifts down


def test_a_confirmed_write_that_reality_undoes_is_a_deviation() -> None:
    events: list[DeviationEvent] = []
    sim = build_sim([POOL], pv=3000.0)
    sim.loop._on_deviation = events.append
    sim.run_sync(120)
    assert sim.devices["pool"].state.on
    sim.devices["pool"].state = DeviceState(on=False)  # e.g. the device's own timer
    sim.run_sync(60)
    assert [e.kind for e in events] == ["state_mismatch"]


def test_set_devices_keeps_the_protection_history() -> None:
    pool = switch_device("pool", nominal_w=1000, min_pause_s=600)
    controller = Controller([pool])
    machine = controller.allocator.machine("pool")
    assert machine is not None
    machine.last_off_ts = T0
    controller.set_devices([pool.model_copy(update={"name": "Pool 2"})])
    fresh = controller.allocator.machine("pool")
    assert fresh is not machine
    assert fresh is not None
    assert fresh.last_off_ts == T0
    controller.set_devices([])
    assert controller.allocator.machines == {}


def test_an_injected_quantiser_is_used() -> None:
    class Coarse(ConfigQuantiser):
        def setpoint_step_c(self, device_id: str) -> float | None:  # noqa: ARG002
            return 1.0

    ac = climate_device("ac")
    controller = Controller([ac], quantiser=Coarse())
    controller.set_devices([ac])
    state = DeviceState(on=True, mode="heat", setpoint_c=21.0)
    est = PowerEstimate(value_w=300.0, uncertainty_w=10.0)
    sent: list[Intent] = []
    for i in range(20):
        result = controller.step(
            at(5 * i),
            ControllerInputs(
                flows=flows(grid_w=-3000.0),
                states={"ac": state},
                estimates={"ac": est},
                goals=GOALS,
            ),
        )
        sent += result.intents
    assert sent
    assert sent[0].action == SetpointAction(setpoint_c=22.0, base_c=21.0)
    assert controller.settings == ControlSettings()


# --- ControllerLoop --------------------------------------------------------------------


def test_loop_submits_reports_and_polls_every_tick() -> None:
    polls: list[datetime] = []
    submitted: list[Intent] = []
    controller = Controller([POOL])

    async def submit(intent: Intent) -> Decision:
        submitted.append(intent)
        return decide(intent)

    async def poll() -> Sequence[Decision]:
        polls.append(controller.allocator.machines["pool"].since or T0)
        return ()

    loop = ControllerLoop(controller, submit=submit, poll=poll)
    assert loop.last is None
    assert loop.gate_inputs is None
    assert loop.views == {}
    assert loop.controller is controller

    async def go() -> None:
        for i in range(20):
            await loop.tick(at(5 * i), inputs(on=bool(submitted)))
            await loop.drain()

    asyncio.run(go())
    assert len(polls) == 20
    assert len(submitted) == 1
    assert loop.gate_inputs is not None
    assert loop.gate_inputs.ts == at(95)
    assert loop.views["pool"].level == 1


def test_loop_survives_a_failing_submit_and_close_cancels() -> None:
    controller = Controller([POOL])
    events: list[DeviationEvent] = []

    async def submit(intent: Intent) -> Decision:
        raise RuntimeError("boom")

    async def slow(intent: Intent) -> Decision:
        await asyncio.sleep(3600)
        return decide(intent)

    async def go() -> None:
        loop = ControllerLoop(controller, submit=submit, on_deviation=events.append)
        for i in range(14):
            await loop.tick(at(5 * i), inputs())
        await loop.drain()
        machine = controller.allocator.machines["pool"]
        assert machine.in_flight_id is not None  # until the machine's timeout
        other = ControllerLoop(Controller([POOL]), submit=slow)
        for i in range(14):
            await other.tick(at(5 * i), inputs())
        await other.close()

    asyncio.run(go())


def test_deviations_reach_the_hook() -> None:
    events: list[DeviationEvent] = []
    sim = build_sim([POOL], pv=2000.0, battery=Battery(soc_pct=60.0))
    sim.loop._on_deviation = events.append
    sim.run_sync(120)
    assert sim.devices["pool"].state.on
    sim.pv = lambda _now: 300.0
    sim.run_sync(120)
    assert any(e.kind == "deficit_backoff" and e.device_id == "pool" for e in events)


def test_stale_flows_do_not_add_load() -> None:
    controller = Controller([POOL])
    snapshot = flows(grid_w=-3000.0)
    stale = snapshot.__class__(
        grid_w=snapshot.grid_w.__class__(-3000.0, Quality.STALE),
        pv_w=snapshot.pv_w,
        battery_w=snapshot.battery_w,
        house_w=snapshot.house_w,
        battery_soc_pct=snapshot.battery_soc_pct,
        ts=T0,
    )
    for i in range(30):
        result = controller.step(at(5 * i), dataclasses.replace(inputs(), flows=stale))
        assert result.intents == ()
    assert rs.plain(rs.SURPLUS_UNKNOWN) in result.views["pool"].reasons


def test_tz_is_used_for_windows() -> None:
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    berlin = ZoneInfo("Europe/Berlin")
    # 12:00 UTC = 14:00 in Berlin (CEST): a 14-15 local window is open.
    window = TimeWindow(start=time(14), end=time(15))
    pool = POOL.model_copy(update={"windows": (window,)})
    assert Controller([pool], tz=berlin).step(T0, inputs()).views["pool"].reasons[0].key != (
        rs.OUTSIDE_WINDOW
    )
    assert Controller([pool], tz=UTC).step(T0, inputs()).views["pool"].reasons[0].key == (
        rs.OUTSIDE_WINDOW
    )
