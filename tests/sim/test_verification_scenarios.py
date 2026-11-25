# SPDX-License-Identifier: Apache-2.0
"""The inverter limit verification procedure in the closed loop (PR 7-J, plan WP8).

Scenarios ``inverter_verify_rest`` (Kostal REST, W, export semantics) and
``inverter_verify_modbus`` (register 533, % of 10 kW, AC semantics) and their variants: a
value that really limits the AC output, a write the inverter ignores silently, an export that
does not follow, a load that rises mid-step, a lost connection, an emergency stop, a crash and
restart, and an expired window.

Evidence comes from the simulated limiter (what was written, what it holds at the end) and the
house balance (never grid import while the test limit is held), not from the procedure's own
opinion. Every path ends with the user's own limit back.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from suncore.app.verification import InverterVerification
from suncore.core.config import InverterVerificationRecord
from suncore.core.decisions import DecisionKind, InverterLimitAction
from suncore.core.verification_view import VerificationPhase
from suncore.sim.house import LoadEvent
from suncore.sim.limiter import SimExportLimiter
from suncore.sim.runner import RunResult, ScenarioRunner
from suncore.sim.scenario import Fault, Scenario, load_scenario
from tests.sim.helpers import catalog

pytestmark = pytest.mark.simulator

START_S = 20.0
CONFIRM_S = 160.0
"""Stable since 20 s, ready after 120 s (tick at 140 s): the user confirms at 160 s."""
BASE_REST = 13_500.0


@dataclass
class Played:
    result: RunResult
    runner: ScenarioRunner
    limiter: SimExportLimiter
    procedure: InverterVerification
    records: list[tuple[InverterVerificationRecord, bool | None]] = field(default_factory=list)

    @property
    def reason(self) -> str | None:
        return self.procedure.reasons[0].key if self.procedure.reasons else None

    def written(self) -> list[Any]:
        return [w[2] for w in self.limiter.log.writes]


def variant(name: str, **limiter: Any) -> Scenario:
    base = load_scenario(name)
    params = base.house.export_limiter
    assert params is not None
    house = base.house.model_copy(update={"export_limiter": params.model_copy(update=limiter)})
    return base.model_copy(update={"house": house})


def with_load(scenario: Scenario, event: LoadEvent) -> Scenario:
    load = scenario.house.load.model_copy(update={"events": (event,)})
    return scenario.model_copy(update={"house": scenario.house.model_copy(update={"load": load})})


async def play(
    scenario: Scenario,
    *,
    confirm: bool = True,
    actions: dict[float, Callable[[ScenarioRunner], Any]] | None = None,
    window_s: float | None = None,
) -> Played:
    runner = ScenarioRunner(scenario, catalog())
    records: list[tuple[InverterVerificationRecord, bool | None]] = []
    holder: dict[str, InverterVerification] = {}

    async def start(r: ScenarioRunner) -> None:
        assert r.stack is not None
        procedure = InverterVerification(
            r.stack, on_finished=lambda rec, v: records.append((rec, v)), window_s=window_s
        )
        assert procedure.start() == ()
        r.stack.verification = procedure
        holder["p"] = procedure

    async def do_confirm(_: ScenarioRunner) -> None:
        assert holder["p"].phase is VerificationPhase.READY, holder["p"].reasons
        await holder["p"].confirm()

    runner.at(START_S, start)
    if confirm:
        runner.at(CONFIRM_S, do_confirm)
    for at_s, action in (actions or {}).items():
        runner.at(at_s, action)
    await runner.start()
    limiter = runner.export_limiter
    assert limiter is not None
    result = await runner.run()
    await runner.close()
    return Played(result, runner, limiter, holder["p"], records)


def run(scenario: Scenario, **kw: Any) -> Played:
    return asyncio.run(play(scenario, **kw))


def assert_no_import_while_curtailing(p: Played) -> None:
    """X2 evidence: whenever the inverter curtails, the site does not import."""
    curtailing = [s for s in p.result.steps if s.curtailed_w > 0]
    assert curtailing
    assert max(s.grid_w for s in curtailing) <= 1.0


def assert_released(p: Played, baseline: float = BASE_REST) -> None:
    assert p.limiter.export_limit_w == baseline
    runtime = p.runner.stack.gate.runtime_of("inverter") if p.runner.stack else None
    assert runtime is None or not runtime.under_control
    assert p.runner.stack is not None
    assert p.runner.stack.gate.verification_window is None


# --- passes ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rest_pass() -> Played:
    return run(load_scenario("inverter_verify_rest"))


def test_rest_pass_export_semantics(rest_pass: Played) -> None:
    p = rest_pass
    assert p.procedure.phase is VerificationPhase.PASSED, p.procedure.reasons
    assert p.procedure.view().semantics == "export_limit"
    test, back = p.written()
    before = p.procedure.view().export_before_w
    assert before is not None
    assert test == pytest.approx(before - 1_000.0, abs=2.0)  # export - 1 kW
    assert back == BASE_REST
    assert_released(p)
    assert_no_import_while_curtailing(p)
    (record, verified), *_ = p.records
    assert verified is True
    assert record.outcome == "passed"
    assert record.semantics == "export_limit"


def test_rest_pass_nothing_is_written_before_the_confirmation() -> None:
    p = run(load_scenario("inverter_verify_rest"), confirm=False)
    assert p.written() == []
    assert p.procedure.phase is VerificationPhase.FAILED
    assert p.reason == "verify.confirm_timeout"
    assert p.records == []  # nothing was tested: nothing stored


def test_the_export_chain_stays_out_of_the_way(rest_pass: Played) -> None:
    accepted = [
        d
        for d in rest_pass.runner.inverter_decisions()
        if d.kind is DecisionKind.ACCEPTED and isinstance(d.intent.action, InverterLimitAction)
    ]
    assert [d.intent.id.split(":")[0] for d in accepted] == ["verify"]
    assert all(d.intent.origin.value == "user" for d in accepted)


def test_modbus_pass_ac_semantics() -> None:
    p = run(load_scenario("inverter_verify_modbus"))
    assert p.procedure.phase is VerificationPhase.PASSED, p.procedure.reasons
    assert p.procedure.view().semantics == "ac_limit"
    values = [w["value"] for w in p.written()]
    before = p.procedure.view().export_before_w
    assert before is not None
    assert values[0] == math.ceil((before - 1_000.0) / 100.0)  # export - 1 kW, int % of 10 kW
    assert values[-1] == 100  # the release writes the inverter's default back
    assert p.limiter.ac_pct == 100.0
    assert_no_import_while_curtailing(p)
    assert p.records[0][1] is True


# --- failures that are evidence: limit_verified is reset -------------------------------------


def test_semantics_mismatch_rest_value_limits_the_ac_output() -> None:
    p = run(variant("inverter_verify_rest", acts_as="ac_limit"))
    assert p.procedure.phase is VerificationPhase.FAILED
    assert p.reason == "verify.semantics_mismatch"
    assert p.procedure.view().semantics == "ac_limit"
    assert_released(p)
    assert_no_import_while_curtailing(p)  # X2 held although the value was an AC limit
    assert p.records[0][1] is False


def test_semantics_mismatch_modbus_percent_limits_the_export() -> None:
    p = run(variant("inverter_verify_modbus", acts_as="export_limit"))
    assert p.procedure.phase is VerificationPhase.FAILED
    assert p.reason == "verify.semantics_mismatch"
    assert p.limiter.ac_pct == 100.0


def test_export_does_not_follow() -> None:
    p = run(variant("inverter_verify_rest", inert=True))
    assert p.procedure.phase is VerificationPhase.FAILED
    assert p.reason == "verify.export_not_following"
    assert_released(p)
    assert p.records[0][1] is False


def test_read_back_mismatch() -> None:
    p = run(variant("inverter_verify_rest", write_fails=True))
    assert p.procedure.phase is VerificationPhase.FAILED
    assert p.reason == "safety.readback_mismatch"
    assert p.limiter.export_limit_w == BASE_REST
    assert p.records[0][1] is False


# --- aborts and interruptions: nothing about the adapter is learnt --------------------------------


def test_house_load_rises_mid_step_x2_ends_the_lease() -> None:
    scenario = with_load(
        load_scenario("inverter_verify_rest"), LoadEvent(from_s=200, to_s=400, extra_w=5_000)
    )
    p = run(scenario)
    assert p.procedure.phase is VerificationPhase.FAILED
    assert p.reason == "verify.lease_ended"
    assert_released(p)
    assert max(s.grid_w for s in p.result.steps if s.curtailed_w > 0) <= 1.0
    assert p.records[0][1] is None  # no evidence against the adapter


def test_connection_lost_mid_step_the_restore_releases() -> None:
    base = load_scenario("inverter_verify_rest")
    scenario = base.model_copy(update={"faults": (Fault(kind="disconnect", from_s=210, to_s=260),)})
    p = run(scenario)
    assert p.procedure.phase is VerificationPhase.FAILED
    assert p.reason == "verify.connection_lost"
    assert_released(p)


async def _emergency(r: ScenarioRunner) -> None:
    assert r.stack is not None
    await r.stack.gate.emergency_stop()


def test_emergency_stop_mid_step() -> None:
    p = run(load_scenario("inverter_verify_rest"), actions={210.0: _emergency})
    assert p.procedure.phase is VerificationPhase.ABORTED
    assert p.reason == "safety.emergency_stop"
    assert p.limiter.export_limit_w == BASE_REST
    assert p.records == []


async def _crash(r: ScenarioRunner) -> None:
    assert r.stack is not None
    await r.stack.crash()


async def _restart(r: ScenarioRunner) -> None:
    await r.restart()


def test_crash_mid_step_the_restart_releases() -> None:
    p = run(load_scenario("inverter_verify_rest"), actions={210.0: _crash, 300.0: _restart})
    # The procedure died with the process; the new gate has no window and gave 13.5 kW back.
    assert p.written()[-1] == BASE_REST
    assert_released(p)


async def _abort(r: ScenarioRunner) -> None:
    assert r.stack is not None
    assert r.stack.verification is not None
    await r.stack.verification.abort()


def test_closing_the_dialog_mid_step_releases() -> None:
    p = run(load_scenario("inverter_verify_rest"), actions={210.0: _abort})
    assert p.procedure.phase is VerificationPhase.ABORTED
    assert p.written()[-1] == BASE_REST
    assert_released(p)
    assert p.records == []


def test_window_expired() -> None:
    p = run(load_scenario("inverter_verify_rest"), window_s=40.0)
    assert p.procedure.phase is VerificationPhase.FAILED
    assert p.reason == "verify.window_expired"
    assert_released(p)
    released = [
        d
        for d in p.runner.inverter_decisions()
        if "safety.verification_window_closed" in [r.key for r in d.reasons]
    ]
    assert released
