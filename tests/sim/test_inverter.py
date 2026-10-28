# SPDX-License-Identifier: Apache-2.0
"""The simulated hybrid inverter (WP10): own control, fallback timer, REST lag, drift, hang."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from suncore.core.battery import ModbusByteOrder
from suncore.devices.battery.modbus_codec import float32_words, uint32_words
from suncore.sim.inverter import MIN_SOC_ID, MODBUS_HUB, MODBUS_SLAVE, InverterParams, SimInverter

T0 = datetime(2026, 12, 7, 20, 0, tzinfo=UTC)
LE = ModbusByteOrder.LITTLE


def inverter(**kw: object) -> SimInverter:
    return SimInverter(
        InverterParams.model_validate(kw),
        capacity_wh=10_000,
        max_charge_w=5000,
        max_discharge_w=5000,
        round_trip_efficiency=0.92,
        soc_max_pct=100,
        initial_soc_pct=50,
        start=T0,
    )


def write(inv: SimInverter, at: datetime, address: int, words: list[int]) -> None:
    inv.write_register(
        at, {"hub": MODBUS_HUB, "slave": MODBUS_SLAVE, "address": address, "value": words}
    )


def test_own_self_consumption_control() -> None:
    inv = inverter()
    assert inv.dispatch(T0, 1000, 10) == 1000  # surplus charges
    assert inv.dispatch(T0, -800, 10) == -800  # deficit discharges


def test_the_fallback_timer_of_section_3_5() -> None:
    inv = inverter(adapter="kostal_modbus")
    write(inv, T0, 1284, float32_words(5000, LE))
    write(inv, T0, 1286, float32_words(5000, LE))
    write(inv, T0, 1288, uint32_words(60, LE))
    write(inv, T0, 1282, float32_words(0, LE))
    assert inv.dispatch(T0 + timedelta(seconds=30), -800, 10) == 0  # locked
    assert inv.fallback_active(T0 + timedelta(seconds=61))
    assert inv.dispatch(T0 + timedelta(seconds=70), -800, 10) == -800  # fell back


def test_an_unarmed_fallback_stops_the_battery_and_writes_need_1080() -> None:
    inv = inverter(adapter="kostal_modbus")
    write(inv, T0, 1288, uint32_words(60, LE))
    write(inv, T0, 1282, float32_words(0, LE))
    assert inv.dispatch(T0 + timedelta(seconds=90), -800, 10) == 0  # fallback limits are 0
    off = inverter(adapter="kostal_modbus", external_control=0)
    write(off, T0, 1282, float32_words(0, LE))
    assert off.registers[1282] == 5000  # ignored without external battery management


def test_rest_lag_silent_failure_drift_and_hang() -> None:
    inv = inverter(adapter="kostal_plenticore", rest_refresh_s=5)
    inv.set_number(T0, MIN_SOC_ID, 40)
    shown = {e: v for e, v, _ in inv.states(T0 + timedelta(seconds=1))}
    assert shown[MIN_SOC_ID] == "10.0"
    shown = {e: v for e, v, _ in inv.states(T0 + timedelta(seconds=6))}
    assert shown[MIN_SOC_ID] == "40.0"
    failing = inverter(adapter="kostal_plenticore", rest_write_fails=True)
    failing.set_number(T0, MIN_SOC_ID, 40)
    assert failing.min_soc_pct == 10
    drifting = inverter(max_drift_pct=4, drift_per_h=2)
    for i in range(360):
        drifting.dispatch(T0 + timedelta(seconds=10 * i), 0, 10)
    assert drifting.reported_soc_pct < drifting.soc_pct - 1.5
    hung = inverter(hang_from_s=0, hang_to_s=600)
    hung.battery_w = 1234
    assert hung.dispatch(T0, -500, 10) == 1234
