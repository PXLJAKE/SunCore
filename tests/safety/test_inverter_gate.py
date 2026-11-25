# SPDX-License-Identifier: Apache-2.0
"""The gate's inverter-limit path (PR 6-G, ADR-0018 decision 3): modes and X6, X1-X5,
takeover, read-back, lease, heartbeat and reset, override, release paths - with a fake
inverter adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from suncore.core.balance import Flow, FlowSnapshot
from suncore.core.control import ControlSettings, GateInputs
from suncore.core.decisions import (
    Action,
    Decision,
    DecisionKind,
    Intent,
    InverterLimitAction,
    OperatingMode,
    Origin,
    ReleaseAction,
)
from suncore.core.grid import GridState
from suncore.core.inverter import INVERTER_ASSET_ID, LimitCapability
from suncore.core.signals import Quality
from suncore.safety.gate import SafetyGate
from suncore.safety.inverter import InverterActuator, InverterGateConfig
from suncore.safety.modes import StaticModes
from suncore.safety.runtime import MemoryRuntimeStore
from tests.safety.helpers import T0, FakeClock, make_intent

AUTO = OperatingMode.AUTOMATIC
ASSISTED = OperatingMode.ASSISTED
BASE_W = InverterLimitAction(max_export_w=13_500.0)
BASE_PCT = InverterLimitAction(max_ac_pct=100.0)
ZERO = InverterLimitAction(max_export_w=0.0)


@dataclass
class FakeInverter:
    """An inverter limit adapter: ``state`` is what the read-back entity shows."""

    cap: LimitCapability = "export_limit"
    state: InverterLimitAction | None = BASE_W
    settle: float = 10.0
    beat: float | None = None
    silent_fail: bool = False
    refuse: Any = None
    applied: list[InverterLimitAction] = field(default_factory=list)
    released: list[Action | None] = field(default_factory=list)
    beats: int = 0
    beat_values: list[InverterLimitAction] = field(default_factory=list)
    held: InverterLimitAction | None = None

    @property
    def asset_id(self) -> str:
        return INVERTER_ASSET_ID

    @property
    def capability(self) -> LimitCapability:
        return self.cap

    @property
    def settle_s(self) -> float:
        return self.settle

    @property
    def heartbeat_s(self) -> float | None:
        return self.beat

    def limit_range(self) -> tuple[float, float]:
        return (0.0, 20_000.0) if self.cap == "export_limit" else (1.0, 100.0)

    def refusal(self) -> Any:
        return self.refuse

    @property
    def default_limit(self) -> InverterLimitAction | None:
        return BASE_PCT if self.beat is not None else None

    async def keep_alive(self, limit: InverterLimitAction) -> None:
        self.beats += 1
        self.beat_values.append(limit)
        if not self.silent_fail:
            self.state = limit

    async def read_back(self) -> Action | None:
        return self.state

    async def apply(self, intent: Intent) -> str | None:
        assert isinstance(intent.action, InverterLimitAction)
        self.applied.append(intent.action)
        self.held = intent.action
        if not self.silent_fail:
            self.state = intent.action
        return "ctx"

    async def release(self, baseline: Action | None) -> None:
        self.released.append(baseline)
        self.held = None
        if isinstance(baseline, InverterLimitAction):
            self.state = baseline


def site(
    grid_w: float | None = -6000.0,
    house_w: float | None = 1500.0,
    battery_w: float | None = 0.0,
    pv_w: float | None = 7500.0,
    quality: Quality = Quality.OK,
) -> FlowSnapshot:
    def flow(value: float | None) -> Flow:
        return Flow(value, quality if value is not None else Quality.UNAVAILABLE)

    return FlowSnapshot(
        grid_w=flow(grid_w),
        pv_w=flow(pv_w),
        battery_w=flow(battery_w),
        house_w=flow(house_w),
        battery_soc_pct=flow(50.0),
        ts=T0,
    )


@dataclass
class Rig:
    gate: SafetyGate
    inverter: FakeInverter
    clock: FakeClock
    modes: StaticModes
    decisions: list[Decision]
    inputs: dict[str, GateInputs]

    def at(
        self,
        *,
        headroom_w: float | None = 0.0,
        grid_state: GridState = GridState.CONNECTED,
        **kw: Any,
    ) -> None:
        self.inputs["now"] = GateInputs(
            flows=site(**kw),
            import_limit_w=20_000,
            export_limit_w=0.0,
            ts=self.clock.now,
            export_headroom_w=headroom_w,
            grid_state=grid_state,
        )

    async def submit(self, action: Action, *, origin: Origin = Origin.CONTROLLER) -> Decision:
        intent = make_intent(action, asset_id=INVERTER_ASSET_ID, ts=self.clock.now, origin=origin)
        return await self.gate.submit(intent)

    async def tick(self, seconds: float = 5.0) -> tuple[Decision, ...]:
        self.clock.advance(seconds)
        inputs = self.inputs["now"]
        self.inputs["now"] = GateInputs(
            flows=inputs.flows,
            import_limit_w=inputs.import_limit_w,
            export_limit_w=inputs.export_limit_w,
            ts=self.clock.now,
            export_headroom_w=inputs.export_headroom_w,
            grid_state=inputs.grid_state,
        )
        return await self.gate.poll()


def rig(
    inverter: FakeInverter | None = None,
    *,
    mode: OperatingMode = AUTO,
    verified: bool = True,
    configured: bool = True,
    store: MemoryRuntimeStore | None = None,
    identity: str = "inv-1",
    static_limit_w: float | None = 13_500.0,
    pv_kwp: float | None = None,
) -> Rig:
    clock = FakeClock()
    inverter = inverter or FakeInverter()
    decisions: list[Decision] = []
    inputs: dict[str, GateInputs] = {}

    async def sink(decision: Decision) -> None:
        decisions.append(decision)

    modes = StaticModes(AUTO, inverter=mode)
    config = InverterGateConfig(
        identity=identity,
        capability=inverter.cap,
        verified=verified,
        nominal_ac_w=10_000.0,
        static_limit_w=static_limit_w,
        pv_kwp=pv_kwp,
    )
    gate = SafetyGate(
        clock=clock,
        sink=sink,
        actuators={INVERTER_ASSET_ID: inverter},
        modes=modes,
        inputs=lambda: inputs.get("now"),
        store=store or MemoryRuntimeStore(),
        settings=ControlSettings(),
        sleep=clock.sleep,
        inverter=config if configured else None,
    )
    r = Rig(gate, inverter, clock, modes, decisions, inputs)
    r.at()
    return r


def keys(decision: Decision) -> list[str]:
    return [r.key for r in decision.reasons]


def test_the_fake_is_an_inverter_actuator() -> None:
    assert isinstance(FakeInverter(), InverterActuator)


# --- modes and X6 --------------------------------------------------------------------------------


async def test_without_inverter_config_a_limit_is_rejected_and_nothing_written() -> None:
    r = rig(configured=False)
    decision = await r.submit(ZERO)
    assert decision.kind is DecisionKind.REJECTED
    assert keys(decision) == ["safety.inverter_limit_not_supported"]
    assert r.inverter.applied == []


async def test_a_limit_for_another_asset_is_rejected() -> None:
    r = rig()
    intent = make_intent(ZERO, asset_id="heatpump", ts=r.clock.now)
    decision = await r.gate.submit(intent)
    assert keys(decision) == ["safety.inverter_limit_not_supported"]
    assert r.inverter.applied == []


async def test_the_inverter_starts_in_shadow_and_shadow_never_writes() -> None:
    r = rig(mode=OperatingMode.SHADOW)
    decision = await r.submit(ZERO)
    assert decision.kind is DecisionKind.SHADOW
    assert keys(decision)[0] == "safety.shadow_mode"
    assert r.inverter.applied == []
    assert StaticModes().inverter_mode() is OperatingMode.SHADOW


async def test_x6_an_unverified_adapter_is_capped_at_assisted() -> None:
    r = rig(verified=False, inverter=FakeInverter(cap="ac_limit", state=BASE_PCT))
    assert r.gate.inverter_mode_capped
    assert r.gate.modes_of(INVERTER_ASSET_ID).effective is ASSISTED
    decision = await r.submit(InverterLimitAction(max_ac_pct=50.0))
    assert decision.kind is DecisionKind.PROPOSED
    assert r.inverter.applied == []
    confirmed = await r.gate.confirm(decision.intent.id)
    assert confirmed is not None
    assert confirmed.kind is DecisionKind.ACCEPTED
    assert r.inverter.applied == [InverterLimitAction(max_ac_pct=50.0)]


async def test_a_verified_adapter_writes_in_automatic_and_reads_back() -> None:
    r = rig()
    assert not r.gate.inverter_mode_capped
    decision = await r.submit(ZERO)
    assert decision.kind is DecisionKind.ACCEPTED
    runtime = r.gate.runtime_of(INVERTER_ASSET_ID)
    assert runtime is not None
    assert runtime.under_control
    assert runtime.baseline == BASE_W
    assert runtime.last_written == ZERO


# --- X1-X5 at submit ----------------------------------------------------------------------------


async def test_x4_write_only_on_change() -> None:
    r = rig()
    await r.submit(ZERO)
    again = await r.submit(ZERO)
    assert keys(again) == ["safety.inverter_write_no_change"]
    assert r.inverter.applied == [ZERO]


async def test_x3_the_chain_must_be_exhausted_first() -> None:
    r = rig()
    r.at(headroom_w=2000.0)
    decision = await r.submit(ZERO)
    assert keys(decision) == ["safety.inverter_chain_not_exhausted"]
    r.at(headroom_w=None)
    decision = await r.submit(ZERO)
    assert keys(decision) == ["safety.inverter_headroom_unknown"]
    assert r.inverter.applied == []


async def test_x5_stale_inputs_and_island_refuse_a_new_limit() -> None:
    r = rig()
    r.at(quality=Quality.STALE)
    assert "safety.inverter_inputs_unknown" in keys(await r.submit(ZERO))
    r.at(grid_state=GridState.ISLAND)
    assert "safety.inverter_island" in keys(await r.submit(ZERO))
    assert r.inverter.applied == []


async def test_x1_never_above_the_users_own_limit() -> None:
    r = rig(FakeInverter(state=InverterLimitAction(max_export_w=4000.0)), static_limit_w=None)
    decision = await r.submit(InverterLimitAction(max_export_w=5000.0))
    assert keys(decision) == ["safety.inverter_above_baseline"]


async def test_x2_an_ac_limit_below_the_house_load_is_refused() -> None:
    r = rig(FakeInverter(cap="ac_limit", state=BASE_PCT))
    r.at(house_w=3000.0, battery_w=1000.0)  # needs 4500 W = 45 %
    decision = await r.submit(InverterLimitAction(max_ac_pct=40.0))
    assert keys(decision) == ["safety.inverter_may_cause_import"]
    assert (await r.submit(InverterLimitAction(max_ac_pct=45.0))).kind is DecisionKind.ACCEPTED


async def test_an_unreadable_limit_is_never_taken_over() -> None:
    r = rig(FakeInverter(state=None))
    decision = await r.submit(ZERO)
    assert decision.kind is DecisionKind.REJECTED
    assert r.inverter.applied == []


async def test_a_silent_write_failure_is_a_failed_read_back() -> None:
    r = rig(FakeInverter(silent_fail=True))
    decision = await r.submit(ZERO)
    assert decision.kind is DecisionKind.FAILED
    assert keys(decision) == ["safety.readback_mismatch"]


# --- the lease ---------------------------------------------------------------------------------


async def test_x5_stale_inputs_end_the_lease_and_restore_the_baseline() -> None:
    r = rig()
    await r.submit(ZERO)
    r.at(quality=Quality.STALE)
    decisions = await r.tick(30)
    assert r.inverter.released == [BASE_W]
    assert r.inverter.state == BASE_W
    assert any("safety.inverter_inputs_unknown" in keys(d) for d in decisions)


async def test_island_ends_the_lease() -> None:
    r = rig()
    await r.submit(ZERO)
    r.at(grid_state=GridState.ISLAND)
    await r.tick(30)
    assert r.inverter.released == [BASE_W]


async def test_x2_a_rising_house_load_ends_an_ac_lease() -> None:
    r = rig(FakeInverter(cap="ac_limit", state=BASE_PCT))
    await r.submit(InverterLimitAction(max_ac_pct=30.0))
    r.at(house_w=4000.0)
    await r.tick(30)
    assert r.inverter.released == [BASE_PCT]


async def test_a_lost_read_back_ends_the_lease() -> None:
    r = rig()
    await r.submit(ZERO)
    r.inverter.state = None
    decisions = await r.tick(30)
    assert r.inverter.released == [BASE_W]
    assert any("safety.inverter_readback_lost" in keys(d) for d in decisions)


async def test_the_heartbeat_runs_only_while_the_lease_holds() -> None:
    r = rig(FakeInverter(cap="ac_limit", state=BASE_PCT, beat=60.0))
    await r.submit(InverterLimitAction(max_ac_pct=50.0))
    for _ in range(12):
        await r.tick(10)
    assert r.inverter.beats == 2
    r.modes.set_inverter(OperatingMode.SHADOW)
    await r.gate.sync_modes()
    assert r.inverter.released == [BASE_PCT]
    beats = r.inverter.beats
    for _ in range(12):
        await r.tick(10)
    assert r.inverter.beats == beats


async def test_a_volatile_register_reset_is_rewritten_and_reported() -> None:
    r = rig(FakeInverter(cap="ac_limit", state=BASE_PCT, beat=600.0))
    await r.submit(InverterLimitAction(max_ac_pct=50.0))
    r.inverter.state = BASE_PCT  # the inverter restarted: register 533 back to 100 %
    before = len(r.decisions)
    await r.tick(30)
    assert r.inverter.state == InverterLimitAction(max_ac_pct=50.0)
    assert r.inverter.beats == 1
    assert [keys(d) for d in r.decisions[before:]] == [["safety.inverter_limit_reset"]]
    assert r.inverter.released == []


async def test_a_user_change_is_an_override_and_is_respected() -> None:
    r = rig()
    await r.submit(ZERO)
    mine = InverterLimitAction(max_export_w=8000.0)
    r.inverter.state = mine
    decisions = await r.tick(30)
    assert [d.kind for d in decisions] == [DecisionKind.HELD]
    assert keys(decisions[0]) == ["safety.inverter_override"]
    assert r.inverter.state == mine
    assert r.inverter.released == []
    held = await r.submit(InverterLimitAction(max_export_w=100.0))
    assert held.kind is DecisionKind.HELD
    assert r.inverter.applied == [ZERO]


# --- release paths -------------------------------------------------------------------------------


async def test_emergency_stop_restores_the_baseline_and_blocks() -> None:
    r = rig()
    await r.submit(ZERO)
    await r.gate.emergency_stop()
    assert r.inverter.released == [BASE_W]
    assert r.inverter.state == BASE_W
    assert keys(await r.submit(ZERO)) == ["safety.emergency_stop"]


async def test_shutdown_releases() -> None:
    r = rig()
    await r.submit(ZERO)
    await r.gate.shutdown()
    assert r.inverter.released == [BASE_W]


async def test_connection_loss_stops_the_heartbeat_and_the_restore_releases() -> None:
    r = rig(FakeInverter(cap="ac_limit", state=BASE_PCT, beat=60.0))
    await r.submit(InverterLimitAction(max_ac_pct=50.0))
    await r.gate.connection_lost()
    assert r.inverter.released == []  # nothing can be written while HA is gone
    await r.gate.connection_restored()
    assert r.inverter.released == [BASE_PCT]


async def test_crash_recovery_releases_what_a_crash_left() -> None:
    store = MemoryRuntimeStore()
    r = rig(store=store)
    await r.submit(ZERO)
    restarted = rig(r.inverter, store=store)
    await restarted.gate.recover()
    assert r.inverter.released == [BASE_W]


async def test_a_changed_adapter_is_never_written_with_the_old_limit() -> None:
    store = MemoryRuntimeStore()
    r = rig(store=store)
    await r.submit(ZERO)
    other = rig(FakeInverter(), store=store, identity="inv-2")
    await other.gate.recover()
    assert other.inverter.applied == []
    assert other.inverter.released == []
    runtime = other.gate.runtime_of(INVERTER_ASSET_ID)
    assert runtime is not None
    assert not runtime.under_control


async def test_a_controller_release_is_verified_by_read_back() -> None:
    r = rig()
    await r.submit(ZERO)
    r.clock.advance(60)
    r.at()
    decision = await r.submit(ReleaseAction())
    assert decision.kind is DecisionKind.ACCEPTED
    runtime = r.gate.runtime_of(INVERTER_ASSET_ID)
    assert runtime is not None
    assert runtime.under_control  # until the read-back proves the baseline
    decisions = [d for _ in range(4) for d in await r.tick(5)]
    assert [keys(d) for d in decisions] == [
        ["safety.released", "safety.inverter_release_confirmed"]
    ]
    runtime = r.gate.runtime_of(INVERTER_ASSET_ID)
    assert runtime is not None
    assert not runtime.under_control


async def test_an_unconfirmed_release_is_retried_and_writes_nothing_else() -> None:
    r = rig()
    await r.submit(ZERO)
    r.clock.advance(60)

    async def stuck(baseline: Action | None) -> None:
        r.inverter.released.append(baseline)  # the write lands nowhere

    r.inverter.release = stuck  # type: ignore[method-assign]
    await r.submit(ReleaseAction())
    seen = [d for _ in range(40) for d in await r.tick(5)]
    assert len(r.inverter.released) >= 2
    assert any("safety.inverter_release_unconfirmed" in keys(d) for d in seen)
    assert r.inverter.applied == [ZERO]
    blocked = await r.submit(InverterLimitAction(max_export_w=100.0))
    assert blocked.kind is DecisionKind.REJECTED


async def test_a_user_value_during_the_release_is_kept() -> None:
    r = rig()
    await r.submit(ZERO)
    r.clock.advance(60)
    mine = InverterLimitAction(max_export_w=9000.0)
    r.inverter.state = mine
    decision = await r.submit(ReleaseAction())
    assert decision.kind is DecisionKind.ACCEPTED
    assert "safety.inverter_override" in keys(decision)
    assert r.inverter.released == []
    assert r.inverter.state == mine


# --- review A1: the lease checks every limit that may be in force --------------------------------

PCT60 = InverterLimitAction(max_ac_pct=60.0)
PCT30 = InverterLimitAction(max_ac_pct=30.0)


async def stricter_attempt_fails() -> Rig:
    """60 % confirmed; a stricter 30 % fails its read-back (pending, may land late)."""
    r = rig(FakeInverter(cap="ac_limit", state=BASE_PCT, beat=60.0))
    assert (await r.submit(PCT60)).kind is DecisionKind.ACCEPTED
    await r.tick(60)
    r.inverter.silent_fail = True
    decision = await r.submit(PCT30)
    assert keys(decision) == ["safety.readback_mismatch"]
    assert r.inverter.state == PCT60
    return r


async def test_a1_a_pending_stricter_limit_ends_the_lease_when_the_house_load_rises() -> None:
    r = await stricter_attempt_fails()
    r.at(house_w=3000.0)  # 30 % = 3000 W < 3000 + 250 W; 60 % would still pass
    decisions = await r.tick(5)
    assert r.inverter.released == [BASE_PCT]
    assert any("safety.inverter_may_cause_import" in keys(d) for d in decisions)
    assert PCT30 not in r.inverter.beat_values  # the heartbeat never wrote the attempt


async def test_a1_no_heartbeat_while_a_write_is_open_and_only_the_confirmed_limit() -> None:
    r = await stricter_attempt_fails()
    for _ in range(30):
        await r.tick(10)
    assert r.inverter.beat_values  # the open write was dropped after its settle time ...
    assert set(r.inverter.beat_values) == {PCT60}  # ... and only 60 % is kept alive
    runtime = r.gate.runtime_of(INVERTER_ASSET_ID)
    assert runtime is not None
    assert runtime.pending_action is None
    assert runtime.last_written == PCT60


async def test_a1_a_late_landing_stricter_limit_is_judged_and_then_held() -> None:
    r = await stricter_attempt_fails()
    r.inverter.state = PCT30  # the 30 % lands late
    r.inverter.silent_fail = False
    await r.tick(30)
    runtime = r.gate.runtime_of(INVERTER_ASSET_ID)
    assert runtime is not None
    assert runtime.last_written == PCT30  # confirmed late
    assert runtime.pending_action is None
    r.at(house_w=3000.0)
    await r.tick(5)
    assert r.inverter.released == [BASE_PCT]


# --- review A2: takeover only from a baseline a release can restore -------------------------------


async def test_a2_a_volatile_register_is_taken_over_only_at_its_default() -> None:
    r = rig(FakeInverter(cap="ac_limit", state=InverterLimitAction(max_ac_pct=80.0), beat=60.0))
    decision = await r.submit(PCT60)
    assert decision.kind is DecisionKind.REJECTED
    assert keys(decision) == ["safety.inverter_baseline_not_default"]
    assert r.inverter.applied == []
    assert r.gate.runtime_of(INVERTER_ASSET_ID) is None or not (
        r.gate.runtime_of(INVERTER_ASSET_ID).under_control  # type: ignore[union-attr]
    )


async def test_a2_an_export_limit_below_the_static_limit_is_not_taken_over() -> None:
    r = rig(FakeInverter(state=InverterLimitAction(max_export_w=8000.0)))
    decision = await r.submit(ZERO)
    assert keys(decision) == ["safety.inverter_baseline_below_static"]
    assert r.inverter.applied == []


# --- review A4: the percent base must fit the installed PV ----------------------------------------


async def test_a4_an_implausible_nominal_power_refuses_an_ac_limit() -> None:
    r = rig(FakeInverter(cap="ac_limit", state=BASE_PCT), pv_kwp=3.0)  # 10 kW on 3 kWp
    decision = await r.submit(PCT60)
    assert keys(decision) == ["safety.inverter_nominal_implausible"]
    ok = rig(FakeInverter(cap="ac_limit", state=BASE_PCT), pv_kwp=8.0)
    assert (await ok.submit(PCT60)).kind is DecisionKind.ACCEPTED
