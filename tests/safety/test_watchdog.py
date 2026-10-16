# SPDX-License-Identifier: Apache-2.0
"""Watchdog: per-sample plausibility, scopes and the safe-state machine."""

from __future__ import annotations

from datetime import datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.core.control import ControlSettings, DeviceState
from suncore.core.decisions import Reason
from suncore.core.signals import Quality, Sample
from suncore.core.site import BatteryConfig, EntityRef, GridConfig, PvArray, SiteConfig
from suncore.core.units import Unit
from suncore.safety.watchdog import (
    REASON_CONNECTION_LOST,
    REASON_COOLDOWN,
    SITE_SCOPE,
    FaultKind,
    ScopeState,
    Watchdog,
)
from tests.safety.helpers import T0

SETTINGS = ControlSettings(safe_state_cooldown_s=120, frozen_pv_s=600, max_jump_w_per_s=1000)


def site(*, house: bool = False, pair: bool = False) -> SiteConfig:
    grid = (
        GridConfig(
            import_power=EntityRef(entity_id="sensor.imp"),
            export_power=EntityRef(entity_id="sensor.exp"),
        )
        if pair
        else GridConfig(power=EntityRef(entity_id="sensor.grid"))
    )
    return SiteConfig(
        grid=grid,
        pv=[PvArray(name="PV", power=EntityRef(entity_id="sensor.pv"))],
        battery=BatteryConfig(
            power=EntityRef(entity_id="sensor.bat"), soc=EntityRef(entity_id="sensor.soc")
        ),
        house=EntityRef(entity_id="sensor.house") if house else None,
    )


def samples(
    now: datetime,
    *,
    grid: float | None = -1000.0,
    pv: float = 3000.0,
    bat: float = 500.0,
    soc: float = 60.0,
    house: float | None = None,
    quality: Quality = Quality.OK,
    pv_unit: Unit | None = Unit.W,
) -> dict[str, Sample]:
    out = {
        "sensor.pv": Sample(pv, pv_unit, now, quality),
        "sensor.bat": Sample(bat, Unit.W, now, quality),
        "sensor.soc": Sample(soc, Unit.PERCENT, now, quality),
    }
    if grid is not None:
        out["sensor.grid"] = Sample(grid, Unit.W, now, quality)
    if house is not None:
        out["sensor.house"] = Sample(house, Unit.W, now, quality)
    return out


def kinds(faults: tuple[object, ...]) -> list[str]:
    return [f.kind.value for f in faults]  # type: ignore[attr-defined]


def test_clean_site_is_normal() -> None:
    wd = Watchdog(SETTINGS)
    assert wd.check_site(T0, site(), samples(T0)) == ()
    assert wd.state(SITE_SCOPE, T0) is ScopeState.NORMAL
    assert not wd.is_paused("any", T0)
    assert wd.pause_reason("any", T0) is None


def test_missing_unavailable_invalid_and_wrong_unit() -> None:
    wd = Watchdog(SETTINGS)
    assert kinds(wd.check_site(T0, site(), samples(T0, grid=None))) == ["unavailable"]
    s = samples(T0)
    s["sensor.grid"] = Sample(None, Unit.W, T0, Quality.UNAVAILABLE)
    assert kinds(wd.check_site(T0, site(), s)) == ["unavailable"]
    s["sensor.grid"] = Sample(1.0, Unit.W, T0, Quality.INVALID)
    assert kinds(wd.check_site(T0, site(), s)) == ["invalid"]
    assert kinds(wd.check_site(T0, site(), samples(T0, pv_unit=Unit.KWH))) == ["invalid"]
    assert kinds(wd.check_site(T0, site(), samples(T0, pv_unit=None))) == ["invalid"]
    s = samples(T0)
    s["sensor.soc"] = Sample(60.0, Unit.W, T0)
    assert kinds(wd.check_site(T0, site(), s)) == ["invalid"]
    faults = wd.check_site(T0, site(pair=True), {})
    assert kinds(faults) == ["unavailable"] * 5
    assert faults[0].reason.key == "safety.watchdog_unavailable"
    assert faults[0].reason.args["entity_id"] == "sensor.imp"


