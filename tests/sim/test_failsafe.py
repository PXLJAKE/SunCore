# SPDX-License-Identifier: Apache-2.0
"""Fail-safe paths end in the baseline state: shutdown, crash, emergency stop, mode change.

One house with plenty of surplus and one device of every control kind (on/off, climate set-point,
stepped select, continuous number). SunCore takes all of them over within minutes; the tests then
stop it in different ways and look at the *physical* devices: the state at takeover (the baseline,
plan question 2) must be back, nothing is written afterwards, and the persisted runtime says the
devices are no longer under control. Everything runs in simulated time.
"""

from __future__ import annotations

from typing import Any

import pytest

from suncore.core.decisions import DecisionKind, OperatingMode, ReleaseAction
from suncore.sim.runner import RunResult, ScenarioRunner
from suncore.sim.scenario import parse_scenario
from tests.sim.helpers import catalog

pytestmark = pytest.mark.simulator

TEXT = """
name: failsafe
description: plenty of surplus, one device of every control kind
start: "2026-07-15T11:00:00+02:00"
duration_s: 3000
step_s: 5
house:
  seed: 5
  pv: {kwp: 12.0}
  load: {noise_fraction: 0.02}
  battery: {capacity_wh: 0}
outdoor: {mean_c: 29.0, amplitude_c: 0.0}
devices:
  - id: pool
    template: generic.switch
    physics: {kind: switch, nominal_w: 750, power_sensor: true}
    control:
      priority: 10
      min_run_s: 300
      min_pause_s: 300
      power_model: {kind: measured, power_role: power, nominal_w: 750}
  - id: ac
    template: lg.thinq_climate
    physics:
      kind: climate
      initial_mode: cool
      initial_setpoint_c: 24
      initial_temp_c: 24.5
      default_on_mode: cool
      nominal_w: 900
      ua_w_per_k: 250
      internal_gain_w: 400
      power_sensor: true
      power_poll_s: 10
    control:
      priority: 20
      comfort: {min_c: 21, max_c: 27}
      power_model: {kind: measured, power_role: power, nominal_w: 900}
  - id: boiler
    template: generic.water_heater_steps
    physics:
      {kind: water_heater, variant: select, initial_temp_c: 36, max_temp_c: 60, power_sensor: true}
    control:
      priority: 30
      comfort: {min_c: 30, max_c: 62}
      power_model: {kind: measured, power_role: power, nominal_w: 3000}
  - id: rod
    template: generic.number_load
    physics: {kind: number, max: 3000, step: 100, power_sensor: true}
    control:
      priority: 40
      power_model: {kind: measured, power_role: power, nominal_w: 3000}
"""
DEVICES = ("pool", "ac", "boiler", "rod")
TAKEOVER_S = 1200
"""By now every device has been taken over."""
SETTLED_S = 10.0
"""A command reaches a cloud-connected unit about a second after the service call returned."""


def snapshot(runner: ScenarioRunner) -> dict[str, Any]:
    """The physical state of every device (what the user would see)."""
    d = runner.sim_devices
    return {
        "pool": d["pool"].on,  # type: ignore[attr-defined]
        "ac": (d["ac"].mode, d["ac"].setpoint_c),  # type: ignore[attr-defined]
        "boiler": d["boiler"].level,  # type: ignore[attr-defined]
        "rod": d["rod"].value,  # type: ignore[attr-defined]
    }


def make() -> ScenarioRunner:
    return ScenarioRunner(parse_scenario(TEXT), catalog())


async def prepared() -> tuple[ScenarioRunner, dict[str, Any]]:
    runner = make()
    await runner.start()
    return runner, snapshot(runner)


