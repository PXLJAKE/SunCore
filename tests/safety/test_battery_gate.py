# SPDX-License-Identifier: Apache-2.0
"""The gate's battery path (WP10, ADR-0013): modes, invariants, takeover, read-back, lease,
heartbeat, release paths, override, watchdog - with a fake battery adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, timedelta
from typing import Any

import pytest

from suncore.core.battery import (
    BATTERY_ASSET_ID,
    BatteryAction,
    BatteryCapability,
    BatteryModel,
    BatteryRanges,
    FieldRange,
    GridChargePolicy,
    resolve_battery_action,
)
from suncore.core.control import BatteryBand, ControlSettings, GateInputs, Plan, PlanInterval
from suncore.core.decisions import (
    Action,
    Decision,
    DecisionKind,
    Intent,
    OperatingMode,
    Origin,
    ReleaseAction,
    SwitchAction,
)
from suncore.core.devices import OverridePolicy
from suncore.safety import overrides
from suncore.safety.battery import BatteryActuator, BatteryGateConfig, BatteryWatch
from suncore.safety.gate import SafetyGate
from suncore.safety.modes import StaticModes
from suncore.safety.runtime import MemoryRuntimeStore
from tests.safety.helpers import T0, FakeClock, flows, make_intent

AUTO = OperatingMode.AUTOMATIC
ALL = frozenset(BatteryCapability) - {BatteryCapability.HARDWARE_FALLBACK}
MODEL = BatteryModel(capacity_wh=10_000, max_charge_w=5000, max_discharge_w=5000)
BASE = BatteryAction(max_charge_w=5000, max_discharge_w=5000, min_soc_pct=10, grid_charge_w=0)


@dataclass
class FakeBattery:
    """A battery adapter: ``state`` is what the entities show."""

    caps: frozenset[BatteryCapability] = ALL
    state: BatteryAction | None = BASE
    settle: float = 10.0
    beat: float | None = None
    armed_ok: bool = True
    armed: bool = False
    silent_fail: bool = False
    refuse: Any = None
    applied: list[BatteryAction] = field(default_factory=list)
    released: list[Action | None] = field(default_factory=list)
    beats: int = 0
    held: BatteryAction | None = None
    own: BatteryAction | None = None
    """``takeover_baseline`` when it differs from the read-back (Kostal §3.5 own limits)."""

    @property
    def asset_id(self) -> str:
        return BATTERY_ASSET_ID

    @property
    def capabilities(self) -> frozenset[BatteryCapability]:
        return self.caps

    @property
    def settle_s(self) -> float:
        return self.settle

    @property
    def heartbeat_s(self) -> float | None:
        return self.beat

    def ranges(self) -> BatteryRanges:
        return BatteryRanges(
            max_charge_w=FieldRange(0, 5000),
            max_discharge_w=FieldRange(0, 5000),
            min_soc_pct=FieldRange(5, 100, 5),
            grid_charge_w=FieldRange(0, 5000),
        )

    def refusal(self) -> Any:
        return self.refuse

    def prepared(self) -> bool:
        return BatteryCapability.HARDWARE_FALLBACK not in self.caps or self.armed

    async def arm_fallback(self) -> None:
        self.armed = self.armed_ok

    async def keep_alive(self) -> None:
        self.beats += 1

    async def read_back(self) -> Action | None:
        return self.state

    async def takeover_baseline(self) -> Action | None:
        return self.state if self.own is None else self.own

    async def apply(self, intent: Intent) -> str | None:
        assert isinstance(intent.action, BatteryAction)
        self.applied.append(intent.action)
        self.held = intent.action
        if not self.silent_fail:
            self.state = resolve_battery_action(intent.action, self.state)
        return "ctx"

    async def release(self, baseline: Action | None) -> None:
        self.released.append(baseline)
        self.held = None
        if isinstance(baseline, BatteryAction):
            self.state = baseline


def plan(*, now: Any = T0, **band: Any) -> Plan:
    start = now - timedelta(minutes=5)
    return Plan(
        created_at=start,
        intervals=(
            PlanInterval(
                start=start, end=start + timedelta(minutes=15), battery=BatteryBand(**band)
            ),
        ),
    )


@dataclass
class Rig:
    gate: SafetyGate
    battery: FakeBattery
    clock: FakeClock
    modes: StaticModes
    decisions: list[Decision]
    inputs: dict[str, GateInputs]
    plans: dict[str, Plan]
    prices: dict[str, float | None]

    def at(self, **kw: Any) -> None:
        self.inputs["now"] = GateInputs(flows=flows(**kw), import_limit_w=10_000, ts=self.clock.now)

    async def submit(self, action: Action, *, origin: Origin = Origin.CONTROLLER) -> Decision:
        intent = make_intent(action, asset_id=BATTERY_ASSET_ID, ts=self.clock.now, origin=origin)
        return await self.gate.submit(intent)

    async def tick(self, seconds: float = 5.0) -> tuple[Decision, ...]:
        self.clock.advance(seconds)
        inputs = self.inputs["now"]
        self.inputs["now"] = GateInputs(
            flows=inputs.flows, import_limit_w=inputs.import_limit_w, ts=self.clock.now
        )
        return await self.gate.poll()


def rig(
    battery: FakeBattery | None = None,
    *,
    mode: OperatingMode = AUTO,
    policy: GridChargePolicy | None = None,
    fallback_verified: bool = False,
    store: MemoryRuntimeStore | None = None,
    identity: str = "bat-1",
    settings: ControlSettings | None = None,
) -> Rig:
    clock = FakeClock()
    battery = battery or FakeBattery()
    decisions: list[Decision] = []
    inputs: dict[str, GateInputs] = {}
    plans = {"now": plan(grid_charge_max_w=3000, discharge_lock=True)}
    prices: dict[str, float | None] = {"now": 0.10}

    async def sink(decision: Decision) -> None:
        decisions.append(decision)

    modes = StaticModes(AUTO, battery=mode)
    config = BatteryGateConfig(
        identity=identity,
        model=MODEL,
        reserve_pct=15.0,
        policy=policy or GridChargePolicy(enabled=True, max_price=0.20),
        fallback_verified=fallback_verified,
        plan=lambda _now: plans["now"],
        import_price=lambda _now: prices["now"],
        watch=BatteryWatch(frozen_s=300),
    )
    gate = SafetyGate(
        clock=clock,
        sink=sink,
        actuators={BATTERY_ASSET_ID: battery},
        modes=modes,
        inputs=lambda: inputs.get("now"),
        store=store or MemoryRuntimeStore(),
        settings=settings or ControlSettings(),
        sleep=clock.sleep,
        battery=config,
    )
    r = Rig(gate, battery, clock, modes, decisions, inputs, plans, prices)
    r.at(grid_w=600, battery_w=0, soc_pct=40)
    return r


def keys(decision: Decision) -> list[str]:
    return [r.key for r in decision.reasons]


def test_the_fake_is_a_battery_actuator() -> None:
    assert isinstance(FakeBattery(), BatteryActuator)


# --- the blanket rejection is gone only for the configured adapter -----------------------------


async def test_without_battery_wiring_or_for_another_asset_nothing_is_written() -> None:
    r = rig()
    decision = await r.gate.submit(
        make_intent(BatteryAction(max_discharge_w=0), asset_id="pool", ts=r.clock.now)
    )
    assert decision.kind is DecisionKind.REJECTED
    assert keys(decision) == ["safety.battery_not_supported"]

    async def sink(_: Decision) -> None:
        return None

    bare = SafetyGate(clock=r.clock, sink=sink, modes=StaticModes(AUTO, battery=AUTO))
    decision = await bare.submit(
        make_intent(BatteryAction(max_discharge_w=0), asset_id=BATTERY_ASSET_ID, ts=r.clock.now)
    )
    assert keys(decision) == ["safety.battery_not_supported"]
    assert r.battery.applied == []


async def test_a_battery_starts_in_shadow_and_shadow_never_writes() -> None:
    r = rig(mode=OperatingMode.SHADOW)
    decision = await r.submit(BatteryAction(max_discharge_w=0))
    assert decision.kind is DecisionKind.SHADOW
    assert keys(decision) == ["safety.shadow_mode"]
    unplanned = await r.submit(BatteryAction(grid_charge_w=4000))
    assert "safety.battery_grid_charge_bound" in keys(unplanned)  # honest "would have" reasons
    assert r.battery.applied == []


async def test_global_shadow_wins_over_the_battery_mode() -> None:
    r = rig()
    r.modes.set_global(OperatingMode.SHADOW)
    assert (await r.submit(BatteryAction(max_discharge_w=0))).kind is DecisionKind.SHADOW


# --- writes ------------------------------------------------------------------------------------


async def test_a_planned_discharge_lock_is_written_and_read_back() -> None:
    r = rig()
    decision = await r.submit(BatteryAction(max_discharge_w=0))
    assert decision.kind is DecisionKind.ACCEPTED, keys(decision)
    # None = baseline: the adapter receives the full resolved state.
    assert r.battery.applied == [
        BatteryAction(max_charge_w=5000, max_discharge_w=0, min_soc_pct=10, grid_charge_w=0)
    ]
    runtime = r.gate.runtime_of(BATTERY_ASSET_ID)
    assert runtime is not None
    assert runtime.baseline == BASE
    assert runtime.last_written == BatteryAction(max_discharge_w=0)
    assert runtime.identity == "bat-1"


async def test_write_only_on_change() -> None:
    r = rig()
    await r.submit(BatteryAction(max_discharge_w=0))
    r.clock.advance(60)
    r.at(grid_w=600, battery_w=0, soc_pct=40)
    again = await r.submit(BatteryAction(max_discharge_w=0))
    assert again.kind is DecisionKind.REJECTED
    assert keys(again) == ["safety.battery_write_no_change"]
    assert len(r.battery.applied) == 1


async def test_write_rate_limit() -> None:
    r = rig(settings=ControlSettings(min_write_interval_s=120))
    await r.submit(BatteryAction(max_discharge_w=0))
    decision = await r.submit(BatteryAction(max_discharge_w=0, min_soc_pct=45))
    assert keys(decision) == ["safety.rate_limited"]


async def test_a_silent_write_failure_is_a_failed_read_back_and_three_trip() -> None:
    r = rig(FakeBattery(silent_fail=True), settings=ControlSettings(min_write_interval_s=1))
    for i in range(3):
        decision = await r.submit(BatteryAction(max_discharge_w=0, min_soc_pct=20 + 5 * i))
        assert decision.kind is DecisionKind.FAILED
        assert keys(decision) == ["safety.readback_mismatch"]
        r.clock.advance(2)
        r.at(grid_w=600, battery_w=0, soc_pct=40)
    assert r.gate.watchdog.pause_reason(BATTERY_ASSET_ID, r.clock.now) is not None
    # The trip released it at once: the battery still shows its baseline (every write failed
    # silently), so there is nothing to write back.
    assert r.battery.released == []
    runtime = r.gate.runtime_of(BATTERY_ASSET_ID)
    assert runtime is not None
    assert not runtime.under_control


@pytest.mark.parametrize(
    ("action", "key"),
    [
        (BatteryAction(min_soc_pct=10), "safety.battery_min_soc_below_reserve"),
        (BatteryAction(grid_charge_w=4000), "safety.battery_grid_charge_bound"),
        (BatteryAction(max_charge_w=6000), "safety.battery_out_of_range"),
    ],
)
async def test_invariant_violations_are_rejected(action: BatteryAction, key: str) -> None:
    r = rig()
    decision = await r.submit(action)
    assert decision.kind is DecisionKind.REJECTED
    assert key in keys(decision)
    assert r.battery.applied == []


async def test_an_unplanned_lock_and_grid_charge_outside_the_policy_are_rejected() -> None:
    r = rig()
    r.plans["now"] = plan()  # no lock, no grid charging in this interval
    assert keys(await r.submit(BatteryAction(max_discharge_w=0))) == [
        "safety.battery_unplanned_import"
    ]
    r.plans["now"] = plan(grid_charge_max_w=3000)
    r.prices["now"] = 0.35
    assert keys(await r.submit(BatteryAction(grid_charge_w=2000))) == [
        "safety.battery_grid_charge_policy"
    ]
    r.prices["now"] = None
    assert keys(await r.submit(BatteryAction(grid_charge_w=2000))) == [
        "safety.battery_grid_charge_policy"
    ]
    assert r.battery.applied == []


async def test_missing_capability_and_adapter_refusal() -> None:
    r = rig(FakeBattery(caps=frozenset({BatteryCapability.MIN_SOC})))
    assert "safety.battery_capability_missing" in keys(
        await r.submit(BatteryAction(grid_charge_w=1))
    )
    refused = rig()
    from suncore.core.decisions import Reason  # noqa: PLC0415

    refused.battery.refuse = Reason(key="safety.battery_external_control_off", args={"value": "0"})
    decision = await refused.submit(BatteryAction(max_discharge_w=0))
    assert keys(decision) == ["safety.battery_external_control_off"]


async def test_assisted_proposes_and_a_confirmation_writes() -> None:
    r = rig(mode=OperatingMode.ASSISTED)
    proposal = await r.submit(BatteryAction(max_discharge_w=0))
    assert proposal.kind is DecisionKind.PROPOSED
    assert r.battery.applied == []
    confirmed = await r.gate.confirm(proposal.intent.id)
    assert confirmed is not None
    assert confirmed.kind is DecisionKind.ACCEPTED


async def test_an_unverified_hardware_fallback_caps_the_battery_at_assisted() -> None:
    battery = FakeBattery(caps=ALL | {BatteryCapability.HARDWARE_FALLBACK})
    r = rig(battery)
    assert r.gate.battery_mode_capped
    assert r.gate.modes_of(BATTERY_ASSET_ID).effective is OperatingMode.ASSISTED
    assert (await r.submit(BatteryAction(max_discharge_w=0))).kind is DecisionKind.PROPOSED
    verified = rig(
        FakeBattery(caps=ALL | {BatteryCapability.HARDWARE_FALLBACK}), fallback_verified=True
    )
    assert verified.gate.modes_of(BATTERY_ASSET_ID).effective is OperatingMode.AUTOMATIC


async def test_the_hardware_fallback_is_armed_and_confirmed_before_the_first_limit() -> None:
    battery = FakeBattery(caps=ALL | {BatteryCapability.HARDWARE_FALLBACK}, beat=20)
    r = rig(battery, fallback_verified=True)
    decision = await r.submit(BatteryAction(max_discharge_w=0))
    assert decision.kind is DecisionKind.ACCEPTED
    assert battery.armed
    unconfirmed = rig(
        FakeBattery(caps=ALL | {BatteryCapability.HARDWARE_FALLBACK}, armed_ok=False),
        fallback_verified=True,
    )
    failed = await unconfirmed.submit(BatteryAction(max_discharge_w=0))
    assert failed.kind is DecisionKind.FAILED
    assert keys(failed) == ["safety.battery_fallback_unconfirmed"]
    assert unconfirmed.battery.applied == []


# --- lease and heartbeat --------------------------------------------------------------------------


async def test_the_heartbeat_runs_only_while_the_lease_holds() -> None:
    battery = FakeBattery(caps=ALL | {BatteryCapability.HARDWARE_FALLBACK}, beat=20)
    r = rig(battery, fallback_verified=True)
    await r.submit(BatteryAction(max_discharge_w=0))
    for _ in range(12):  # 60 s
        await r.tick(5)
    assert 2 <= battery.beats <= 3
    # The plan interval stops asking for the lock: the lease ends, the baseline is restored.
    r.plans["now"] = plan(now=r.clock.now)
    decisions = await r.tick(5)
    assert [d.kind for d in decisions] == [DecisionKind.ACCEPTED]
    assert "safety.battery_unplanned_import" in keys(decisions[0])
    assert battery.released == [BASE]
    beats = battery.beats
    for _ in range(12):
        await r.tick(5)
    assert battery.beats == beats  # no heartbeat without a lease


async def test_a_stale_plan_ends_the_lease() -> None:
    r = rig()
    await r.submit(BatteryAction(max_discharge_w=0))
    for _ in range(4):
        assert await r.tick(5) == ()
    r.plans["now"] = r.plans["now"].model_copy(
        update={"created_at": r.clock.now - timedelta(minutes=40)}
    )
    decisions = await r.tick(5)
    assert decisions
    assert "safety.battery_plan_ended" in keys(decisions[0])
    assert r.battery.state == BASE


async def test_grid_charging_ends_when_the_live_price_leaves_the_policy() -> None:
    r = rig()
    decision = await r.submit(BatteryAction(grid_charge_w=2000))
    assert decision.kind is DecisionKind.ACCEPTED, keys(decision)
    assert await r.tick(5) == ()
    r.prices["now"] = 0.40
    decisions = await r.tick(5)
    assert "safety.battery_grid_charge_policy" in keys(decisions[0])
    assert r.battery.state == BASE


async def test_grid_charging_ends_at_the_import_limit() -> None:
    r = rig()
    await r.submit(BatteryAction(grid_charge_w=3000))
    r.at(grid_w=9000, battery_w=3000, soc_pct=40)  # the house alone now draws 6 kW
    r.inputs["now"] = GateInputs(
        flows=flows(grid_w=9000, battery_w=3000, soc_pct=40), import_limit_w=8000, ts=r.clock.now
    )
    decisions = await r.tick(5)
    assert decisions
    assert keys(decisions[0])[1] in (
        "safety.battery_grid_charge_bound",
        "safety.battery_import_limit",
    )


async def test_a_manual_change_is_an_override_and_is_respected() -> None:
    r = rig()
    await r.submit(BatteryAction(max_discharge_w=0))
    r.clock.advance(60)
    r.battery.state = BatteryAction(
        max_charge_w=5000, max_discharge_w=2500, min_soc_pct=30, grid_charge_w=0
    )
    decisions = await r.tick(5)
    assert [d.kind for d in decisions] == [DecisionKind.HELD]
    assert keys(decisions[0]) == ["safety.battery_override"]
    assert r.battery.released == []  # the user's change is not overwritten
    held = await r.submit(BatteryAction(max_discharge_w=0))
    assert held.kind is DecisionKind.HELD


# --- release paths ------------------------------------------------------------------------------


async def test_emergency_stop_restores_the_baseline_and_blocks() -> None:
    r = rig()
    await r.submit(BatteryAction(max_discharge_w=0))
    await r.gate.emergency_stop()
    assert r.battery.released == [BASE]
    assert r.battery.state == BASE
    assert keys(await r.submit(BatteryAction(max_discharge_w=0))) == ["safety.emergency_stop"]


async def test_mode_to_shadow_releases() -> None:
    r = rig()
    await r.submit(BatteryAction(max_discharge_w=0))
    r.modes.set_battery(OperatingMode.SHADOW)
    await r.gate.sync_modes()
    assert r.battery.released == [BASE]


async def test_shutdown_releases_within_the_deadline() -> None:
    r = rig()
    await r.submit(BatteryAction(max_discharge_w=0))
    await r.gate.shutdown()
    assert r.battery.released == [BASE]


async def test_crash_recovery_releases_what_a_crash_left() -> None:
    store = MemoryRuntimeStore()
    r = rig(store=store)
    await r.submit(BatteryAction(max_discharge_w=0))
    # the process dies; a new gate on the same store recovers
    restarted = rig(r.battery, store=store)
    await restarted.gate.recover()
    assert r.battery.released == [BASE]


async def test_a_remapped_battery_is_released_never_written() -> None:
    store = MemoryRuntimeStore()
    r = rig(store=store)
    await r.submit(BatteryAction(max_discharge_w=0))
    other = rig(FakeBattery(), store=store, identity="bat-2")
    await other.gate.recover()  # no actuator for the old battery: reported, dropped
    assert other.battery.released == []
    assert other.battery.applied == []
    runtime = other.gate.runtime_of(BATTERY_ASSET_ID)
    assert runtime is not None
    assert not runtime.under_control


async def test_a_controller_release_restores_the_baseline_and_is_verified() -> None:
    r = rig()
    await r.submit(BatteryAction(max_discharge_w=0))
    r.clock.advance(60)
    r.at(grid_w=600, battery_w=0, soc_pct=40)
    decision = await r.submit(ReleaseAction())
    assert decision.kind is DecisionKind.ACCEPTED
    assert r.battery.state == BASE
    runtime = r.gate.runtime_of(BATTERY_ASSET_ID)
    assert runtime is not None
    assert runtime.under_control  # until the read-back proves the baseline
    assert runtime.baseline == BASE
    decisions = [d for _ in range(3) for d in await r.tick(5)]
    assert [keys(d) for d in decisions] == [["safety.released", "safety.battery_release_confirmed"]]
    runtime = r.gate.runtime_of(BATTERY_ASSET_ID)
    assert runtime is not None
    assert not runtime.under_control


def _stuck_release(r: Rig) -> list[Action | None]:
    """The adapter accepts the release call, but the battery keeps SunCore's state."""
    calls: list[Action | None] = []

    async def ignore(baseline: Action | None) -> None:
        calls.append(baseline)

    r.battery.release = ignore  # type: ignore[method-assign]
    return calls


