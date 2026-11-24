# SPDX-License-Identifier: Apache-2.0
"""The export chain and the inverter limit in the closed loop (PR 6-G, plan WP7): zero export,
the dynamic 0 W limit at negative prices and the volatile Kostal register 533 with heartbeat,
reset, lost read-back, crash and restart.

Evidence comes from the simulated inverter limiter (what it really did) and the house balance,
not from the gate's own opinion.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pytest

from suncore.core.decisions import Decision, DecisionKind, InverterLimitAction, OperatingMode
from suncore.sim.demo_devices import sim_price
from suncore.sim.limiter import SimExportLimiter
from suncore.sim.runner import RunResult, ScenarioRunner
from suncore.sim.scenario import Scenario, load_scenario
from tests.sim.helpers import catalog

pytestmark = pytest.mark.simulator

Played = tuple[RunResult, ScenarioRunner, SimExportLimiter]


async def play(
    scenario: Scenario,
    *,
    mode: OperatingMode = OperatingMode.AUTOMATIC,
    actions: dict[float, Callable[[ScenarioRunner], Any]] | None = None,
) -> Played:
    runner = ScenarioRunner(scenario, catalog(), global_mode=mode)
    for at_s, action in (actions or {}).items():
        runner.at(at_s, action)
    await runner.start()
    limiter = runner.export_limiter
    assert limiter is not None
    result = await runner.run()
    await runner.close()
    return result, runner, limiter


def keys(decision: Decision) -> list[str]:
    return [r.key for r in decision.reasons]


def offset(result: RunResult, when: datetime) -> float:
    return (when - result.scenario.start).total_seconds()


def limits(runner: ScenarioRunner, kind: DecisionKind = DecisionKind.ACCEPTED) -> list[Decision]:
    return [
        d
        for d in runner.inverter_decisions()
        if d.kind is kind and isinstance(d.intent.action, InverterLimitAction)
    ]


def assert_no_import_while_curtailing(result: RunResult) -> None:
    """X2 evidence: whenever the inverter curtails, the site does not import."""
    curtailing = [s for s in result.steps if s.curtailed_w > 0]
    assert curtailing
    assert max(s.grid_w for s in curtailing) <= 1.0


# --- export_limit_zero_export ----------------------------------------------------------------


@pytest.fixture(scope="module")
def zero_export() -> Played:
    return asyncio.run(play(load_scenario("export_limit_zero_export")))


def test_zero_export_curtails_only_after_devices_and_battery(zero_export: Played) -> None:
    result, runner, limiter = zero_export
    accepted = limits(runner)
    assert accepted, [keys(d) for d in runner.inverter_decisions()]
    first = accepted[0]
    assert first.intent.action == InverterLimitAction(max_export_w=0.0)
    at = offset(result, first.intent.ts)
    index = int(at // result.step_s)
    # Chain order: the pool was switched on first, the battery charged at its maximum.
    pool_on = next(i for i, on in enumerate(result.devices["pool"].on) if on)
    assert pool_on < index
    assert result.steps[index].battery_w >= result.scenario.house.battery.max_charge_w - 50
    # From then on the export is zero (the limit holds) and nothing is imported for it.
    after = [s for s in result.steps if offset(result, s.t) >= at + 3 * result.step_s]
    assert after
    assert max(-s.grid_w for s in after) <= 1.0
    assert_no_import_while_curtailing(result)
    # The inverter's own 13.5 kW limit is never written; the number holds 0 W.
    assert limiter.export_limit_w == 0.0
    assert all(w[2] <= 13_500 for w in limiter.log.writes)


def test_zero_export_does_not_oscillate_under_cloud_flicker(zero_export: Played) -> None:
    result, runner, limiter = zero_export
    assert len(limiter.log.writes) <= 2
    assert len(limits(runner)) <= 2
    assert result.switches_in_window("pool") <= 4
    assert result.of_kind(DecisionKind.REJECTED) == []
    assert result.of_kind(DecisionKind.FAILED) == []


def test_zero_export_in_shadow_never_writes() -> None:
    result, runner, limiter = asyncio.run(
        play(load_scenario("export_limit_zero_export"), mode=OperatingMode.SHADOW)
    )
    assert limiter.log.writes == []
    shadow = [d for d in runner.inverter_decisions() if d.kind is DecisionKind.SHADOW]
    assert shadow
    assert not any(s.curtailed_w > 0 for s in result.steps)


# --- negative_price_curtail --------------------------------------------------------------------


def negative_window(scenario: Scenario) -> tuple[float, float]:
    """Offsets of the first and the end of the last negative 15-minute price slot."""
    offsets = [
        float(s)
        for s in range(0, scenario.duration_s, 900)
        if sim_price(scenario.start + timedelta(seconds=s), scenario.prices.profile) < 0
    ]
    assert offsets
    return offsets[0], offsets[-1] + 900


@pytest.fixture(scope="module")
def negative() -> Played:
    return asyncio.run(play(load_scenario("negative_price_curtail")))


def test_negative_price_limits_to_zero_and_gives_the_limit_back(negative: Played) -> None:
    result, runner, limiter = negative
    start, end = negative_window(result.scenario)
    writes = limiter.log.writes
    assert [w[2] for w in writes] == [0.0, 13_500.0]  # limit, then the user's own value back
    limit_at = offset(result, writes[0][0])
    release_at = offset(result, writes[1][0])
    assert start <= limit_at <= start + 600
    assert end <= release_at <= end + 600
    assert limiter.export_limit_w == 13_500.0
    released = [
        d for d in runner.inverter_decisions() if "safety.inverter_release_confirmed" in keys(d)
    ]
    assert released
    assert_no_import_while_curtailing(result)
    assert result.of_kind(DecisionKind.REJECTED) == []


def test_negative_price_without_verification_never_curtails() -> None:
    base = load_scenario("negative_price_curtail")
    assert base.inverter is not None
    scenario = base.model_copy(
        update={"inverter": base.inverter.model_copy(update={"verified": False})}
    )
    result, _, limiter = asyncio.run(play(scenario))
    # ADR-0018 decision 4: the dynamic 0 W limit needs a verified adapter.
    assert limiter.log.writes == []
    assert not any(s.curtailed_w > 0 for s in result.steps)


# --- inverter_limit_heartbeat_lost -------------------------------------------------------------

CRASH_S = 4800.0
RESTART_S = 5400.0


async def _crash(runner: ScenarioRunner) -> None:
    assert runner.stack is not None
    await runner.stack.crash()


async def _restart(runner: ScenarioRunner) -> None:
    await runner.restart()


@pytest.fixture(scope="module")
def heartbeat() -> Played:
    return asyncio.run(
        play(
            load_scenario("inverter_limit_heartbeat_lost"),
            actions={CRASH_S: _crash, RESTART_S: _restart},
        )
    )


def register_writes(result: RunResult, limiter: SimExportLimiter) -> list[tuple[float, int]]:
    return [(offset(result, w[0]), int(w[2]["value"])) for w in limiter.log.writes]


def test_register_533_is_written_as_an_int_and_re_written_every_heartbeat(
    heartbeat: Played,
) -> None:
    result, runner, limiter = heartbeat
    writes = register_writes(result, limiter)
    assert writes
    first = limits(runner)[0]
    assert first.intent.action.max_ac_pct is not None  # type: ignore[union-attr]
    held = [t for t, v in writes if t < 1700 and v < 100]
    gaps = [b - a for a, b in itertools.pairwise(held)]
    assert gaps
    assert max(gaps) <= 70  # heartbeat_s 60 plus one tick
    assert all(isinstance(w[2]["value"], int) for w in limiter.log.writes)


def test_an_inverter_reset_is_detected_and_re_written(heartbeat: Played) -> None:
    result, runner, limiter = heartbeat
    resets = [d for d in runner.inverter_decisions() if "safety.inverter_limit_reset" in keys(d)]
    assert resets
    assert 1800 <= offset(result, resets[0].intent.ts) <= 1830
    rewrite = [(t, v) for t, v in register_writes(result, limiter) if 1800 <= t <= 1830]
    assert rewrite
    assert rewrite[0][1] < 100


def test_a_lost_read_back_restores_the_inverter_default(heartbeat: Played) -> None:
    result, runner, limiter = heartbeat
    lost = [d for d in runner.inverter_decisions() if "safety.inverter_readback_lost" in keys(d)]
    assert lost
    assert 3000 <= offset(result, lost[0].intent.ts) <= 3020
    during = [(t, v) for t, v in register_writes(result, limiter) if 3000 <= t < 3300]
    assert during
    assert all(v == 100 for _, v in during)  # only the release, never a limit, while blind


def test_crash_stops_the_heartbeat_and_the_restart_releases(heartbeat: Played) -> None:
    result, _, limiter = heartbeat
    writes = register_writes(result, limiter)
    assert not [w for w in writes if CRASH_S + 20 <= w[0] < RESTART_S]
    after = [w for w in writes if w[0] >= RESTART_S]
    assert after
    assert after[0][1] == 100
    assert after[0][0] <= RESTART_S + 30


def test_heartbeat_scenario_never_imports_for_the_limit(heartbeat: Played) -> None:
    result, runner, _ = heartbeat
    assert_no_import_while_curtailing(result)
    assert [d for d in runner.inverter_decisions() if d.kind is DecisionKind.REJECTED] == []
