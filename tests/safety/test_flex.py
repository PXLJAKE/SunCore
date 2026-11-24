# SPDX-License-Identifier: Apache-2.0
"""PR 6-I: the EV invariants E1-E6 of ``safety.flex`` as pure functions (property-based).

Each property states the invariant from the accepting side: whatever the inputs, an accepted
charge satisfies it. E7-E9 run through the gate and the driver (``test_ev_gate.py``,
``tests/devices/drivers/test_charger.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from suncore.core.control import DeviceBand
from suncore.core.decisions import BatteryAction, ChargeAction
from suncore.core.devices import ChargerControl
from suncore.core.flex import EvMode
from suncore.safety.flex import (
    CURRENT_TOLERANCE_A,
    POWER_TOLERANCE_W,
    EvSafety,
    charge_matches,
    charge_power_w,
    check_charge_grid,
    check_current_range,
    check_ev_mode,
    check_phases,
    ev_allowance_w,
    holds_discharge_lock,
)

T0 = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def charger(
    *, enable: bool = True, switching: bool = True, min_a: float = 6.0, max_a: float = 16.0
) -> ChargerControl:
    return ChargerControl(
        kind="charger",
        role="current",
        enable_role="enable" if enable else None,
        phases_role="phases" if switching else None,
        phase_option_1="1" if switching else None,
        phase_option_3="3" if switching else None,
        phase_switching=switching,
        min_current_a=min_a,
        max_current_a=max_a,
    )


modes = st.sampled_from(EvMode)
currents = st.floats(min_value=0, max_value=40, allow_nan=False)
powers = st.floats(min_value=0, max_value=30_000, allow_nan=False)
maybe_bool = st.sampled_from((None, True, False))


# --- E1 ---------------------------------------------------------------------------------


@given(
    current=currents,
    enable=st.booleans(),
    entity_min=st.one_of(st.none(), st.floats(min_value=0, max_value=10)),
    entity_max=st.one_of(st.none(), st.floats(min_value=6, max_value=32)),
)
def test_e1_an_accepted_current_is_a_possible_pause_or_inside_both_ranges(
    current: float, enable: bool, entity_min: float | None, entity_max: float | None
) -> None:
    control = charger(enable=enable)
    ev = EvSafety(entity_min_a=entity_min, entity_max_a=entity_max)
    reason = check_current_range(ChargeAction(current_a=current), control, ev)
    if reason is not None:
        return
    if current <= CURRENT_TOLERANCE_A:
        assert enable or entity_min == 0  # a pause the charger can actually do
        return
    assert control.min_current_a - CURRENT_TOLERANCE_A <= current
    assert current <= control.max_current_a + CURRENT_TOLERANCE_A
    if entity_min is not None:
        assert current >= entity_min - CURRENT_TOLERANCE_A
    if entity_max is not None:
        assert current <= entity_max + CURRENT_TOLERANCE_A


def test_e1_rejects_below_the_minimum_and_a_pause_without_a_way_to_pause() -> None:
    control = charger(enable=False)
    assert check_current_range(ChargeAction(current_a=3), control, EvSafety()) is not None
    assert check_current_range(ChargeAction(current_a=0), control, EvSafety()) is not None
    zero_ok = EvSafety(entity_min_a=0)
    assert check_current_range(ChargeAction(current_a=0), control, zero_ok) is None


# --- E2 ---------------------------------------------------------------------------------


@given(
    target=st.sampled_from((1, 3)),
    observed_phases=st.sampled_from((None, 1, 3)),
    observed_current=st.sampled_from((0.0, 6.0, 16.0)),
    enabled=maybe_bool,
    current=st.sampled_from((0.0, 6.0)),
    mode=modes,
    switching=st.booleans(),
    paused_s=st.one_of(st.none(), st.integers(min_value=0, max_value=600)),
    never_charged=st.booleans(),
    count=st.integers(min_value=0, max_value=6),
)
def test_e2_a_phase_change_is_accepted_only_paused_rarely_and_never_in_now(  # noqa: PLR0917
    target: int,
    observed_phases: int | None,
    observed_current: float,
    enabled: bool | None,
    current: float,
    mode: EvMode,
    switching: bool,
    paused_s: int | None,
    never_charged: bool,
    count: int,
) -> None:
    control = charger(switching=switching)
    observed = ChargeAction(
        current_a=observed_current,
        phases=observed_phases,  # type: ignore[arg-type]
        enabled=enabled,
    )
    action = ChargeAction(current_a=current, phases=target)  # type: ignore[arg-type]
    paused_since = None if paused_s is None else T0 - timedelta(seconds=paused_s)
    reason = check_phases(
        action,
        control,
        EvSafety(mode=mode, observed=observed),
        now=T0,
        paused_since=paused_since,
        never_charged=never_charged,
        last_phase_switch=None,
        phase_switches_last_hour=count,
    )
    changes = target != (observed_phases or control.phases)
    if reason is not None or not changes:
        return
    assert switching
    assert observed_phases is not None
    assert mode is not EvMode.NOW
    assert current == 0
    assert observed.effective_current_a == 0  # paused, not charging
    assert never_charged or (paused_s is not None and paused_s >= control.phase_switch_pause_s)
    assert count < control.max_phase_switches_per_hour


def test_e2_charging_resumes_only_after_the_pause() -> None:
    control = charger()
    observed = ChargeAction(current_a=0, phases=1)
    common = {
        "now": T0,
        "paused_since": None,
        "phase_switches_last_hour": 1,
    }
    early = check_phases(
        ChargeAction(current_a=6),
        control,
        EvSafety(observed=observed),
        last_phase_switch=T0 - timedelta(seconds=30),
        **common,  # type: ignore[arg-type]
    )
    assert early is not None
    assert early.key == "safety.ev_phase_pause"
    late = check_phases(
        ChargeAction(current_a=6),
        control,
        EvSafety(observed=observed),
        last_phase_switch=T0 - timedelta(seconds=control.phase_switch_pause_s),
        **common,  # type: ignore[arg-type]
    )
    assert late is None


# --- E3, E6, G1 -------------------------------------------------------------------------


@given(
    after=powers,
    current_lower=powers,
    surplus_lower=st.one_of(st.none(), powers),
    claimed=powers,
    grid=st.one_of(st.none(), st.floats(min_value=-20_000, max_value=20_000)),
    limit=st.one_of(st.none(), st.floats(min_value=1000, max_value=40_000)),
    allowance=powers,
    locked=st.booleans(),
)
def test_e3_e6_g1_an_accepted_charge_imports_at_most_the_allowance(  # noqa: PLR0917
    after: float,
    current_lower: float,
    surplus_lower: float | None,
    claimed: float,
    grid: float | None,
    limit: float | None,
    allowance: float,
    locked: bool,
) -> None:
    ev = EvSafety(allowance_w=allowance, battery_locked=locked)
    reason = check_charge_grid(
        after_w=after,
        current_lower_w=current_lower,
        surplus_lower_w=surplus_lower,
        claimed_w=claimed,
        grid_w=grid,
        import_limit_w=limit,
        ev=ev,
    )
    if reason is not None:
        return
    assert surplus_lower is not None  # unknown surplus: never accepted
    available = max(0.0, current_lower + surplus_lower - claimed)
    caused_import = max(0.0, after - available)
    assert caused_import <= allowance + POWER_TOLERANCE_W  # E3
    if caused_import > POWER_TOLERANCE_W:
        assert locked  # E6: the house battery cannot feed the car
    if limit is not None:  # G1
        assert grid is not None
        assert grid + claimed + max(0.0, after - current_lower) <= limit


@given(mode=modes, connected=maybe_bool, phases=st.sampled_from((1, 3)))
def test_e3_allowance_by_mode(mode: EvMode, connected: bool | None, phases: int) -> None:
    control = charger()
    band = DeviceBand(grid_power_max_w=7000)
    allowance = ev_allowance_w(
        mode,
        control,
        phases,  # type: ignore[arg-type]
        connected=connected,
        band=band,
    )
    if connected is False or mode in (EvMode.PV, EvMode.OFF):
        assert allowance == 0
    elif mode is EvMode.NOW:
        assert allowance == charge_power_w(16, phases, 230)
    elif mode is EvMode.MIN_PV:
        assert allowance == charge_power_w(6, phases, 230)
    else:
        assert allowance == 7000
    # A stale or missing plan band permits nothing in mode target; a disallowed band neither.
    for stale in (None, DeviceBand(allowed=False), DeviceBand()):
        assert ev_allowance_w(EvMode.TARGET, control, 3, connected=True, band=stale) == 0


# --- E5 ---------------------------------------------------------------------------------


@given(mode=modes, connected=maybe_bool, increase=st.booleans())
def test_e5_no_increase_unplugged_or_off(
    mode: EvMode, connected: bool | None, increase: bool
) -> None:
    reason = check_ev_mode(increase, EvSafety(mode=mode, connected=connected))
    if not increase:
        assert reason is None  # a reduction or pause is always allowed
    elif mode is EvMode.OFF or connected is False:
        assert reason is not None
    else:
        assert reason is None


# --- E6 lock ----------------------------------------------------------------------------


def test_e6_only_a_real_lock_counts() -> None:
    assert holds_discharge_lock(BatteryAction(max_discharge_w=0), None)
    assert holds_discharge_lock(BatteryAction(min_soc_pct=60), 60.2)
    assert not holds_discharge_lock(BatteryAction(min_soc_pct=60), None)  # SoC unknown
    assert not holds_discharge_lock(BatteryAction(min_soc_pct=40), 60)
    assert not holds_discharge_lock(BatteryAction(max_charge_w=0), 60)
    assert not holds_discharge_lock(None, 60)


# --- read-back --------------------------------------------------------------------------


def test_read_back_matches_the_effective_current_and_phases() -> None:
    expected = ChargeAction(current_a=10, phases=3)
    assert charge_matches(expected, ChargeAction(current_a=10, phases=3, enabled=True))
    assert not charge_matches(expected, ChargeAction(current_a=10, phases=3, enabled=False))
    assert not charge_matches(expected, ChargeAction(current_a=10, phases=1))
    pause = ChargeAction(current_a=0)
    assert charge_matches(pause, ChargeAction(current_a=16, enabled=False))