async def test_an_unconfirmed_release_is_latched_and_retried_with_back_off() -> None:
    """Review 4-G B1: the pause must hold across many healthy watchdog ticks, the stuck lock is
    never adopted as a new baseline, and the release is retried with back-off."""
    r = rig()
    await r.submit(BatteryAction(max_discharge_w=0))
    calls = _stuck_release(r)
    await r.gate.emergency_stop()
    await r.gate.clear_emergency_stop()
    rows: list[Decision] = []
    for _ in range(240):  # 20 minutes of ticks with healthy flows
        r.clock.advance(5)
        r.at(grid_w=600, battery_w=0, soc_pct=40)
        await r.gate.check_battery(flows(grid_w=600, battery_w=0, soc_pct=40))
        rows.extend(await r.gate.poll())
    unconfirmed = [d for d in rows if keys(d) == ["safety.battery_release_unconfirmed"]]
    assert 3 <= len(unconfirmed) <= 8  # back-off: not one row per tick
    assert len(calls) == len(unconfirmed) + 1
    assert all(c == BASE for c in calls)  # always the ORIGINAL baseline
    runtime = r.gate.runtime_of(BATTERY_ASSET_ID)
    assert runtime is not None
    assert runtime.under_control
    assert runtime.release_pending
    assert runtime.baseline == BASE
    # No new takeover while the release is unconfirmed - the stuck lock is never a baseline.
    r.plans["now"] = plan(now=r.clock.now)
    blocked = await r.submit(BatteryAction(max_charge_w=4000))
    assert keys(blocked) == ["safety.release_pending"]
    assert r.battery.applied == [
        BatteryAction(max_charge_w=5000, max_discharge_w=0, min_soc_pct=10, grid_charge_w=0)
    ]
    # The battery comes back: the next check confirms and control ends.
    r.battery.state = BASE
    r.clock.advance(700)
    r.at(grid_w=600, battery_w=0, soc_pct=40)
    done = await r.gate.poll()
    assert [keys(d) for d in done] == [["safety.released", "safety.battery_release_confirmed"]]
    runtime = r.gate.runtime_of(BATTERY_ASSET_ID)
    assert runtime is not None
    assert not runtime.under_control


