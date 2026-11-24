# SPDX-License-Identifier: Apache-2.0
"""The ``evcc_actor`` scenario of PR 6-J, closed-loop (plan WP10): evcc as actor on the fake evcc.

Judged by what evcc's topics show and the gate's decisions: SunCore sets mode, ``maxCurrent`` and
``limitSoc`` only (never ``site/*``), evcc follows the surplus by itself, nothing is imported for
the car, shadow writes nothing, a silent evcc is never written and its release is retried, the
emergency stop restores evcc's own values, and a mode change in evcc's own app is respected.
"""

from __future__ import annotations

import asyncio
import itertools

import pytest

from suncore.core.decisions import (
    ActionKind,
    DecisionKind,
    LoadpointAction,
    OperatingMode,
    ReleaseAction,
)
from suncore.sim.devices import SimLoadpoint
from suncore.sim.runner import ScenarioRunner
from suncore.sim.scenario import ManualEvent, load_scenario
from tests.sim.helpers import catalog, caused_import_wh, run_named

pytestmark = pytest.mark.simulator

NAME = "evcc_actor"
ALLOWED_TOPICS = {
    "evcc/loadpoints/1/mode/set",
    "evcc/loadpoints/1/maxCurrent/set",
    "evcc/loadpoints/1/limitSoc/set",
}
MIN_3PH_W = 6 * 230 * 3


def loadpoint(runner: ScenarioRunner) -> SimLoadpoint:
    sim = runner.sim_devices["evcc_lp"]
    assert isinstance(sim, SimLoadpoint)
    return sim


def test_the_loadpoint_is_set_to_smart_once_and_nothing_is_rejected() -> None:
    result, runner = run_named(NAME)
    assert result.of_kind(DecisionKind.REJECTED) == []
    assert result.of_kind(DecisionKind.FAILED) == []
    accepted = result.accepted("evcc_lp")
    assert [d.intent.action for d in accepted] == [
        LoadpointAction(mode="smart", max_current_a=16.0)
    ]
    assert [r.key for r in accepted[0].intent.reasons] == ["controller.evcc.smart"]
    assert loadpoint(runner).mode == "smart"


def test_only_mode_max_current_and_limit_of_the_loadpoint_are_ever_sent() -> None:
    _, runner = run_named(NAME)
    assert runner.evcc.sent
    assert runner.evcc.refused == []
    for topic, _ in runner.evcc.sent:
        assert topic in ALLOWED_TOPICS, topic
        assert "/site/" not in topic