def controlled(result: RunResult, at_s: float) -> dict[str, bool]:
    """Which devices differ from the baseline at ``at_s`` (SunCore had taken them over)."""
    i = int(at_s // result.step_s)
    out = {
        "pool": result.devices["pool"].on[i],
        "ac": result.devices["ac"].setpoint_c[i] != 24.0,
        "boiler": result.devices["boiler"].on[i],
        "rod": result.devices["rod"].on[i],
    }
    return {k: bool(v) for k, v in out.items()}


def accepted_writes_after(result: RunResult, at_s: float) -> list[Any]:
    return [
        d
        for d in result.accepted()
        if not isinstance(d.intent.action, ReleaseAction)
        and (d.intent.ts - result.scenario.start).total_seconds() >= at_s
    ]


async def test_the_controller_takes_over_every_kind_of_device() -> None:
    """The premise of the other tests: by TAKEOVER_S all four devices differ from the baseline."""
    runner, baseline = await prepared()
    try:
        result = await runner.run(TAKEOVER_S + 60)
    finally:
        await runner.close()
    assert controlled(result, TAKEOVER_S) == dict.fromkeys(DEVICES, True)
    assert snapshot(runner) != baseline


async def test_shutdown_releases_every_device_to_the_baseline_within_the_deadline() -> None:
    runner, baseline = await prepared()
    seen: dict[str, Any] = {}

    async def stop(r: ScenarioRunner) -> None:
        seen["before"] = snapshot(r)
        started = r.vt.now
        assert r.stack is not None
        await r.stack.shutdown()
        seen["took_s"] = (r.vt.now - started).total_seconds()
        await r.vt.sleep(SETTLED_S)  # the last commands reach the devices (cloud latency)
        seen["after"] = snapshot(r)
        seen["calls"] = len(r.conn.call_log)

    runner.at(TAKEOVER_S, stop)
    try:
        result = await runner.run(TAKEOVER_S + 600)
    finally:
        await runner.close()
    assert seen["before"] != baseline
    assert seen["after"] == baseline
    assert seen["took_s"] <= runner.settings.release_deadline_s
    # every device under control got an accepted release for the shutdown ...
    for device_id in DEVICES:
        rel = [d for d in result.accepted(device_id) if isinstance(d.intent.action, ReleaseAction)]
        assert [r.key for r in rel[-1].reasons][:2] == ["safety.released", "safety.shutdown"]
        runtime = runner.stack.gate.runtime_of(device_id)  # type: ignore[union-attr]
        assert runtime is not None
        assert not runtime.under_control
    # ... and the process is gone: no tick, no write, no service call after the shutdown
    assert accepted_writes_after(result, TAKEOVER_S) == []
    assert len(runner.conn.call_log) == seen["calls"]
    assert snapshot(runner) == baseline


async def test_a_crash_leaves_the_devices_and_the_restart_releases_them() -> None:
    runner, baseline = await prepared()
    seen: dict[str, Any] = {}

    async def crash(r: ScenarioRunner) -> None:
        assert r.stack is not None
        await r.stack.crash()
        seen["crashed"] = snapshot(r)
        seen["under_control"] = [
            rt.device_id for rt in await r.store.all_runtimes() if rt.under_control
        ]

    async def restart(r: ScenarioRunner) -> None:
        stack = r.new_stack()
        await stack.start()  # recover() releases what the crash left under control
        await r.vt.sleep(SETTLED_S)
        seen["recovered"] = snapshot(r)
        r.stack = stack  # only now does the loop tick it

    runner.at(TAKEOVER_S, crash)
    runner.at(TAKEOVER_S + 90, restart)
    try:
        result = await runner.run(TAKEOVER_S + 900)
    finally:
        await runner.close()
    assert seen["crashed"] != baseline  # nobody released anything: the kill left them as they were
    assert sorted(seen["under_control"]) == sorted(DEVICES)  # ... and the store knows
    assert seen["recovered"] == baseline  # recover() at start-up: urgent release of what was ours
    for device_id in DEVICES:
        rel = [d for d in result.accepted(device_id) if isinstance(d.intent.action, ReleaseAction)]
        assert rel
        assert "safety.crash_recovery" in [r.key for r in rel[0].reasons]
    # nothing was written between the kill and the restart
    gap = [
        c
        for c in runner.conn.call_log
        if TAKEOVER_S + 5 < (c.ts - result.scenario.start).total_seconds() < TAKEOVER_S + 90
    ]
    assert gap == []
    # the restarted controller is a normal controller again: it takes over after recovery
    assert accepted_writes_after(result, TAKEOVER_S + 90) != []


async def test_emergency_stop_releases_at_once_blocks_writes_and_survives_a_crash() -> None:
    runner, baseline = await prepared()
    seen: dict[str, Any] = {}
    stop_at, clear_at = TAKEOVER_S, TAKEOVER_S + 600

    async def stop(r: ScenarioRunner) -> None:
        assert r.stack is not None
        started = r.vt.now
        await r.stack.gate.emergency_stop()
        seen["took_s"] = (r.vt.now - started).total_seconds()
        await r.vt.sleep(SETTLED_S)
        seen["after"] = snapshot(r)
        seen["persisted"] = await r.store.load_emergency_stop()

    async def clear(r: ScenarioRunner) -> None:
        assert r.stack is not None
        await r.stack.gate.clear_emergency_stop()
        seen["persisted_cleared"] = await r.store.load_emergency_stop()

    runner.at(stop_at, stop)
    runner.at(clear_at, clear)
    try:
        result = await runner.run(clear_at + 900)
    finally:
        await runner.close()
    assert seen["after"] == baseline
    assert seen["took_s"] <= runner.settings.release_deadline_s
    assert seen["persisted"] is True
    assert seen["persisted_cleared"] is False
    # while stopped nothing is written and every device stays at the baseline (physically)
    assert accepted_writes_after(result, stop_at + 30) != []  # (after the clear: it resumes)
    window = [
        d
        for d in accepted_writes_after(result, stop_at)
        if (d.intent.ts - result.scenario.start).total_seconds() < clear_at
    ]
    assert window == []
    i = int((clear_at - 60) // result.step_s)
    assert not any(result.devices["pool"].on[int(stop_at // 5) + 10 : i])
    assert controlled(result, stop_at + 300) == dict.fromkeys(DEVICES, False)
    # after the clear the controller takes over again (the modes were never touched)
    assert controlled(result, clear_at + 800) != dict.fromkeys(DEVICES, False)


async def test_an_emergency_stop_is_still_in_force_after_a_restart() -> None:
    runner, baseline = await prepared()
    seen: dict[str, Any] = {}

    async def stop(r: ScenarioRunner) -> None:
        assert r.stack is not None
        await r.stack.gate.emergency_stop()
        await r.stack.crash()

    async def restart(r: ScenarioRunner) -> None:
        stack = await r.restart()
        seen["stopped"] = stack.gate.emergency_stopped

    runner.at(TAKEOVER_S, stop)
    runner.at(TAKEOVER_S + 60, restart)
    try:
        result = await runner.run(TAKEOVER_S + 600)
    finally:
        await runner.close()
    assert seen["stopped"] is True  # recover() loads the persisted stop
    assert accepted_writes_after(result, TAKEOVER_S) == []
    assert snapshot(runner) == baseline


async def test_switching_to_shadow_releases_after_the_protection_time() -> None:
    """Non-urgent release (plan section 3): writing stops at once, the baseline comes back
    when the pump's minimum run time has elapsed - never by chopping a running compressor."""
    runner, baseline = await prepared()
    seen: dict[str, Any] = {}
    change_at = 150  # the pump started at about 65 s: its min_run_s (300) is not over

    async def to_shadow(r: ScenarioRunner) -> None:
        assert r.stack is not None
        r.modes.set_global(OperatingMode.SHADOW)
        await r.stack.gate.sync_modes()
        seen["pool_on_right_after"] = r.sim_devices["pool"].is_on()
        seen["ac_right_after"] = r.sim_devices["ac"].setpoint_c  # type: ignore[attr-defined]

    runner.at(change_at, to_shadow)
    try:
        result = await runner.run(900)
    finally:
        await runner.close()
    assert seen["pool_on_right_after"] is True  # protected: min run not over
    pool_off = next(i for i, on in result.transitions("pool") if not on) * result.step_s
    pool_on = next(i for i, on in result.transitions("pool") if on) * result.step_s
    assert pool_off - pool_on >= 300 - 15  # released after its minimum run time
    assert pool_off < 600
    assert snapshot(runner) == baseline
    # no write after the mode change: the controller only produces "would have" entries
    assert accepted_writes_after(result, change_at + 1) == []
    assert result.of_kind(DecisionKind.SHADOW)