async def test_a_failed_release_write_keeps_the_original_baseline_and_retries() -> None:
    r = rig()
    await r.submit(BatteryAction(max_discharge_w=0))

    async def unavailable(_: Action | None) -> None:
        msg = "number.max_discharge is unavailable"
        raise RuntimeError(msg)

    r.battery.release = unavailable  # type: ignore[method-assign]
    await r.gate.emergency_stop()  # the retry runs during the emergency stop too
    rows: list[Decision] = []
    for _ in range(60):
        r.clock.advance(5)
        rows.extend(await r.gate.poll())
    failed = [d for d in rows if d.kind is DecisionKind.FAILED]
    assert 2 <= len(failed) <= 5
    runtime = r.gate.runtime_of(BATTERY_ASSET_ID)
    assert runtime is not None
    assert runtime.under_control
    assert runtime.baseline == BASE


async def test_a_user_change_during_the_release_is_kept() -> None:
    r = rig()
    await r.submit(BatteryAction(max_discharge_w=0))
    _stuck_release(r)
    await r.gate.emergency_stop()
    user = BatteryAction(max_charge_w=5000, max_discharge_w=3000, min_soc_pct=30, grid_charge_w=0)
    r.battery.state = user
    done = [d for _ in range(4) for d in await r.tick(5)]
    assert [keys(d) for d in done] == [["safety.released", "safety.battery_override"]]
    assert r.battery.state == user
    # Review 4-G re-check, minor 2: the user's choice is held like a lease override (04:00).
    runtime = r.gate.runtime_of(BATTERY_ASSET_ID)
    assert runtime is not None
    assert runtime.override
    assert runtime.override_until == overrides.resume_at(OverridePolicy(), done[0].ts, UTC)
    assert runtime.is_held(r.clock.now)
    await r.gate.clear_emergency_stop()
    assert (await r.submit(BatteryAction(max_discharge_w=0))).kind is DecisionKind.HELD
    assert r.battery.state == user