def test_evcc_follows_the_surplus_and_nothing_is_imported_for_the_car() -> None:
    result, runner = run_named(NAME)
    caused, moved = caused_import_wh(NAME)
    assert moved > 5_000  # the car took > 5 kWh of PV
    assert caused <= 0.03 * moved
    power = result.devices["evcc_lp"].power_w
    assert all(w == 0 or w >= MIN_3PH_W - 1 for w in power)  # evcc's own 6 A minimum
    depart = int(12_600 // result.step_s)
    assert all(w == 0 for w in power[depart + 12 :])  # the car left: nothing is drawn
    assert loadpoint(runner).soc_pct > 25


def test_shadow_writes_nothing() -> None:
    result, runner = run_named(NAME, shadow=True)
    assert result.accepted() == []
    assert runner.evcc.sent == []
    assert loadpoint(runner).mode == "off"
    assert loadpoint(runner).charged_wh == 0


def test_a_silent_evcc_is_never_written_and_the_release_is_retried_with_backoff() -> None:
    async def go() -> ScenarioRunner:
        runner = ScenarioRunner(load_scenario(NAME), catalog())

        async def silent(r: ScenarioRunner) -> None:
            r.evcc.silent = True

        async def back(r: ScenarioRunner) -> None:
            r.evcc.silent = False

        runner.at(3600, silent)
        runner.at(5400, back)
        try:
            await runner.run()
        finally:
            await runner.close()
        return runner

    runner = asyncio.run(go())
    start = runner.scenario.start
    failed = [d for d in runner.decisions if d.kind is DecisionKind.FAILED]
    assert failed, "the release must be tried (and refused) while evcc is silent"
    assert {d.intent.action.kind for d in failed} == {ActionKind.RELEASE}
    assert all("safety.watchdog_unavailable" in [r.key for r in d.reasons] for d in failed)
    times = sorted((d.ts - start).total_seconds() for d in failed)
    assert 3600 <= times[0] <= 3600 + 300  # the heartbeat ages out after 120 s
    assert times[-1] <= 5400  # nothing is tried after evcc is back but the successful retry
    assert len(failed) <= 10  # B1: back-off, not one failure per 5 s tick (about 360)
    gaps = [b - a for a, b in itertools.pairwise(times)]
    assert gaps == sorted(gaps)  # the back-off only grows
    # C1: the retry that follows evcc's return succeeds and restores evcc's own values
    released = [
        d
        for d in runner.decisions
        if d.kind is DecisionKind.ACCEPTED and isinstance(d.intent.action, ReleaseAction)
    ]
    assert len(released) == 1
    assert (released[0].ts - start).total_seconds() >= 5400
    sent = runner.evcc.sent
    assert [t for t, _ in sent[:2]] == [
        "evcc/loadpoints/1/maxCurrent/set",
        "evcc/loadpoints/1/mode/set",
    ]
    # nothing was written while silent: the next write is the release that restores the mode
    assert sent[2] == ("evcc/loadpoints/1/mode/set", "off")
    assert not runner.evcc.refused or all(t in ALLOWED_TOPICS for t, _ in runner.evcc.refused)


def test_a1_the_emergency_stop_restores_the_mode_without_a_limit_soc_topic() -> None:
    """Release review A1: evcc shows no limitSoc; the stop must still restore mode and current."""

    async def go() -> ScenarioRunner:
        runner = ScenarioRunner(load_scenario(NAME), catalog())

        async def drift(r: ScenarioRunner) -> None:
            loadpoint(r).hide_limit_soc = True
            r.evcc._values.pop("evcc/loadpoints/1/limitSoc", None)

        async def stop(r: ScenarioRunner) -> None:
            assert r.stack is not None
            await r.stack.gate.emergency_stop()

        runner.at(7200, drift)
        runner.at(7300, stop)
        try:
            await runner.run()
        finally:
            await runner.close()
        return runner

    runner = asyncio.run(go())
    sim = loadpoint(runner)
    assert (sim.mode, sim.max_current_a) == ("off", 10.0)


def test_the_emergency_stop_restores_evccs_own_values() -> None:
    async def go() -> ScenarioRunner:
        runner = ScenarioRunner(load_scenario(NAME), catalog())

        async def stop(r: ScenarioRunner) -> None:
            assert r.stack is not None
            await r.stack.gate.emergency_stop()

        runner.at(7200, stop)
        try:
            await runner.run()
        finally:
            await runner.close()
        return runner

    runner = asyncio.run(go())
    sim = loadpoint(runner)
    assert (sim.mode, sim.max_current_a, sim.limit_soc_pct) == ("off", 10.0, 90)
    releases = [d for d in runner.decisions if isinstance(d.intent.action, ReleaseAction)]
    assert [d.kind for d in releases] == [DecisionKind.ACCEPTED]
    power = runner.result().devices["evcc_lp"].power_w[(7200 // 5) + 24 : (12_600 // 5)]
    assert all(w == 0 for w in power)  # evcc stopped charging after the release


def test_a_mode_change_in_evccs_own_app_is_respected() -> None:
    scenario = load_scenario(NAME).model_copy(
        update={
            "manual": (
                ManualEvent(at_s=5000, device="evcc_lp", action="select_option", value="off"),
            )
        }
    )

    async def go() -> ScenarioRunner:
        runner = ScenarioRunner(scenario, catalog())
        try:
            await runner.run()
        finally:
            await runner.close()
        return runner

    runner = asyncio.run(go())
    assert loadpoint(runner).mode == "off"  # SunCore did not fight the user
    accepted = [d for d in runner.decisions if d.kind is DecisionKind.ACCEPTED]
    assert len(accepted) == 1  # only the first takeover
    runtime = runner.stack.gate.runtime_of("evcc_lp") if runner.stack is not None else None
    assert runtime is not None
    assert runtime.override


def test_an_unconfirmed_loadpoint_stays_at_assisted() -> None:
    scenario = load_scenario(NAME)
    spec = scenario.devices[0].model_copy(update={"control": {"behaviour_confirmed": False}})
    scenario = scenario.model_copy(update={"devices": (spec,)})

    async def go() -> ScenarioRunner:
        runner = ScenarioRunner(scenario, catalog())
        try:
            await runner.run(1800)
        finally:
            await runner.close()
        return runner

    runner = asyncio.run(go())
    assert runner.evcc.sent == []  # assisted: proposals only, nothing written
    assert loadpoint(runner).mode == "off"
    assert runner.modes.device_mode("evcc_lp") is OperatingMode.AUTOMATIC  # the configured mode


def test_a2_an_evcc_plan_that_imports_is_cut_to_the_import_limit() -> None:
    """Review A2: evcc imports in `smart` by its own plan; SunCore lowers `maxCurrent` by the
    excess, keeps it (no raise straight back, no rejected re-tries) and the import stays under
    the limit."""
    result, runner = run_named("evcc_plan")
    limit = result.scenario.control.import_limit_w
    assert limit is not None
    over = [i * result.step_s for i, s in enumerate(result.steps) if s.grid_w > limit]
    assert over
    assert max(over) <= 60  # at most the first ticks until the cut is written
    cuts = [
        d
        for d in result.accepted("evcc_lp")
        if any(r.key == "controller.evcc.import_cut" for r in d.intent.reasons)
    ]
    assert len(cuts) == 1
    assert loadpoint(runner).max_current_a == 8.0
    assert len(result.of_kind(DecisionKind.REJECTED)) <= 2  # held, not re-tried every minute
    assert result.of_kind(DecisionKind.FAILED) == []
    tail = result.steps[len(result.steps) // 2 :]
    assert all(s.grid_w <= limit for s in tail)
    assert all(t in ALLOWED_TOPICS for t, _ in runner.evcc.sent)


def test_r1_flicker_near_the_threshold_switches_a_bounded_number_of_times() -> None:
    """Clouds and load flicker around evcc's 6 A minimum: off <-> on does not flap."""
    result, runner = run_named("evcc_flicker")
    modes = [
        d.intent.action.mode
        for d in result.accepted("evcc_lp")
        if isinstance(d.intent.action, LoadpointAction)
    ]
    changes = sum(1 for a, b in itertools.pairwise(modes) if (a == "off") != (b == "off"))
    assert changes <= 2
    assert result.switches_in_window("evcc_lp", window_s=3600) <= 4
    assert result.of_kind(DecisionKind.REJECTED) == []
    assert all(t in ALLOWED_TOPICS for t, _ in runner.evcc.sent)