def test_stale_uses_cadence_for_slow_sensors_and_the_cache_flag_otherwise() -> None:
    wd = Watchdog(SETTINGS, cadence_s={"sensor.pv": 300})
    old = T0 - timedelta(seconds=500)
    s = samples(T0)
    s["sensor.pv"] = Sample(3000.0, Unit.W, old, Quality.STALE)  # cache says stale at 120 s
    assert wd.check_site(T0, site(), s) == ()  # 500 s < 300 * 2: still within the cadence allowance
    s["sensor.pv"] = Sample(3000.0, Unit.W, T0 - timedelta(seconds=601), Quality.STALE)
    assert kinds(wd.check_site(T0, site(), s)) == ["stale"]
    s["sensor.pv"] = Sample(3000.0, Unit.W, T0)
    s["sensor.grid"] = Sample(-1000.0, Unit.W, T0 - timedelta(seconds=10), Quality.STALE)
    assert kinds(wd.check_site(T0, site(), s)) == ["stale"]  # no cadence: the flag decides
    wd.set_cadence("sensor.grid", 60)
    assert wd.cadence_s("sensor.grid") == 60
    assert wd.check_site(T0, site(), s) == ()
    wd.set_cadence("sensor.grid", None)
    assert wd.cadence_s("sensor.grid") is None
    import pytest  # noqa: PLC0415

    with pytest.raises(ValueError, match="positive"):
        wd.set_cadence("x", 0)


def test_jump_detection_on_power_only() -> None:
    wd = Watchdog(SETTINGS)
    assert wd.check_site(T0, site(), samples(T0)) == ()
    t1 = T0 + timedelta(seconds=1)
    assert kinds(wd.check_site(t1, site(), samples(t1, grid=5000.0))) == ["jump"]  # 6000 W/s
    t2 = T0 + timedelta(seconds=2)
    assert wd.check_site(t2, site(), samples(t2, grid=5500.0)) == ()
    # SoC is a ratio: a 40 point step is not a power jump.
    t3 = T0 + timedelta(seconds=3)
    assert wd.check_site(t3, site(), samples(t3, grid=5500.0, soc=100.0)) == ()


def test_frozen_pv_in_daylight_is_an_inverter_hang() -> None:
    wd = Watchdog(SETTINGS)
    for n in range(7):
        t = T0 + timedelta(seconds=100 * n)
        faults = wd.check_site(t, site(), samples(t, pv=2500.0))
        assert faults == () if n < 6 else kinds(faults) == ["frozen"]
    t = T0 + timedelta(seconds=800)
    assert wd.check_site(t, site(), samples(t, pv=2501.0)) == ()  # a change resets the timer
    # At night a frozen 0 W is normal.
    wd2 = Watchdog(SETTINGS)
    for n in range(10):
        t = T0 + timedelta(seconds=100 * n)
        assert wd2.check_site(t, site(), samples(t, pv=0.0)) == ()


def test_balance_check_only_with_a_measured_house() -> None:
    wd = Watchdog(SETTINGS)
    # pv 3000 + grid -1000 - bat 500 = 1500 W computed.
    assert wd.check_site(T0, site(house=True), samples(T0, house=1500.0)) == ()
    assert wd.check_site(T0, site(house=True), samples(T0, house=1900.0)) == ()  # within 500 W
    faults = wd.check_site(T0, site(house=True), samples(T0, house=3000.0))
    assert kinds(faults) == ["balance"]
    assert faults[0].reason.key == "safety.watchdog_balance"
    # Without the house sensor the balance cannot be checked, and a missing input skips it.
    assert wd.check_site(T0, site(), samples(T0)) == ()
    assert kinds(wd.check_site(T0, site(house=True), samples(T0))) == ["unavailable"]


def test_state_machine_safe_cooldown_normal() -> None:
    wd = Watchdog(SETTINGS)
    wd.check_site(T0, site(), samples(T0, grid=None))
    assert wd.state(SITE_SCOPE, T0) is ScopeState.SAFE
    assert wd.is_paused("dev", T0)
    assert wd.site_paused(T0)
    reason = wd.pause_reason("dev", T0)
    assert reason is not None
    assert reason.key == "safety.watchdog_unavailable"
    t1 = T0 + timedelta(seconds=10)
    wd.check_site(t1, site(), samples(t1))
    assert wd.state(SITE_SCOPE, t1) is ScopeState.COOLDOWN
    assert wd.pause_reason("dev", t1) == REASON_COOLDOWN
    assert wd.faults(SITE_SCOPE) != ()
    t2 = t1 + timedelta(seconds=60)
    wd.check_site(t2, site(), samples(t2, grid=None))  # re-trips during the cool-down
    assert wd.state(SITE_SCOPE, t2) is ScopeState.SAFE
    t3 = t2 + timedelta(seconds=10)
    wd.check_site(t3, site(), samples(t3))
    t4 = t3 + timedelta(seconds=119)
    assert wd.state(SITE_SCOPE, t4) is ScopeState.COOLDOWN
    t5 = t3 + timedelta(seconds=120)
    assert wd.state(SITE_SCOPE, t5) is ScopeState.NORMAL
    assert wd.faults(SITE_SCOPE) == ()
    assert not wd.is_paused("dev", t5)


