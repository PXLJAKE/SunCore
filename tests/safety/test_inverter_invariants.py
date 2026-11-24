# SPDX-License-Identifier: Apache-2.0
"""Property tests for the inverter limit invariants X1, X2, X3, X5 (``safety.inverter``) and
the shared conversions in ``core.inverter`` (PR 6-G, ADR-0018 decision 3)."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from suncore.core.balance import Flow, FlowSnapshot
from suncore.core.decisions import InverterLimitAction
from suncore.core.grid import GridState
from suncore.core.inverter import (
    EXPORT_TOLERANCE_W,
    LimitCapability,
    ac_limit_w,
    export_cap_w,
    looser,
    pct_for_ac_w,
    same_limit,
)
from suncore.core.signals import Quality
from suncore.safety.inverter import (
    LEASE_MARGIN_SHARE,
    InverterContext,
    InverterGateConfig,
    InverterLive,
    evaluate_inverter,
    lease_violations,
    no_import_floor_w,
)

NOMINAL = 10_000.0
watts = st.floats(min_value=0, max_value=20_000, allow_nan=False)
signed = st.floats(min_value=-15_000, max_value=15_000, allow_nan=False)
pcts = st.floats(min_value=0, max_value=100, allow_nan=False)
maybe = st.one_of(st.none(), st.floats(min_value=0, max_value=20_000, allow_nan=False))


@st.composite
def limits(draw: st.DrawFn, capability: str | None = None) -> InverterLimitAction:
    cap = capability or draw(st.sampled_from(["export_limit", "ac_limit"]))
    if cap == "export_limit":
        return InverterLimitAction(max_export_w=draw(watts))
    return InverterLimitAction(max_ac_pct=draw(pcts))


@st.composite
def contexts(draw: st.DrawFn) -> InverterContext:
    cap: LimitCapability = draw(st.sampled_from(["export_limit", "ac_limit"]))
    config = InverterGateConfig(
        identity="inv",
        capability=cap,
        verified=draw(st.booleans()),
        nominal_ac_w=draw(st.sampled_from([None, NOMINAL])),
        static_limit_w=draw(st.one_of(st.none(), watts)),
    )
    live = InverterLive(
        grid_w=draw(st.one_of(st.none(), signed)),
        pv_w=draw(st.one_of(st.none(), watts)),
        house_w=draw(st.one_of(st.none(), watts)),
        battery_w=draw(st.one_of(st.none(), signed)),
        fresh=draw(st.booleans()),
    )
    requested = draw(limits(draw(st.sampled_from([cap, None])) or None))
    baseline = draw(st.one_of(st.none(), limits(cap)))
    return InverterContext(
        requested=requested,
        config=config,
        live=live,
        baseline=baseline,
        limit_range=(0.0, 100.0 if cap == "ac_limit" else 20_000.0),
        headroom_w=draw(st.one_of(st.none(), st.sampled_from([0.0, 0.5, 100.0, 3000.0]))),
        grid_state=draw(st.sampled_from(list(GridState))),
    )


@given(contexts())
def test_x5_a_written_limit_had_fresh_ok_inputs_and_no_island(ctx: InverterContext) -> None:
    if not evaluate_inverter(ctx):
        assert ctx.live.known
        assert ctx.grid_state is not GridState.ISLAND


@given(contexts())
def test_x1_a_written_limit_is_never_looser_than_baseline_or_static(ctx: InverterContext) -> None:
    if evaluate_inverter(ctx):
        return
    assert ctx.baseline is not None
    assert not looser(ctx.requested, ctx.baseline)
    static = ctx.config.static_limit_w
    if ctx.requested.max_export_w is not None and static is not None:
        assert ctx.requested.max_export_w <= static + EXPORT_TOLERANCE_W


@given(contexts())
def test_x2_an_ac_or_unverified_limit_never_cuts_below_house_and_charging(
    ctx: InverterContext,
) -> None:
    if evaluate_inverter(ctx):
        return
    floor = no_import_floor_w(ctx)
    assert floor is not None
    house, battery = ctx.live.house_w, ctx.live.battery_w
    assert house is not None
    assert battery is not None
    if ctx.requested.max_ac_pct is not None:
        ac = ac_limit_w(ctx.requested, ctx.config.nominal_ac_w)
        assert ac is not None
        assert ac + 1e-6 >= house + max(0.0, battery) + ctx.config.margin_w
    elif not ctx.config.verified:
        assert ctx.requested.max_export_w is not None
        assert ctx.requested.max_export_w + 1e-6 >= floor


@given(contexts())
def test_x3_curtailing_below_the_export_needs_an_exhausted_chain(ctx: InverterContext) -> None:
    if evaluate_inverter(ctx):
        return
    export = ctx.live.export_w
    cap = export_cap_w(ctx.requested, ctx.config.nominal_ac_w, ctx.live.house_w)
    assert export is not None
    assert cap is not None
    if cap < export - EXPORT_TOLERANCE_W:
        assert ctx.headroom_w is not None
        assert ctx.headroom_w <= EXPORT_TOLERANCE_W


@given(contexts())
def test_the_lease_ends_whenever_inputs_or_bounds_fail(ctx: InverterContext) -> None:
    lease = lease_violations(ctx)
    if ctx.grid_state is GridState.ISLAND or not ctx.live.known:
        assert lease
    if lease:
        # whatever ends a lease also stops a new write of the same limit
        assert evaluate_inverter(ctx)


@given(contexts())
def test_x2_a_held_ac_limit_keeps_at_least_half_the_margin(ctx: InverterContext) -> None:
    """The lease is looser than a new write only by half the margin (no oscillation), never by
    more: a held AC (or unverified) limit still leaves house + charging + margin / 2."""
    if lease_violations(ctx):
        return
    house, battery = ctx.live.house_w, ctx.live.battery_w
    assert house is not None
    assert battery is not None
    need = house + max(0.0, battery) + ctx.config.margin_w * LEASE_MARGIN_SHARE
    if ctx.requested.max_ac_pct is not None:
        ac = ac_limit_w(ctx.requested, ctx.config.nominal_ac_w)
        assert ac is not None
        assert ac + 1e-6 >= need
    elif not ctx.config.verified:
        assert ctx.requested.max_export_w is not None
        assert ctx.requested.max_export_w + 1e-6 >= need
    # whatever passes a new write also keeps its lease
    if not evaluate_inverter(ctx):
        assert not lease_violations(ctx)


@given(contexts())
def test_evaluation_is_deterministic(ctx: InverterContext) -> None:
    assert evaluate_inverter(ctx) == evaluate_inverter(ctx)


# --- the shared conversions --------------------------------------------------------------------


@given(st.floats(min_value=0, max_value=NOMINAL, allow_nan=False))
def test_pct_for_ac_w_never_allows_less_than_asked(ac_w: float) -> None:
    pct = pct_for_ac_w(ac_w, NOMINAL)
    assert pct == int(pct)
    assert pct / 100.0 * NOMINAL + 1e-6 >= ac_w
    assert pct - 1 < ac_w / NOMINAL * 100.0 + 1e-6  # the smallest such percent


@given(limits(), limits())
def test_same_limit_is_symmetric_and_excludes_looser(
    a: InverterLimitAction, b: InverterLimitAction
) -> None:
    assert same_limit(a, b) == same_limit(b, a)
    assert same_limit(a, a)
    if same_limit(a, b):
        assert not looser(a, b)
        assert not looser(b, a)
    assert not (looser(a, b) and looser(b, a))


@given(limits("ac_limit"), st.one_of(st.none(), watts))
def test_export_cap_of_an_ac_limit_is_ac_minus_house(
    limit: InverterLimitAction, house: float | None
) -> None:
    cap = export_cap_w(limit, NOMINAL, house)
    if house is None:
        assert cap is None
    else:
        assert cap is not None
        assert cap >= 0.0
        ac = ac_limit_w(limit, NOMINAL)
        assert ac is not None
        assert abs(cap - max(0.0, ac - house)) < 1e-6


def test_live_values_from_not_ok_readings_are_unknown() -> None:
    def flow(value: float, quality: Quality = Quality.OK) -> Flow:
        return Flow(value, quality)

    snapshot = FlowSnapshot(
        grid_w=flow(-3000.0),
        pv_w=flow(5000.0, Quality.STALE),
        battery_w=flow(0.0),
        house_w=flow(2000.0),
        battery_soc_pct=flow(50.0),
    )
    live = InverterLive.of(snapshot, fresh=True)
    assert live.pv_w is None
    assert not live.known
    assert live.export_w == 3000.0
    assert not InverterLive.of(None, fresh=True).known


# --- examples: each rule passes and fails where it should ------------------------------------


def _ctx(requested: InverterLimitAction, **kw: object) -> InverterContext:
    capability: LimitCapability = (
        "export_limit" if requested.max_export_w is not None else "ac_limit"
    )
    config = InverterGateConfig(
        identity="inv",
        capability=capability,
        verified=bool(kw.pop("verified", True)),
        nominal_ac_w=NOMINAL,
        static_limit_w=13_500.0,
    )
    live = InverterLive(
        grid_w=float(kw.pop("grid_w", -6000.0)),  # type: ignore[arg-type]
        pv_w=8000.0,
        house_w=float(kw.pop("house_w", 2000.0)),  # type: ignore[arg-type]
        battery_w=float(kw.pop("battery_w", 0.0)),  # type: ignore[arg-type]
        fresh=True,
    )
    baseline = kw.pop(
        "baseline",
        InverterLimitAction(max_export_w=13_500.0)
        if capability == "export_limit"
        else InverterLimitAction(max_ac_pct=100.0),
    )
    return InverterContext(
        requested=requested,
        config=config,
        live=live,
        baseline=baseline,  # type: ignore[arg-type]
        limit_range=(0.0, 100.0 if capability == "ac_limit" else 20_000.0),
        headroom_w=kw.pop("headroom_w", 0.0),  # type: ignore[arg-type]
        grid_state=kw.pop("grid_state", GridState.CONNECTED),  # type: ignore[arg-type]
    )


def _keys(ctx: InverterContext) -> list[str]:
    return [r.key for r in evaluate_inverter(ctx)]


def test_a_verified_zero_export_limit_passes_when_the_chain_is_exhausted() -> None:
    assert _keys(_ctx(InverterLimitAction(max_export_w=0.0))) == []


def test_x3_devices_that_can_still_absorb_block_curtailing() -> None:
    keys = _keys(_ctx(InverterLimitAction(max_export_w=0.0), headroom_w=1500.0))
    assert keys == ["safety.inverter_chain_not_exhausted"]
    keys = _keys(_ctx(InverterLimitAction(max_export_w=0.0), headroom_w=None))
    assert keys == ["safety.inverter_headroom_unknown"]
    # a limit above the current export curtails nothing: no chain check
    assert _keys(_ctx(InverterLimitAction(max_export_w=7000.0), headroom_w=1500.0)) == []


def test_x2_an_unverified_export_limit_is_judged_like_an_ac_limit() -> None:
    keys = _keys(_ctx(InverterLimitAction(max_export_w=0.0), verified=False))
    assert keys == ["safety.inverter_may_cause_import"]
    assert _keys(_ctx(InverterLimitAction(max_export_w=2500.0), verified=False)) == []


def test_x2_an_ac_limit_keeps_house_and_battery_charging() -> None:
    # house 2000 + charging 1500 + margin 500 = 4000 W = 40 %
    assert _keys(_ctx(InverterLimitAction(max_ac_pct=40.0), battery_w=1500.0)) == []
    keys = _keys(_ctx(InverterLimitAction(max_ac_pct=39.0), battery_w=1500.0))
    assert keys == ["safety.inverter_may_cause_import"]


def test_x1_never_above_the_baseline_or_the_static_limit() -> None:
    low = InverterLimitAction(max_export_w=5000.0)
    assert _keys(_ctx(InverterLimitAction(max_export_w=6000.0), baseline=low)) == [
        "safety.inverter_above_baseline"
    ]
    high = InverterLimitAction(max_export_w=20_000.0)
    assert _keys(_ctx(InverterLimitAction(max_export_w=14_000.0), baseline=high)) == [
        "safety.inverter_above_static"
    ]
    assert _keys(_ctx(InverterLimitAction(max_export_w=0.0), baseline=None)) == [
        "safety.inverter_baseline_unknown"
    ]


def test_x5_island_and_stale_inputs_block() -> None:
    ctx = _ctx(InverterLimitAction(max_export_w=0.0), grid_state=GridState.ISLAND)
    assert "safety.inverter_island" in _keys(ctx)
    assert [r.key for r in lease_violations(ctx)] == ["safety.inverter_island"]


def test_a_limit_in_the_wrong_unit_is_refused() -> None:
    ctx = _ctx(InverterLimitAction(max_export_w=0.0))
    wrong = InverterContext(
        requested=InverterLimitAction(max_ac_pct=50.0),
        config=ctx.config,
        live=ctx.live,
        baseline=ctx.baseline,
        limit_range=ctx.limit_range,
        headroom_w=0.0,
        grid_state=GridState.CONNECTED,
    )
    assert "safety.inverter_capability_mismatch" in _keys(wrong)