async def test_a_late_landing_own_write_is_never_taken_for_a_user_value() -> None:
    """Review 4-G re-check (reviewer probe 3): a pending write that lands after its read-back
    window, behind an earlier confirmed write, is SunCore's state - the release writes the
    original baseline and waits for the read-back instead of keeping SunCore's 45 % floor."""
    r = rig()
    first = await r.submit(BatteryAction(max_discharge_w=0))
    assert first.kind is DecisionKind.ACCEPTED, keys(first)
    r.clock.advance(120)
    r.at(grid_w=600, battery_w=0, soc_pct=40)
    r.battery.silent_fail = True
    late = await r.submit(BatteryAction(min_soc_pct=45))
    assert late.kind is DecisionKind.FAILED, keys(late)
    runtime = r.gate.runtime_of(BATTERY_ASSET_ID)
    assert runtime is not None
    assert isinstance(late.intent.action, BatteryAction)
    r.battery.state = resolve_battery_action(late.intent.action, runtime.baseline)  # lands late
    r.battery.silent_fail = False
    await r.gate.emergency_stop()
    assert r.battery.released == [BASE]
    assert r.battery.state == BASE
    runtime = r.gate.runtime_of(BATTERY_ASSET_ID)
    assert runtime is not None
    assert not runtime.override  # nothing was the user's
    assert "safety.battery_override" not in [k for d in r.decisions for k in keys(d)]


