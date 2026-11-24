# SPDX-License-Identifier: Apache-2.0
"""PR 6-J: the controller's evcc logic (runtime EV mode -> ``LoadpointAction`` intents)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.controller.evcc import (
    RAISE_STEP_A,
    RETRY_S,
    EvccIntents,
    LoadpointTick,
)
from suncore.controller.grid import GridHolds, GridInputs
from suncore.core.control import DeviceBand, DeviceState
from suncore.core.decisions import (
    Decision,
    DecisionKind,
    LoadpointAction,
    OperatingMode,
    Reason,
)
from suncore.core.devices import (
    DeviceConfig,
    DeviceKind,
    LoadpointControl,
    PowerModel,
    PowerModelKind,
)
from suncore.core.flex import EvMode, EvTarget
from suncore.core.grid import GridState, Para14aBudget, Para14aStatus
from suncore.core.signals import Quality

T0 = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
CONTROL = LoadpointControl(kind="loadpoint", loadpoint_id=1)  # 16 A, 3 phases, 230 V -> 690 W/A
PER_A = 230.0 * 3


def device(*, para14a: bool = False) -> DeviceConfig:
    return DeviceConfig(
        id="lp",
        template_id="evcc.loadpoint",
        name="evcc",
        kind=DeviceKind.WALLBOX,
        power_model=PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=11_040),
        control=CONTROL,
        para14a=para14a,
    )


def state(mode: str = "off", current: float | None = 10.0, **kw: Any) -> DeviceState:
    base: dict[str, Any] = {"mode": mode, "max_current_a": current, "connected": True}
    base.update(kw)
    return DeviceState(**base)


def tick(**kw: Any) -> LoadpointTick:
    base: dict[str, Any] = {
        "device": device(),
        "state": state(),
        "mode": EvMode.PV,
        "target": None,
        "band": None,
        "battery_locked": True,
        "site_trusted": True,
        "grid": GridInputs(import_limit_w=17_000.0),
        "holds": GridHolds(),
        "import_w": 300.0,
    }
    base.update(kw)
    return LoadpointTick(**base)


def wanted(**kw: Any) -> LoadpointAction | None:
    return EvccIntents().wanted(tick(**kw)).wanted


def accepted(intent_id: str, asset: str = "lp", at: datetime = T0) -> Decision:
    from tests.safety.helpers import make_intent  # noqa: PLC0415

    intent = make_intent(LoadpointAction(mode="off"), asset_id=asset, ts=at).model_copy(
        update={"id": intent_id}
    )
    return Decision(
        intent=intent, kind=DecisionKind.ACCEPTED, mode=OperatingMode.AUTOMATIC, ts=at, reasons=()
    )


@pytest.mark.parametrize("mode", [EvMode.PV, EvMode.MIN_PV])
def test_pv_and_min_pv_map_to_smart_with_the_wiring_limit(mode: EvMode) -> None:
    assert wanted(mode=mode, state=state("off", 10.0)) == LoadpointAction(
        mode="smart", max_current_a=16.0
    )


def test_off_is_always_off_also_on_unknown_inputs() -> None:
    cut = tick(mode=EvMode.OFF, state=state("now", 16.0), site_trusted=False)
    assert EvccIntents().wanted(cut).wanted == LoadpointAction(mode="off")


def test_unknown_or_silent_evcc_says_nothing() -> None:
    assert wanted(state=None) is None
    assert wanted(state=DeviceState(quality=Quality.UNAVAILABLE)) is None


def test_now_needs_the_battery_lock_and_asks_for_one() -> None:
    view = EvccIntents().wanted(tick(mode=EvMode.NOW, battery_locked=False))
    assert view.lock_wanted is True
    assert view.wanted is not None
    assert view.wanted.mode == "smart"  # waits, never imports
    view = EvccIntents().wanted(tick(mode=EvMode.NOW, battery_locked=True))
    assert view.wanted == LoadpointAction(mode="now", max_current_a=16.0)


def test_now_is_cut_to_the_import_headroom() -> None:
    view = EvccIntents().wanted(
        tick(
            mode=EvMode.NOW,
            grid=GridInputs(import_limit_w=9_000.0),
            import_w=4_000.0,
            state=state("off", 10.0, value=0.0),
        )
    )
    assert view.wanted == LoadpointAction(mode="now", max_current_a=7.0)  # 5000 W / 690


def test_target_gets_grid_power_only_in_a_planned_band_and_not_when_reached() -> None:
    band = DeviceBand(grid_power_max_w=5_000, max_power_w=11_040)
    got = wanted(mode=EvMode.TARGET, band=band, battery_locked=True)
    assert got == LoadpointAction(mode="now", max_current_a=7.0)
    assert wanted(mode=EvMode.TARGET, band=None) == LoadpointAction(
        mode="smart", max_current_a=16.0
    )
    reached = wanted(
        mode=EvMode.TARGET,
        band=band,
        target=EvTarget(soc_pct=80, by=T0 + timedelta(hours=8)),
        state=state("smart", 16.0, soc_pct=85.0),
    )
    assert reached == LoadpointAction(mode="smart", max_current_a=16.0)


def test_an_unplugged_vehicle_is_never_raised() -> None:
    got = wanted(mode=EvMode.NOW, state=state("off", 10.0, connected=False))
    assert got == LoadpointAction(mode="off", max_current_a=10.0)


@pytest.mark.parametrize(
    "kw",
    [
        {"site_trusted": False},
        {"grid": GridInputs(grid_state=GridState.ISLAND)},
        {"grid": GridInputs(grid_state=GridState.UNKNOWN)},
        {"holds": GridHolds(island_s=30.0)},
        {"state": state("off", 10.0, quality=Quality.STALE)},
    ],
    ids=["untrusted", "island", "grid_unknown", "cooldown", "stale"],
)
def test_no_raise_when_anything_is_unknown_or_protective(kw: dict[str, Any]) -> None:
    got = wanted(mode=EvMode.NOW, **kw)
    assert got is not None
    assert got.mode == "off"  # never above what evcc shows
    assert got.max_current_a is not None
    assert got.max_current_a <= 10.0


def test_no_raise_keeps_a_lowering() -> None:
    got = wanted(mode=EvMode.PV, site_trusted=False, state=state("now", 16.0))
    assert got == LoadpointAction(mode="smart", max_current_a=16.0)  # smart < now: lowered


def test_para14a_budget_caps_the_current_and_below_the_floor_it_is_off() -> None:
    capped = tick(
        device=device(para14a=True),
        grid=GridInputs(para14a=Para14aBudget(Para14aStatus.ACTIVE, budget_w=4_200.0)),
    )
    assert EvccIntents().wanted(capped).wanted == LoadpointAction(
        mode="smart",
        max_current_a=6.0,  # floor(4200 / 690) = 6
    )
    off = tick(
        device=device(para14a=True),
        grid=GridInputs(para14a=Para14aBudget(Para14aStatus.ACTIVE, budget_w=3_000.0)),
    )
    assert EvccIntents().wanted(off).wanted == LoadpointAction(mode="off")
    unflagged = tick(grid=GridInputs(para14a=Para14aBudget(Para14aStatus.ACTIVE, budget_w=0.0)))
    assert EvccIntents().wanted(unflagged).wanted == LoadpointAction(
        mode="smart", max_current_a=16.0
    )


def test_a_small_raise_is_debounced_but_a_lowering_is_immediate() -> None:
    held = wanted(state=state("smart", 15.0))  # wanted 16 A, only +1 A
    assert held == LoadpointAction(mode="smart", max_current_a=15.0)
    big = wanted(state=state("smart", 16.0 - RAISE_STEP_A))
    assert big == LoadpointAction(mode="smart", max_current_a=16.0)
    low = wanted(
        mode=EvMode.NOW,
        state=state("now", 16.0, value=0.0),
        grid=GridInputs(import_limit_w=9_000.0),
        import_w=4_000.0,
    )
    assert low == LoadpointAction(mode="now", max_current_a=7.0)


def test_intents_are_sent_on_change_only_and_repeated_after_the_retry_time() -> None:
    controller = EvccIntents()
    first = controller.step(T0, [tick()])
    assert len(first) == 1
    assert first[0].asset_id == "lp"
    assert first[0].action == LoadpointAction(mode="smart", max_current_a=16.0)
    assert first[0].id.startswith("evcc:")
    assert controller.step(T0 + timedelta(seconds=5), [tick()]) == []  # gate has not answered
    assert controller.report(accepted(first[0].id)) is True
    assert controller.step(T0 + timedelta(seconds=10), [tick()]) == []  # within the retry time
    again = controller.step(T0 + timedelta(seconds=RETRY_S + 1), [tick()])
    assert len(again) == 1  # evcc still does not show it
    shown = tick(state=state("smart", 16.0))
    assert controller.step(T0 + timedelta(seconds=300), [shown]) == []


def test_a_blocked_device_gets_no_intent_and_a_foreign_decision_is_not_ours() -> None:
    controller = EvccIntents()
    blocked = tick(blocked=Reason(key="safety.paused"))
    assert controller.step(T0, [blocked]) == []
    other = accepted("charge:wb:1")
    assert controller.report(other) is False


def test_reset_pending_lets_an_unanswered_intent_be_retried() -> None:
    controller = EvccIntents()
    assert len(controller.step(T0, [tick()])) == 1
    assert controller.step(T0 + timedelta(seconds=1), [tick()]) == []
    controller.reset_pending()
    assert controller.step(T0 + timedelta(seconds=2), [tick()]) == []  # same action, retry window
    assert len(controller.step(T0 + timedelta(seconds=RETRY_S + 3), [tick()])) == 1


def test_a_removed_loadpoint_is_forgotten() -> None:
    controller = EvccIntents()
    controller.step(T0, [tick()])
    assert "lp" in controller.sent
    controller.step(T0 + timedelta(seconds=1), [])
    assert controller.sent == {}
    assert controller.lock_wanted is False


@settings(max_examples=200, deadline=None)
@given(
    mode=st.sampled_from(EvMode),
    observed_mode=st.sampled_from(("off", "smart", "now")),
    observed_current=st.floats(6.0, 16.0),
    trusted=st.booleans(),
    locked=st.booleans(),
    connected=st.booleans(),
    import_w=st.one_of(st.none(), st.floats(0.0, 20_000.0)),
)
def test_property_a_wanted_state_never_exceeds_the_wiring_and_unsafe_inputs_never_raise(  # noqa: PLR0917
    mode: EvMode,
    observed_mode: str,
    observed_current: float,
    trusted: bool,
    locked: bool,
    connected: bool,
    import_w: float | None,
) -> None:
    seen = state(observed_mode, observed_current, connected=connected)
    view = EvccIntents().wanted(
        tick(
            mode=mode,
            state=seen,
            site_trusted=trusted,
            battery_locked=locked,
            import_w=import_w,
            band=DeviceBand(grid_power_max_w=6_000, max_power_w=11_040),
        )
    )
    action = view.wanted
    assert action is not None
    if action.max_current_a is not None:
        assert action.max_current_a <= CONTROL.max_current_a + 1e-6
    if action.mode == "now" and observed_mode != "now":
        assert (locked, trusted, connected) == (True, True, True)  # E6 and the fail-safe
    if not (trusted and connected):
        rank = {"off": 0, "smart": 1, "now": 2}
        assert rank[action.mode] <= max(rank[observed_mode], 0)
        if action.max_current_a is not None:
            assert action.max_current_a <= observed_current + 1e-6
    if mode is EvMode.OFF:
        assert action.mode == "off"
