# SPDX-License-Identifier: Apache-2.0
"""The export chain (PR 6-G, ADR-0018 decision 3): when the inverter is limited, the chain
order, pacing, release, X2 in the controller and the oscillation count under cloud flicker."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.controller.export import (
    DeviceHeadroom,
    ExportChain,
    ExportControlInputs,
    ExportControlSettings,
    absorb_headroom_w,
    export_over_limit,
    lift_battery_charge_cap,
    needed_limit_w,
)
from suncore.controller.grid import GridInputs
from suncore.core.balance import Flow, FlowSnapshot
from suncore.core.decisions import (
    BatteryAction,
    Decision,
    DecisionKind,
    Intent,
    InverterLimitAction,
    OperatingMode,
    Reason,
    ReleaseAction,
)
from suncore.core.grid import GridState
from suncore.core.inverter import NO_IMPORT_MARGIN_W
from suncore.core.signals import Quality

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
STATIC = 13_500.0
ZERO_GRID = GridInputs(export_limit_w=0.0, grid_state=GridState.CONNECTED)


def site(
    export_w: float,
    house_w: float = 1000.0,
    battery_w: float = 0.0,
    quality: Quality = Quality.OK,
) -> FlowSnapshot:
    def flow(value: float) -> Flow:
        return Flow(value, quality)

    return FlowSnapshot(
        grid_w=flow(-export_w),
        pv_w=flow(export_w + house_w + battery_w),
        battery_w=flow(battery_w),
        house_w=flow(house_w),
        battery_soc_pct=flow(50.0),
        ts=T0,
    )


def inputs(**kw: object) -> ExportControlInputs:
    base: dict[str, object] = {
        "capability": "export_limit",
        "nominal_ac_w": 10_000.0,
        "static_limit_w": STATIC,
        "verified": True,
    }
    base.update(kw)
    return ExportControlInputs(**base)  # type: ignore[arg-type]


def decide(intent: Intent, kind: DecisionKind = DecisionKind.ACCEPTED) -> Decision:
    return Decision(
        intent=intent,
        kind=kind,
        mode=OperatingMode.AUTOMATIC,
        ts=intent.ts,
        reasons=(),
    )


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


# --- pure helpers ---------------------------------------------------------------------------------


def test_the_static_limit_stays_in_the_inverter() -> None:
    static_only = GridInputs(export_limit_w=STATIC)
    assert needed_limit_w(static_only, STATIC, enforce_static=False) is None
    assert needed_limit_w(static_only, STATIC, enforce_static=True) == STATIC
    assert needed_limit_w(ZERO_GRID, STATIC, enforce_static=False) == 0.0
    assert needed_limit_w(GridInputs(), STATIC, enforce_static=True) is None


def test_export_over_limit_uses_ok_readings_only() -> None:
    assert export_over_limit(site(500.0), ZERO_GRID)
    assert not export_over_limit(site(500.0, quality=Quality.STALE), ZERO_GRID)
    assert not export_over_limit(site(0.5), ZERO_GRID)
    assert not export_over_limit(site(500.0), GridInputs())


def test_chain_step_2_lifts_only_suncores_own_charge_cap() -> None:
    capped = BatteryAction(max_charge_w=1000.0, max_discharge_w=0.0)
    lifted, done = lift_battery_charge_cap(capped, over_limit=True)
    assert done
    assert lifted == BatteryAction(max_discharge_w=0.0)
    assert lift_battery_charge_cap(capped, over_limit=False) == (capped, False)
    assert lift_battery_charge_cap(BatteryAction(max_charge_w=1000.0), over_limit=True) == (
        None,
        True,
    )
    assert lift_battery_charge_cap(None, over_limit=True) == (None, False)


def test_headroom_counts_rising_devices_and_a_capped_battery() -> None:
    rising = [DeviceHeadroom("a", rising=True), DeviceHeadroom("b", rising=False)]
    assert absorb_headroom_w(rising, None, None) == 100.0
    capped = BatteryAction(max_charge_w=1000.0)
    assert absorb_headroom_w([], capped, 1000.0, battery_high_w=5000.0) == 4000.0
    assert absorb_headroom_w([], capped, 200.0, battery_high_w=5000.0) == 0.0  # not at the cap
    assert absorb_headroom_w([], capped, 1000.0, battery_blocked=True) == 0.0
    assert absorb_headroom_w([], None, 3000.0) == 0.0  # the inverter charges by itself


# --- the chain -----------------------------------------------------------------------------------


def test_no_limit_while_the_static_limit_suffices() -> None:
    chain = ExportChain()
    grid = GridInputs(export_limit_w=STATIC)
    assert chain.step(T0, inputs(), site(9000.0), grid, 0.0) is None
    assert chain.view is not None
    assert [r.key for r in chain.view.reasons] == ["controller.export.not_needed"]


def test_curtailing_waits_for_the_chain_and_the_delay() -> None:
    chain = ExportChain(ExportControlSettings(curtail_delay_s=120))
    flows = site(3000.0)
    assert chain.step(at(0), inputs(), flows, ZERO_GRID, 500.0) is None
    assert chain.view is not None
    assert chain.view.reasons[0].key == "controller.export.absorbing"
    assert chain.step(at(60), inputs(), flows, ZERO_GRID, 0.0) is None
    assert chain.view.reasons[0].key == "controller.export.waiting"
    intent = chain.step(at(125), inputs(), flows, ZERO_GRID, 0.0)
    assert intent is not None
    assert intent.asset_id == "inverter"
    assert intent.action == InverterLimitAction(max_export_w=0.0)
    assert intent.reasons[0] == Reason(key="controller.export.limit", args={"limit_w": 0})


def test_an_unverified_export_limit_keeps_house_load_and_margin_like_the_gate() -> None:
    chain = ExportChain(ExportControlSettings(curtail_delay_s=0))
    flows = site(3000.0, house_w=1234.0, battery_w=400.0)
    intent = chain.step(at(0), inputs(verified=False), flows, ZERO_GRID, 0.0)
    assert intent is not None
    assert isinstance(intent.action, InverterLimitAction)
    assert intent.action.max_export_w == 2200.0  # ceil((1234 + 400 + 500) / 100) * 100


def test_an_ac_limit_leaves_house_charging_and_margin() -> None:
    chain = ExportChain(ExportControlSettings(curtail_delay_s=0))
    ac = inputs(capability="ac_limit")
    flows = site(3000.0, house_w=1234.0, battery_w=400.0)
    intent = chain.step(at(0), ac, flows, ZERO_GRID, 0.0)
    assert intent is not None
    assert isinstance(intent.action, InverterLimitAction)
    assert intent.action.max_ac_pct == 22.0  # (1234 + 400 + 500) / 10 kW, rounded up


def test_release_when_no_longer_needed_and_urgent_on_stale_inputs() -> None:
    chain = ExportChain(ExportControlSettings(curtail_delay_s=0, interval_s=60))
    held = InverterLimitAction(max_export_w=0.0)
    under = inputs(held=held, under_control=True)
    first = chain.step(at(0), inputs(), site(3000.0), ZERO_GRID, 0.0)
    first = first or chain.step(at(1), inputs(), site(3000.0), ZERO_GRID, 0.0)
    assert first is not None
    chain.report(decide(first))
    stale = chain.step(at(5), under, site(3000.0, quality=Quality.STALE), ZERO_GRID, 0.0)
    assert stale is not None  # urgent: not paced
    assert stale.action == ReleaseAction()
    assert stale.reasons[1].key == "controller.export.inputs_unknown"
    chain.report(decide(stale))
    chain2 = ExportChain()
    gone = chain2.step(at(0), under, site(3000.0), GridInputs(export_limit_w=STATIC), 0.0)
    assert gone is not None
    assert gone.action == ReleaseAction()


def test_island_releases_at_once() -> None:
    chain = ExportChain()
    held = inputs(held=InverterLimitAction(max_export_w=0.0), under_control=True)
    island = GridInputs(export_limit_w=0.0, grid_state=GridState.ISLAND)
    intent = chain.step(at(0), held, site(0.0), island, 0.0)
    assert intent is not None
    assert intent.action == ReleaseAction()


def test_a_looser_limit_goes_out_without_waiting_for_the_chain() -> None:
    chain = ExportChain(ExportControlSettings(curtail_delay_s=600))
    held = inputs(
        capability="ac_limit",
        held=InverterLimitAction(max_ac_pct=20.0),
        under_control=True,
    )
    # the house load rose: 20 % no longer covers house + margin -> a higher % at once
    intent = chain.step(at(0), held, site(0.0, house_w=3000.0), ZERO_GRID, 5000.0)
    assert intent is not None
    assert intent.action == InverterLimitAction(max_ac_pct=35.0)


def test_blocked_and_pending_submit_nothing() -> None:
    chain = ExportChain(ExportControlSettings(curtail_delay_s=0))
    block = Reason(key="safety.emergency_stop")
    assert chain.step(at(0), inputs(blocked=block), site(3000.0), ZERO_GRID, 0.0) is None
    first = chain.step(at(1), inputs(), site(3000.0), ZERO_GRID, 0.0)
    assert first is not None
    assert chain.step(at(200), inputs(), site(3000.0), ZERO_GRID, 0.0) is None  # pending
    chain.reset_pending()
    assert chain.step(at(201), inputs(), site(3000.0), ZERO_GRID, 0.0) is not None


def test_the_same_proposal_repeats_at_most_every_repeat_s() -> None:
    chain = ExportChain(ExportControlSettings(curtail_delay_s=0, interval_s=60, repeat_s=900))
    sent = 0
    for second in range(0, 1800, 5):
        intent = chain.step(at(second), inputs(), site(3000.0), ZERO_GRID, 0.0)
        if intent is not None:
            sent += 1
            chain.report(decide(intent, DecisionKind.PROPOSED))
    assert sent == 2


# --- oscillation ---------------------------------------------------------------------------------


def _flicker_run(seed_pattern: list[float], *, capability: str) -> int:
    """Clouds: export jumps between high and almost nothing every few seconds for an hour.
    The fake gate accepts every intent; returns how many limit changes were written."""
    chain = ExportChain()
    held: InverterLimitAction | None = None
    writes = 0
    house = 1500.0
    for tick in range(720):  # one hour at 5 s
        now = at(tick * 5)
        export = seed_pattern[tick % len(seed_pattern)]
        if held is not None:
            limit_export = (
                held.max_export_w
                if held.max_export_w is not None
                else max(0.0, (held.max_ac_pct or 0.0) / 100 * 10_000 - house)
            )
            export = min(export, limit_export)
        state = inputs(capability=capability, held=held, under_control=held is not None)
        intent = chain.step(now, state, site(export, house_w=house), ZERO_GRID, 0.0)
        if intent is None:
            continue
        chain.report(decide(intent))
        held = intent.action if isinstance(intent.action, InverterLimitAction) else None
        writes += 1
    return writes


def test_cloud_flicker_does_not_oscillate_the_export_limit() -> None:
    pattern = [4000.0, 4000.0, 50.0, 3500.0, 0.0, 0.0, 4200.0, 100.0]
    assert _flicker_run(pattern, capability="export_limit") <= 1
    assert _flicker_run(pattern, capability="ac_limit") <= 1
    # export above the limit throughout, but jumpy: limited exactly once and then held
    always_over = [4000.0, 300.0, 3500.0, 100.0, 5000.0]
    assert _flicker_run(always_over, capability="export_limit") == 1
    assert _flicker_run(always_over, capability="ac_limit") == 1


@settings(max_examples=60, deadline=None)
@given(
    st.lists(st.floats(min_value=0, max_value=8000, allow_nan=False), min_size=2, max_size=40),
    st.lists(st.floats(min_value=200, max_value=6000, allow_nan=False), min_size=1, max_size=20),
)
def test_limit_changes_per_hour_are_bounded_and_never_cut_below_the_house(
    exports: list[float], houses: list[float]
) -> None:
    chain = ExportChain()
    held: InverterLimitAction | None = None
    writes = 0
    for tick in range(720):
        house = houses[tick % len(houses)]
        state = inputs(capability="ac_limit", held=held, under_control=held is not None)
        flows = site(exports[tick % len(exports)], house_w=house)
        intent = chain.step(at(tick * 5), state, flows, ZERO_GRID, 0.0)
        if intent is None:
            continue
        chain.report(decide(intent))
        writes += 1
        if isinstance(intent.action, InverterLimitAction):
            pct = intent.action.max_ac_pct
            assert pct is not None
            # X2 in the controller: never below house + margin at the moment it was asked
            assert pct / 100 * 10_000 + 1e-6 >= house + NO_IMPORT_MARGIN_W or pct == 100
            held = intent.action
        else:
            held = None
    # at most one new intent per interval_s (60 s): 60 per hour
    assert writes <= math.ceil(3600 / ExportControlSettings().interval_s) + 1
