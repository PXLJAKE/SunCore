# SPDX-License-Identifier: Apache-2.0
"""The X3 waiver of the inverter limit verification (PR 7-J, ADR-0018 addendum, SEC-24).

Property tests on the pure invariants: the waiver applies only inside an active window, only to
``Origin.USER`` intents of that window, never below its lowest limit, and it waives X3 alone -
X1, X2, X5 and the unit/range checks give the same verdict with and without a window.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.core.decisions import InverterLimitAction, Origin
from suncore.core.grid import GridState
from suncore.core.inverter import EXPORT_TOLERANCE_W, export_cap_w
from suncore.core.verification import (
    MAX_WINDOW_S,
    VerificationWindow,
    verification_intent_id,
)
from suncore.safety.inverter import (
    InverterContext,
    InverterGateConfig,
    InverterLive,
    check_chain,
    evaluate_inverter,
    window_waives_chain,
)

T0 = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
CHAIN_KEYS = frozenset({"safety.inverter_chain_not_exhausted", "safety.inverter_headroom_unknown"})


def window(
    *,
    opened: float = 0.0,
    length: float = 300.0,
    lowest: InverterLimitAction | None = None,
    wid: str = "w1",
) -> VerificationWindow:
    start = T0 + timedelta(seconds=opened)
    return VerificationWindow(
        id=wid,
        opened_at=start,
        expires_at=start + timedelta(seconds=length),
        lowest=lowest or InverterLimitAction(max_export_w=0.0),
    )


# --- the window itself ---------------------------------------------------------------------------


def test_a_window_longer_than_ten_minutes_cannot_exist() -> None:
    window(length=MAX_WINDOW_S)
    with pytest.raises(ValueError, match="must last"):
        window(length=MAX_WINDOW_S + 1)
    with pytest.raises(ValueError, match="must last"):
        window(length=0.0)
    with pytest.raises(ValueError, match="timezone"):
        VerificationWindow(
            id="w",
            opened_at=datetime(2026, 1, 1),  # noqa: DTZ001 - the naive time under test
            expires_at=datetime(2026, 1, 1, 0, 5),  # noqa: DTZ001
            lowest=InverterLimitAction(max_export_w=0.0),
        )
    for bad in ("", "W1", "a" * 65, "w:1", "-w"):
        with pytest.raises(ValueError, match="id"):
            window(wid=bad)


@given(
    length_ms=st.integers(min_value=1, max_value=int(MAX_WINDOW_S * 1000)),
    at_ms=st.integers(min_value=-700_000, max_value=1_300_000),
)
def test_a_window_is_active_only_between_opening_and_expiry(length_ms: int, at_ms: int) -> None:
    w = window(length=length_ms / 1000)
    now = T0 + timedelta(milliseconds=at_ms)
    assert w.active(now) == (0 <= at_ms < length_ms)


def test_a_window_owns_only_its_own_intents_and_allows_nothing_tighter() -> None:
    w = window(lowest=InverterLimitAction(max_export_w=4000.0))
    assert w.owns(verification_intent_id("w1", "test"))
    assert not w.owns(verification_intent_id("w10", "test"))
    assert not w.owns("inverter:2026-06-21T12:00:00+00:00:1")
    assert not w.owns("w1:test")
    assert w.allows(InverterLimitAction(max_export_w=4000.0))
    assert w.allows(InverterLimitAction(max_export_w=9000.0))
    assert not w.allows(InverterLimitAction(max_export_w=3000.0))
    assert not w.allows(InverterLimitAction(max_ac_pct=90.0))  # another unit


# --- the waiver as a property --------------------------------------------------------------------

limits_w = st.floats(min_value=0.0, max_value=15_000.0, allow_nan=False)
limits_pct = st.floats(min_value=1.0, max_value=100.0, allow_nan=False)
requested_st = st.one_of(
    limits_w.map(lambda w: InverterLimitAction(max_export_w=round(w))),
    limits_pct.map(lambda p: InverterLimitAction(max_ac_pct=round(p))),
)


@st.composite
def contexts(draw: st.DrawFn) -> InverterContext:
    requested = draw(requested_st)
    capability = "export_limit" if requested.max_export_w is not None else "ac_limit"
    export = draw(st.floats(min_value=0.0, max_value=12_000.0))
    house = draw(st.floats(min_value=0.0, max_value=8_000.0))
    battery = draw(st.floats(min_value=-5_000.0, max_value=5_000.0))
    fresh = draw(st.booleans())
    has_window = draw(st.booleans())
    w = None
    if has_window:
        lowest = draw(requested_st)
        w = window(
            opened=draw(st.floats(min_value=-700.0, max_value=100.0)),
            length=draw(st.floats(min_value=1.0, max_value=MAX_WINDOW_S)),
            lowest=lowest,
        )
    own = draw(st.booleans())
    baseline = (
        InverterLimitAction(max_export_w=13_500.0)
        if capability == "export_limit"
        else InverterLimitAction(max_ac_pct=100.0)
    )
    return InverterContext(
        requested=requested,
        config=InverterGateConfig(
            identity="inv",
            capability=capability,
            verified=draw(st.booleans()),
            nominal_ac_w=10_000.0,
            static_limit_w=draw(st.sampled_from([None, 13_500.0, 5_000.0])),
        ),
        live=InverterLive(
            grid_w=-export, pv_w=export + house, house_w=house, battery_w=battery, fresh=fresh
        ),
        baseline=draw(st.sampled_from([baseline, None])),
        limit_range=(0.0, 20_000.0) if capability == "export_limit" else (1.0, 100.0),
        headroom_w=draw(st.sampled_from([None, 0.0, 500.0, 3_000.0])),
        grid_state=draw(st.sampled_from([GridState.CONNECTED, GridState.ISLAND])),
        origin=draw(st.sampled_from([None, *Origin])),
        intent_id=verification_intent_id("w1", "test") if own else "inverter:x:1",
        window=w,
        now=T0,
    )


def _no_window(ctx: InverterContext) -> InverterContext:
    return replace(ctx, window=None, origin=None, intent_id=None)


def _waiver_conditions(ctx: InverterContext) -> bool:
    w = ctx.window
    return (
        w is not None
        and ctx.origin is Origin.USER
        and w.opened_at <= T0 < w.expires_at
        and ctx.intent_id is not None
        and ctx.intent_id.startswith(f"verify:{w.id}:")
        and w.allows(ctx.requested)
    )


@settings(max_examples=600)
@given(ctx=contexts())
def test_x3_is_waived_exactly_inside_an_active_window_for_its_user_intents(
    ctx: InverterContext,
) -> None:
    assert window_waives_chain(ctx) == _waiver_conditions(ctx)
    plain = check_chain(_no_window(ctx))
    waived = check_chain(ctx)
    if _waiver_conditions(ctx):
        # Only the chain verdict may change, and only from "not exhausted" to "may".
        export = ctx.live.export_w
        cap = export_cap_w(ctx.requested, ctx.config.nominal_ac_w, ctx.live.house_w)
        if export is not None and cap is not None:
            assert waived is None
            if cap < export - EXPORT_TOLERANCE_W and (
                ctx.headroom_w is None or ctx.headroom_w > EXPORT_TOLERANCE_W
            ):
                assert plain is not None
    else:
        assert waived == plain


@settings(max_examples=600)
@given(ctx=contexts())
def test_x1_x2_x5_and_the_unit_checks_are_never_waived(ctx: InverterContext) -> None:
    def others(c: InverterContext) -> list[str]:
        return [r.key for r in evaluate_inverter(c) if r.key not in CHAIN_KEYS]

    assert others(ctx) == others(_no_window(ctx))


def test_the_waiver_needs_every_condition() -> None:
    lowest = InverterLimitAction(max_export_w=4000.0)
    base = InverterContext(
        requested=InverterLimitAction(max_export_w=5000.0),
        config=InverterGateConfig(identity="inv", capability="export_limit"),
        live=InverterLive(grid_w=-6000.0, pv_w=7500.0, house_w=1500.0, battery_w=0.0, fresh=True),
        baseline=InverterLimitAction(max_export_w=13_500.0),
        limit_range=(0.0, 20_000.0),
        headroom_w=2000.0,
        grid_state=GridState.CONNECTED,
        origin=Origin.USER,
        intent_id=verification_intent_id("w1", "test"),
        window=window(lowest=lowest),
        now=T0 + timedelta(seconds=10),
    )
    assert check_chain(base) is None
    expired = replace(base, now=T0 + timedelta(seconds=300))
    not_yet = replace(base, now=T0 - timedelta(seconds=1))
    variants = [
        replace(base, window=None),
        expired,
        not_yet,
        replace(base, origin=Origin.CONTROLLER),
        replace(base, origin=Origin.PLANNER),
        replace(base, origin=Origin.SAFETY),
        replace(base, intent_id="inverter:x:1"),
        replace(base, intent_id=verification_intent_id("w2", "test")),
        replace(base, requested=InverterLimitAction(max_export_w=3000.0)),
        replace(base, now=None),
    ]
    for ctx in variants:
        reason = check_chain(ctx)
        assert reason is not None
        assert reason.key == "safety.inverter_chain_not_exhausted"