async def test_a_partial_own_write_is_restored_and_only_the_users_field_is_kept() -> None:
    """Review 4-G re-check, mixed multi-field case: one field still shows SunCore's partly
    applied write, another the user's value - SunCore's goes back to the baseline, the user's
    stays, the result is proven by read-back and held as an override."""
    r = rig()
    await r.submit(BatteryAction(max_discharge_w=0))
    r.clock.advance(120)
    r.at(grid_w=600, battery_w=0, soc_pct=40)

    async def partial(intent: Intent) -> str | None:  # only the SoC floor reaches the battery
        assert isinstance(intent.action, BatteryAction)
        assert isinstance(r.battery.state, BatteryAction)
        r.battery.state = r.battery.state.model_copy(
            update={"min_soc_pct": intent.action.min_soc_pct}
        )
        msg = "number.max_discharge did not answer"
        raise RuntimeError(msg)

    r.battery.apply = partial  # type: ignore[method-assign]
    failed = await r.submit(BatteryAction(max_discharge_w=1000, min_soc_pct=45))
    assert failed.kind is DecisionKind.FAILED, keys(failed)
    assert isinstance(r.battery.state, BatteryAction)
    # SunCore's 0 W cap and 45 % floor are on the battery; the user lowers the charge cap.
    r.battery.state = r.battery.state.model_copy(update={"max_charge_w": 3000})
    await r.gate.emergency_stop()
    kept = BASE.model_copy(update={"max_charge_w": 3000})
    assert r.battery.released == [kept]
    assert r.battery.state == kept
    runtime = r.gate.runtime_of(BATTERY_ASSET_ID)
    assert runtime is not None
    assert runtime.under_control  # until the read-back confirms
    assert runtime.override
    done = [d for _ in range(6) for d in await r.tick(5)]
    assert [keys(d) for d in done] == [["safety.released", "safety.battery_release_confirmed"]]
    runtime = r.gate.runtime_of(BATTERY_ASSET_ID)
    assert runtime is not None
    assert not runtime.under_control
    assert runtime.attempted == ()
    assert runtime.is_held(r.clock.now)


