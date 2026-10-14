# SPDX-License-Identifier: Apache-2.0
"""Property tests for the safety invariants (BRIEF §5, plan phase-3 WP3).

Each invariant is a pure function; hypothesis drives random actions, device states,
runtimes, flows and surplus estimates. The properties state exactly when a check may
pass, so a check that silently allows more than the rule is caught.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.core.control import DeviceState, PowerEstimate
from suncore.core.decisions import (
    Action,
    ModeAction,
    PowerSetpointAction,
    ReleaseAction,
    SetpointAction,
    StepAction,
    SwitchAction,
)
from suncore.core.devices import DeviceConfig
from suncore.core.signals import Quality
from suncore.safety import invariants as inv
from suncore.safety.invariants import InvariantContext
from suncore.safety.runtime import DeviceRuntime
from tests.safety.helpers import (
    SETTINGS,
    T0,
    actions,
    climate_device,
    continuous_device,
    flow_snapshots,
    flows,
    runtime,
    runtimes_for,
    states,
    steps_device,
    surplus,
    surpluses,
    switch_device,
    temps,
)

DEVICES = (
    switch_device(),
    climate_device(),
    climate_device("ac_on", allow_switch_on=True),
    steps_device(),
    continuous_device(),
)


def ctx(
    action: Action,
    device: DeviceConfig = DEVICES[0],
    *,
    state: DeviceState | None = None,
    rt: DeviceRuntime | None = None,
    **extra: object,
) -> InvariantContext:
    return InvariantContext(
        action=action,
        device=device,
        state=state,
        runtime=rt if rt is not None else runtime(device.id),
        now=T0,
        settings=SETTINGS,
        **extra,  # type: ignore[arg-type]
    )


contexts = st.builds(
    lambda action, device, state, rt, fl, sp, est, limit, reserve: InvariantContext(
        action=action,
        device=device,
        state=state,
        runtime=rt.model_copy(update={"device_id": device.id}),
        now=T0,
        settings=SETTINGS,
        flows=fl,
        surplus=sp,
        estimate=est,
        import_limit_w=limit,
        reserve_soc_pct=reserve,
    ),
    action=actions,
    device=st.sampled_from(DEVICES),
    state=states,
    rt=runtimes_for("x"),
    fl=st.one_of(st.none(), flow_snapshots),
    sp=surpluses,
    est=surpluses,
    limit=st.one_of(st.none(), st.floats(min_value=0, max_value=20_000)),
    reserve=st.one_of(st.none(), st.floats(min_value=0, max_value=100)),
)


# --- grid: no SunCore-caused import, never above the limit ----------------------------


@settings(max_examples=400, deadline=None)
@given(contexts)
def test_grid_passes_only_with_proven_surplus(c: InvariantContext) -> None:
    delta = inv.power_delta_w(c)
    verdict = inv.check_grid(c)
    if delta is None or delta <= 0:
        assert verdict is None, "reducing load never trips the grid invariant"
        return
    grid = c.flows.grid_w.value if c.flows is not None else None
    if c.surplus is None or c.surplus.quality is not Quality.OK:
        assert verdict is not None
        assert verdict.key == "safety.surplus_unknown"
    elif grid is not None and grid > 0:
        assert verdict is not None
        assert verdict.key == "safety.grid_importing"
    elif c.surplus.lower_w < delta:
        assert verdict is not None
        assert verdict.key == "safety.grid_import_caused"
    elif c.import_limit_w is not None and grid is None:
        assert verdict is not None
        assert verdict.key == "safety.grid_unknown"
    elif c.import_limit_w is not None and grid is not None and grid + delta > c.import_limit_w:
        assert verdict is not None
        assert verdict.key == "safety.grid_import_limit"
    else:
        assert verdict is None


def test_grid_examples() -> None:
    on = SwitchAction(on=True)
    base = {"flows": flows(grid_w=-1500.0), "surplus": surplus(1500.0, 100.0)}
    assert inv.check_grid(ctx(on, **base)) is None
    # Uncertainty eats the margin: 1500 - 600 < 1000.
    assert inv.check_grid(ctx(on, flows=flows(-1500.0), surplus=surplus(1500.0, 600.0))) is not None
    # Importing right now: never add load, whatever the estimate says.
    r = inv.check_grid(ctx(on, flows=flows(grid_w=200.0), surplus=surplus(5000.0)))
    assert r is not None
    assert r.key == "safety.grid_importing"
    # Stale surplus is unknown surplus.
    r = inv.check_grid(ctx(on, flows=flows(), surplus=surplus(5000.0, quality=Quality.STALE)))
    assert r is not None
    assert r.key == "safety.surplus_unknown"
    # Import limit: -1500 + 1000 = -500 <= 0 ok; limit 0 with grid -500 and 1000 W -> 500 > 0.
    assert inv.check_grid(ctx(on, **base, import_limit_w=0.0)) is None
    r = inv.check_grid(ctx(on, flows=flows(-500.0), surplus=surplus(2000.0), import_limit_w=0.0))
    assert r is not None
    assert r.key == "safety.grid_import_limit"
    # Switching off never needs surplus.
    assert inv.check_grid(ctx(SwitchAction(on=False))) is None


def test_setpoint_toward_base_never_adds_load() -> None:
    dev = climate_device()
    rt = runtime(dev.id, base_setpoint_c=22.0)
    state = DeviceState(on=True, setpoint_c=20.0, mode="cool")
    back = ctx(SetpointAction(setpoint_c=21.0, base_c=22.0), dev, state=state, rt=rt)
    assert inv.power_delta_w(back) is not None
    assert inv.power_delta_w(back) <= 0
    assert inv.check_grid(back) is None
    away = ctx(SetpointAction(setpoint_c=19.0, base_c=22.0), dev, state=state, rt=rt)
    assert inv.check_grid(away) is not None  # no surplus given


def test_continuous_power_conversion() -> None:
    amps = continuous_device(unit="A")
    assert inv.expected_power_w(PowerSetpointAction(power_w=10.0), amps, None) == 10 * 230 * 3
    watts = continuous_device()
    assert inv.expected_power_w(PowerSetpointAction(power_w=1234.0), watts, None) == 1234.0
    assert inv.expected_power_w(StepAction(step=2), steps_device(), None) == 3000.0
    assert inv.expected_power_w(StepAction(step=9), steps_device(), None) is None
    assert inv.expected_power_w(ModeAction(mode="off"), climate_device(), None) == 0.0
    assert inv.expected_power_w(ReleaseAction(), switch_device(), None) is None
    est = PowerEstimate(value_w=500.0, uncertainty_w=100.0)
    dev = switch_device().model_copy(
        update={
            "power_model": switch_device().power_model.model_copy(
                update={"nominal_w": None, "kind": "measured", "power_role": "switch"}
            )
        }
    )
    assert inv.expected_power_w(SwitchAction(on=True), dev, est) == 600.0


# --- comfort -------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(st.builds(SetpointAction, setpoint_c=temps, base_c=temps))
def test_comfort_band_is_the_only_pass_condition(action: SetpointAction) -> None:
    dev = climate_device(min_c=21.0, max_c=24.0)
    verdict = inv.check_comfort(ctx(action, dev))
    assert (verdict is None) == (21.0 <= action.setpoint_c <= 24.0)


@given(actions)
def test_comfort_ignores_non_setpoint_actions(action: Action) -> None:
    if isinstance(action, SetpointAction):
        return
    assert inv.check_comfort(ctx(action, climate_device())) is None


def test_comfort_without_band_rejects() -> None:
    dev = climate_device().model_copy(update={"comfort": None})
    r = inv.check_comfort(ctx(SetpointAction(setpoint_c=22.0, base_c=22.0), dev))
    assert r is not None
    assert r.key == "safety.comfort_band_missing"


# --- min run / min pause ---------------------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(
    on=st.booleans(),
    current=st.one_of(st.none(), st.booleans()),
    since_s=st.one_of(st.none(), st.integers(min_value=0, max_value=1200)),
    limit=st.integers(min_value=0, max_value=900),
)
def test_min_run_pause_property(
    on: bool, current: bool | None, since_s: int | None, limit: int
) -> None:
    dev = switch_device(min_run_s=limit, min_pause_s=limit)
    state = None if current is None else DeviceState(on=current)
    since = None if since_s is None else T0 - timedelta(seconds=since_s)
    rt = runtime(dev.id, last_off_ts=since if on else None, last_on_ts=None if on else since)
    verdict = inv.check_min_run_pause(ctx(SwitchAction(on=on), dev, state=state, rt=rt))
    transition = current is None or current != on
    blocked = transition and limit > 0 and since_s is not None and since_s < limit
    assert (verdict is not None) == blocked
    if verdict is not None:
        assert verdict.key == ("safety.min_pause" if on else "safety.min_run")
        assert verdict.args["remaining_s"] == limit - (since_s or 0)


def test_release_bypasses_protection_times() -> None:
    dev = switch_device(min_run_s=600)
    rt = runtime(dev.id, last_on_ts=T0, switch_times=(T0,) * 10)
    c = ctx(ReleaseAction(), dev, state=DeviceState(on=True), rt=rt)
    assert inv.check_min_run_pause(c) is None
    assert inv.check_switch_rate(c) is None
    assert (
        inv.check_write_rate(ctx(ReleaseAction(), dev, rt=runtime(dev.id, last_write_ts=T0)))
        is None
    )


# --- max switches per sliding hour -----------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(
    ages=st.lists(st.integers(min_value=0, max_value=7200), max_size=12),
    limit=st.integers(min_value=1, max_value=8),
    on=st.booleans(),
)
def test_switch_rate_counts_the_sliding_hour(ages: list[int], limit: int, on: bool) -> None:
    dev = switch_device(max_switches_per_hour=limit)
    rt = runtime(dev.id, switch_times=tuple(T0 - timedelta(seconds=a) for a in ages))
    state = DeviceState(on=not on)  # always a transition
    verdict = inv.check_switch_rate(ctx(SwitchAction(on=on), dev, state=state, rt=rt))
    in_window = sum(1 for a in ages if a < 3600)
    assert (verdict is not None) == (in_window >= limit)
    # No transition, no switch counted.
    assert (
        inv.check_switch_rate(ctx(SwitchAction(on=on), dev, state=DeviceState(on=on), rt=rt))
        is None
    )


def test_switch_rate_uses_settings_default_when_device_has_none() -> None:
    dev = switch_device()
    assert dev.max_switches_per_hour is None
    rt = runtime(
        dev.id, switch_times=(T0 - timedelta(seconds=1),) * SETTINGS.default_max_switches_per_hour
    )
    r = inv.check_switch_rate(ctx(SwitchAction(on=True), dev, state=DeviceState(on=False), rt=rt))
    assert r is not None
    assert r.args["limit"] == SETTINGS.default_max_switches_per_hour


# --- per-device write rate limit ---------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(age=st.one_of(st.none(), st.floats(min_value=0, max_value=60, allow_nan=False)))
def test_write_rate_limit(age: float | None) -> None:
    last = None if age is None else T0 - timedelta(seconds=age)
    verdict = inv.check_write_rate(ctx(SwitchAction(on=True), rt=runtime(last_write_ts=last)))
    assert (verdict is not None) == (age is not None and age < SETTINGS.min_write_interval_s)


# --- write only on change ------------------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(actions, states)
def test_write_only_on_change(action: Action, state: DeviceState | None) -> None:
    verdict = inv.check_change(ctx(action, state=state))
    if state is None or isinstance(action, ReleaseAction):
        assert verdict is None
    else:
        assert (verdict is not None) == inv.action_matches_state(action, state)


def test_change_examples() -> None:
    assert inv.action_matches_state(SwitchAction(on=True), DeviceState(on=True))
    assert not inv.action_matches_state(
        SwitchAction(on=True), DeviceState(on=True, quality=Quality.UNAVAILABLE)
    )
    assert inv.action_matches_state(
        SetpointAction(setpoint_c=22.0, base_c=22.0), DeviceState(on=True, setpoint_c=22.04)
    )
    assert not inv.action_matches_state(
        SetpointAction(setpoint_c=22.0, base_c=22.0), DeviceState(on=False, setpoint_c=22.0)
    )
    assert inv.action_matches_state(StepAction(step=1), DeviceState(step=1))
    assert inv.action_matches_state(ModeAction(mode="cool"), DeviceState(mode="cool"))
    assert inv.action_matches_state(PowerSetpointAction(power_w=100.0), DeviceState(value=100.5))
    assert not inv.action_matches_state(ReleaseAction(), DeviceState(on=True))


# --- battery reserve guard ------------------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(
    battery_w=st.one_of(st.none(), st.floats(min_value=-5000, max_value=5000, allow_nan=False)),
    soc=st.one_of(st.none(), st.floats(min_value=0, max_value=100)),
    reserve=st.one_of(st.none(), st.floats(min_value=0, max_value=100)),
    on=st.booleans(),
)
def test_battery_reserve_guard(
    battery_w: float | None, soc: float | None, reserve: float | None, on: bool
) -> None:
    c = ctx(
        SwitchAction(on=on),
        state=DeviceState(on=not on),
        flows=flows(battery_w=battery_w, soc_pct=soc),
        reserve_soc_pct=reserve,
    )
    verdict = inv.check_battery_reserve(c)
    blocked = (
        on
        and battery_w is not None
        and soc is not None
        and reserve is not None
        and battery_w < 0
        and soc <= reserve
    )
    assert (verdict is not None) == blocked


# --- climate set-point / switch-on while off ---------------------------------------


@settings(max_examples=300, deadline=None)
@given(actions, states, st.booleans())
def test_climate_off_needs_opt_in(action: Action, state: DeviceState | None, allow: bool) -> None:
    dev = climate_device(allow_switch_on=allow)
    verdict = inv.check_climate_switch_on(ctx(action, dev, state=state))
    turns_on = inv.action_turns_on(action, dev) is True
    known_on = inv.device_is_on(state) is True
    assert (verdict is not None) == (turns_on and not known_on and not allow)


def test_climate_rule_does_not_apply_to_other_kinds() -> None:
    assert (
        inv.check_climate_switch_on(
            ctx(SwitchAction(on=True), switch_device(), state=DeviceState(on=False))
        )
        is None
    )


def test_setpoint_while_off_is_rejected_unless_allowed() -> None:
    off = DeviceState(on=False, mode="off")
    act = SetpointAction(setpoint_c=23.0, base_c=22.0)
    r = inv.check_climate_switch_on(ctx(act, climate_device(), state=off))
    assert r is not None
    assert r.key == "safety.climate_off"
    assert (
        inv.check_climate_switch_on(ctx(act, climate_device(allow_switch_on=True), state=off))
        is None
    )
    assert (
        inv.check_climate_switch_on(ctx(act, climate_device(), state=None)) is not None
    )  # unknown = off
    assert (
        inv.check_climate_switch_on(ctx(SwitchAction(on=False), climate_device(), state=off))
        is None
    )


# --- evaluate: union, purity, release ------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(contexts)
def test_evaluate_is_pure_and_the_union_of_all_checks(c: InvariantContext) -> None:
    first = inv.evaluate(c)
    assert first == inv.evaluate(c)
    expected = tuple(r for r in (check(c) for check in inv.ALL_CHECKS) if r is not None)
    assert first == expected
    assert all(r.key.startswith("safety.") for r in first)


@settings(max_examples=200, deadline=None)
@given(contexts)
def test_release_never_violates(c: InvariantContext) -> None:
    released = dataclasses.replace(c, action=ReleaseAction())
    assert inv.evaluate(released) == ()


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (None, None),
        (DeviceState(on=True), True),
        (DeviceState(mode="off"), False),
        (DeviceState(mode="cool"), True),
        (DeviceState(step=0), False),
        (DeviceState(step=2), True),
        (DeviceState(value=0.0), False),
        (DeviceState(value=5.0), True),
        (DeviceState(), None),
        (DeviceState(on=True, quality=Quality.INVALID), None),
    ],
)
def test_device_is_on(state: DeviceState | None, expected: bool | None) -> None:
    assert inv.device_is_on(state) is expected


def test_action_turns_on_per_kind() -> None:
    dev = steps_device()
    assert inv.action_turns_on(StepAction(step=0), dev) is False
    assert inv.action_turns_on(StepAction(step=1), dev) is True
    assert inv.action_turns_on(StepAction(step=1), switch_device()) is True
    assert inv.action_turns_on(ModeAction(mode="off"), dev) is False
    assert inv.action_turns_on(PowerSetpointAction(power_w=0.0), dev) is False
    assert inv.action_turns_on(ReleaseAction(), dev) is None


def test_action_matches_read_back() -> None:
    assert inv.action_matches(
        SetpointAction(setpoint_c=22.0, base_c=22.0), SetpointAction(setpoint_c=22.0, base_c=21.0)
    )
    assert not inv.action_matches(
        SetpointAction(setpoint_c=22.0, base_c=22.0), SetpointAction(setpoint_c=22.5, base_c=22.0)
    )
    assert inv.action_matches(
        PowerSetpointAction(power_w=100.0), PowerSetpointAction(power_w=100.9)
    )
    assert not inv.action_matches(SwitchAction(on=True), None)
    assert not inv.action_matches(SwitchAction(on=True), ModeAction(mode="cool"))
    assert inv.action_matches(StepAction(step=1), StepAction(step=1))
