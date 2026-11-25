# SPDX-License-Identifier: Apache-2.0
"""PR 6-J: the pure loadpoint invariants (L1-L3) as properties."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.core.control import DeviceBand
from suncore.core.decisions import LoadpointAction
from suncore.core.devices import LoadpointControl
from suncore.core.flex import EvMode
from suncore.safety.evcc import (
    LoadpointSafety,
    check_raise,
    check_values,
    loadpoint_allowance_w,
    loadpoint_raises,
    potential_power_w,
)
from suncore.safety.flex import EvSafety

CONTROL = LoadpointControl(kind="loadpoint", loadpoint_id=1)  # 16 A, 3 phases, 230 V
MODES = st.sampled_from(("off", "smart", "now"))
CURRENTS = st.floats(min_value=0.1, max_value=40.0, allow_nan=False, allow_infinity=False)
ACTIONS = st.builds(
    LoadpointAction,
    mode=MODES,
    max_current_a=st.one_of(st.none(), CURRENTS),
)
OBSERVED = st.one_of(
    st.none(),
    st.builds(LoadpointAction, mode=MODES, max_current_a=st.floats(6.0, 16.0)),
)


@settings(max_examples=200, deadline=None)
@given(action=ACTIONS, minimum=st.one_of(st.none(), st.floats(6.0, 10.0)))
def test_l1_a_value_in_range_passes_and_one_outside_never_does(
    action: LoadpointAction, minimum: float | None
) -> None:
    reason = check_values(action, CONTROL, LoadpointSafety(min_current_a=minimum))
    current = action.max_current_a
    if current is None:
        assert reason is None
        return
    low = max(6.0, minimum or 0.0)
    inside = low - 0.05 <= current <= CONTROL.max_current_a + 0.05
    assert (reason is None) is inside


@given(action=ACTIONS, observed=OBSERVED)
def test_potential_power_is_zero_for_off_and_never_above_the_wiring(
    action: LoadpointAction, observed: LoadpointAction | None
) -> None:
    power = potential_power_w(action, CONTROL, observed)
    ceiling = CONTROL.max_current_a * CONTROL.voltage_v * CONTROL.phases
    assert 0.0 <= power <= ceiling + 1e-6
    if action.mode == "off":
        assert power == 0.0


@given(action=ACTIONS, observed=OBSERVED)
def test_a_lowering_never_counts_as_a_raise(
    action: LoadpointAction, observed: LoadpointAction | None
) -> None:
    if observed is None:
        assert loadpoint_raises(action, None) is (action.mode != "off")
        return
    if loadpoint_raises(action, observed):
        higher_mode = ("off", "smart", "now").index(action.mode) > (
            "off",
            "smart",
            "now",
        ).index(observed.mode)
        higher_current = (
            action.mode != "off"
            and action.max_current_a is not None
            and observed.max_current_a is not None
            and action.max_current_a > observed.max_current_a
        )
        assert higher_mode or higher_current


@given(action=ACTIONS, observed=OBSERVED, allowance=st.floats(0.0, 12_000.0))
def test_l2_now_never_passes_without_an_allowance(
    action: LoadpointAction, observed: LoadpointAction | None, allowance: float
) -> None:
    ev = EvSafety(mode=EvMode.PV, allowance_w=allowance, connected=True)
    reason = check_raise(action, ev, LoadpointSafety(observed=observed))
    if action.mode == "now" and allowance <= 1.0:
        assert reason is not None


@given(action=ACTIONS, connected=st.sampled_from((None, True, False)))
def test_l2_an_unknown_state_blocks_every_raise(
    action: LoadpointAction, connected: bool | None
) -> None:
    ev = EvSafety(mode=EvMode.NOW, allowance_w=11_040.0, connected=connected)
    reason = check_raise(action, ev, LoadpointSafety(observed=None))
    if action.mode != "off":
        assert reason is not None


@given(mode=st.sampled_from(EvMode), connected=st.sampled_from((None, True, False)))
def test_allowance_is_only_the_users_now_or_a_planned_band(
    mode: EvMode, connected: bool | None
) -> None:
    band = DeviceBand(grid_power_max_w=5000, max_power_w=11_040)
    allowance = loadpoint_allowance_w(mode, CONTROL, connected=connected, band=band)
    full = CONTROL.max_current_a * CONTROL.voltage_v * CONTROL.phases
    if connected is False:
        assert allowance == 0.0
    elif mode is EvMode.NOW:
        assert allowance == full
    elif mode is EvMode.TARGET:
        assert allowance == 5000.0 if band.allowed else allowance == 0.0
    else:
        assert allowance == 0.0