async def test_a_partial_own_write_without_user_change_restores_the_full_baseline() -> None:
    r = rig()
    await r.submit(BatteryAction(max_discharge_w=0))
    r.clock.advance(120)
    r.at(grid_w=600, battery_w=0, soc_pct=40)
    landed = BASE.model_copy(update={"max_discharge_w": 1000})  # half of a two-field write

    async def partial(_: Intent) -> str | None:
        r.battery.state = landed
        msg = "timeout"
        raise RuntimeError(msg)

    r.battery.apply = partial  # type: ignore[method-assign]
    await r.submit(BatteryAction(max_discharge_w=1000, min_soc_pct=45))
    await r.gate.emergency_stop()
    assert r.battery.released == [BASE]
    assert r.battery.state == BASE
    runtime = r.gate.runtime_of(BATTERY_ASSET_ID)
    assert runtime is not None
    assert not runtime.override


async def test_a_late_landing_earlier_write_is_no_lease_override() -> None:
    """The same class on the lease: an earlier failed write that lands behind a confirmed one
    is SunCore's own value - no override, the next write brings the held state back."""
    r = rig()
    r.battery.silent_fail = True
    stale = await r.submit(BatteryAction(max_discharge_w=0))
    assert stale.kind is DecisionKind.FAILED, keys(stale)
    r.battery.silent_fail = False
    r.clock.advance(120)
    r.at(grid_w=600, battery_w=0, soc_pct=40)
    held = await r.submit(BatteryAction(min_soc_pct=20))
    assert held.kind is DecisionKind.ACCEPTED, keys(held)
    r.battery.state = BASE.model_copy(update={"max_discharge_w": 0})  # the stale write lands
    r.clock.advance(60)
    r.at(grid_w=600, battery_w=0, soc_pct=40)
    rows = await r.tick(5)
    assert "safety.battery_override" not in [k for d in rows for k in keys(d)]
    runtime = r.gate.runtime_of(BATTERY_ASSET_ID)
    assert runtime is not None
    assert runtime.under_control
    assert not runtime.override
    again = await r.submit(BatteryAction(min_soc_pct=20))
    assert again.kind is DecisionKind.ACCEPTED, keys(again)
    assert r.battery.state == BASE.model_copy(update={"min_soc_pct": 20})