def test_zero_cooldown_returns_to_normal_immediately() -> None:
    wd = Watchdog(ControlSettings(safe_state_cooldown_s=0))
    wd.check_site(T0, site(), samples(T0, grid=None))
    wd.check_site(T0 + timedelta(seconds=5), site(), samples(T0 + timedelta(seconds=5)))
    assert wd.state(SITE_SCOPE, T0 + timedelta(seconds=5)) is ScopeState.NORMAL


def test_device_scope_is_independent_of_the_site() -> None:
    wd = Watchdog(SETTINGS)
    faults = wd.check_device(
        T0,
        "ac",
        {"sensor.ac_power": Sample(None, Unit.W, T0, Quality.UNAVAILABLE)},
        DeviceState(on=True),
    )
    assert kinds(faults) == ["unavailable"]
    assert wd.is_paused("ac", T0)
    assert not wd.is_paused("pool", T0)
    assert not wd.site_paused(T0)
    assert kinds(wd.check_device(T0, "pool", {}, None)) == ["unavailable"]
    assert kinds(wd.check_device(T0, "pool", {}, DeviceState(quality=Quality.INVALID))) == [
        "invalid"
    ]
    assert kinds(
        wd.check_device(
            T0, "pool", {"s": Sample(1.0, Unit.W, T0, Quality.INVALID)}, DeviceState(on=True)
        )
    ) == ["invalid"]
    assert (
        wd.check_device(T0, "pool", {"sensor.p": Sample(1.0, Unit.W, T0)}, DeviceState(on=True))
        == ()
    )
    wd.forget("ac")
    assert wd.state("ac", T0) is ScopeState.NORMAL


def test_connection_loss_and_restore() -> None:
    wd = Watchdog(SETTINGS)
    wd.check_site(T0, site(), samples(T0))
    wd.connection_lost(T0)
    assert not wd.connected
    assert wd.is_paused("x", T0)
    assert wd.pause_reason("x", T0) == REASON_CONNECTION_LOST
    assert wd.state(SITE_SCOPE, T0) is ScopeState.SAFE
    t1 = T0 + timedelta(minutes=10)
    faults = wd.check_site(t1, site(), samples(t1, grid=4000.0, pv=2000.0))  # offline: stays
    assert kinds(faults) == ["connection_lost"]
    wd.connection_restored(t1)
    assert wd.connected
    assert wd.state(SITE_SCOPE, t1) is ScopeState.COOLDOWN
    t2 = t1 + timedelta(seconds=1)
    assert wd.check_site(t2, site(), samples(t2, grid=4000.0)) == ()  # the gap is not a jump


def test_trip_forces_the_safe_state() -> None:
    wd = Watchdog(SETTINGS)
    wd.trip("pool", Reason(key="safety.repeated_failures"), T0)
    assert wd.state("pool", T0) is ScopeState.SAFE
    reason = wd.pause_reason("pool", T0)
    assert reason is not None
    assert reason.key == "safety.repeated_failures"


@settings(max_examples=100, deadline=None)
@given(st.lists(st.booleans(), min_size=1, max_size=30))
def test_scope_is_never_normal_while_a_fault_is_present(sequence: list[bool]) -> None:
    wd = Watchdog(SETTINGS)
    for n, faulty in enumerate(sequence):
        t = T0 + timedelta(seconds=30 * n)
        wd.check_site(t, site(), samples(t, grid=None if faulty else -500.0, pv=3000.0 + n))
        state = wd.state(SITE_SCOPE, t)
        if faulty:
            assert state is ScopeState.SAFE
        else:
            assert state is not ScopeState.SAFE
    assert FaultKind.STALE.value == "stale"


def test_an_old_value_the_cache_still_trusts_is_not_stale() -> None:
    """An unchanged SoC or idle sensor (cache quality OK) is never a fault, cadence or not."""
    wd = Watchdog(SETTINGS, cadence_s={"sensor.soc": 60})
    s = samples(T0)
    s["sensor.soc"] = Sample(84.0, Unit.PERCENT, T0 - timedelta(hours=3), Quality.OK)
    assert wd.check_site(T0, site(), s) == ()