# --- watchdog (B8) ------------------------------------------------------------------------------


async def test_a_frozen_battery_while_the_grid_moves_releases_and_pauses() -> None:
    r = rig()
    await r.submit(BatteryAction(max_discharge_w=0))
    reason = None
    for i in range(100):
        r.clock.advance(5)
        grid = 300.0 + (900.0 if (i // 6) % 2 else 0.0)
        reason = await r.gate.check_battery(flows(grid_w=grid, battery_w=-800, soc_pct=40))
        if reason is not None:
            break
    assert reason is not None
    assert reason.key == "safety.battery_frozen"
    assert r.battery.released == [BASE]
    assert r.gate.watchdog.pause_reason(BATTERY_ASSET_ID, r.clock.now) is not None


async def test_a_device_never_sees_the_battery_path() -> None:
    """Phase 3 device rules unchanged: a device intent still goes through the device path."""
    r = rig()
    decision = await r.gate.submit(
        make_intent(SwitchAction(on=True), asset_id="pool", ts=r.clock.now)
    )
    assert keys(decision) == ["safety.shadow_mode"]  # no mode for the device: shadow


async def test_no_new_takeover_until_the_release_is_confirmed() -> None:
    r = rig()
    await r.submit(BatteryAction(max_discharge_w=0))
    r.clock.advance(60)
    r.at(grid_w=600, battery_w=0, soc_pct=40)
    await r.submit(ReleaseAction())
    r.clock.advance(6)
    r.at(grid_w=600, battery_w=0, soc_pct=40)
    early = await r.submit(BatteryAction(max_discharge_w=0))
    assert keys(early) == ["safety.release_pending"]
    await r.tick(5)  # settle time over: confirmed
    r.clock.advance(60)
    r.at(grid_w=600, battery_w=0, soc_pct=40)
    again = await r.submit(BatteryAction(max_discharge_w=0))
    assert again.kind is DecisionKind.ACCEPTED
    runtime = r.gate.runtime_of(BATTERY_ASSET_ID)
    assert runtime is not None
    assert runtime.baseline == BASE


# --- review 4-G regressions ------------------------------------------------------------------


async def test_an_inherited_stale_discharge_lock_counts_as_a_lock() -> None:
    """Review M1 (probe): a baseline max_discharge_w = 0 is written with any action, so it is a
    discharge lock that needs the plan's ``discharge_lock``."""
    stale = BatteryAction(max_charge_w=5000, max_discharge_w=0, min_soc_pct=10, grid_charge_w=0)
    r = rig(FakeBattery(state=stale))
    r.plans["now"] = plan()  # no discharge lock, no grid charging
    r.at(grid_w=1500, battery_w=0, soc_pct=60)  # the house needs 1.5 kW
    decision = await r.submit(BatteryAction(max_charge_w=2000))
    assert decision.kind is DecisionKind.REJECTED
    assert "safety.battery_unplanned_import" in keys(decision)
    assert r.battery.applied == []


async def test_the_takeover_baseline_comes_from_the_adapter_not_the_limit_registers() -> None:
    """Review M1: Kostal §3.5 - the None fields mean the battery's own limits, not a stale 1282."""
    stale = BatteryAction(max_charge_w=5000, max_discharge_w=0)
    own = BatteryAction(max_charge_w=5000, max_discharge_w=5000)
    r = rig(
        FakeBattery(
            caps=frozenset({BatteryCapability.CHARGE_LIMIT, BatteryCapability.DISCHARGE_LIMIT}),
            state=stale,
            own=own,
        )
    )
    r.plans["now"] = plan()
    r.at(grid_w=-2000, battery_w=0, soc_pct=60)  # export: a charge cap cannot cause import
    decision = await r.submit(BatteryAction(max_charge_w=2000))
    assert decision.kind is DecisionKind.ACCEPTED, keys(decision)
    assert r.battery.applied == [BatteryAction(max_charge_w=2000, max_discharge_w=5000)]
    runtime = r.gate.runtime_of(BATTERY_ASSET_ID)
    assert runtime is not None
    assert runtime.baseline == own


async def test_a_refusing_adapter_ends_the_lease_and_its_heartbeat() -> None:
    """Review minor 2: no heartbeat into a refusing inverter; the lease ends with the reason."""
    from suncore.core.decisions import Reason  # noqa: PLC0415

    battery = FakeBattery(caps=ALL | {BatteryCapability.HARDWARE_FALLBACK}, beat=20)
    r = rig(battery, fallback_verified=True)
    await r.submit(BatteryAction(max_discharge_w=0))
    battery.refuse = Reason(key="safety.battery_external_control_off", args={"value": "0"})
    decisions = await r.tick(5)
    assert decisions
    assert "safety.battery_external_control_off" in keys(decisions[0])
    beats = battery.beats
    for _ in range(10):
        await r.tick(5)
    assert battery.beats == beats


async def test_the_lease_needs_a_fresh_plan_also_for_states_without_import() -> None:
    """Review minor 8 (ADR-0013 5B): a charge cap is held only while a plan interval covers it."""
    r = rig()
    r.at(grid_w=-2000, battery_w=0, soc_pct=60)
    r.plans["now"] = plan(now=r.clock.now)
    decision = await r.submit(BatteryAction(max_charge_w=2000))
    assert decision.kind is DecisionKind.ACCEPTED, keys(decision)
    r.plans["now"] = Plan.default()
    decisions = await r.tick(5)
    assert decisions
    assert "safety.battery_plan_ended" in keys(decisions[0])


async def test_an_emergency_stop_is_not_delayed_by_the_fallback_settle_wait() -> None:
    """Review minor 3: the settle wait after arming runs outside the lock; the stop wins and
    nothing is written after it."""
    import asyncio  # noqa: PLC0415

    battery = FakeBattery(caps=ALL | {BatteryCapability.HARDWARE_FALLBACK}, settle=15)
    r = rig(battery, fallback_verified=True)
    waiting = asyncio.Event()
    proceed = asyncio.Event()

    async def slow_sleep(seconds: float) -> None:
        r.clock.advance(seconds)
        waiting.set()
        await proceed.wait()

    r.gate._sleep = slow_sleep
    write = asyncio.ensure_future(r.submit(BatteryAction(max_discharge_w=0)))
    await waiting.wait()
    stop = asyncio.ensure_future(r.gate.emergency_stop())
    await asyncio.wait_for(asyncio.shield(stop), timeout=1.0)  # not blocked by the wait
    proceed.set()
    decision = await write
    assert decision.kind is DecisionKind.REJECTED
    assert keys(decision) == ["safety.emergency_stop"]
    assert battery.applied == []
