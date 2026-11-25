# SPDX-License-Identifier: Apache-2.0
"""Review 6-I H3: when the composition root counts a home battery as present (E6)."""

from __future__ import annotations

from types import SimpleNamespace

from suncore.app.control import ControlStack
from suncore.core.balance import Flow, FlowSnapshot
from suncore.core.signals import Quality


def snapshot(battery_w: float | None) -> FlowSnapshot:
    ok = Flow(0.0, Quality.OK)
    return FlowSnapshot(
        grid_w=ok,
        pv_w=ok,
        battery_w=Flow(battery_w, Quality.OK if battery_w is not None else Quality.UNAVAILABLE),
        house_w=ok,
        battery_soc_pct=Flow(None, Quality.UNAVAILABLE),
    )


def stack(*, battery: object = None, driver: object = None) -> SimpleNamespace:
    return SimpleNamespace(
        _site=SimpleNamespace(battery=battery), battery_driver=driver, _battery_seen=False
    )


def present(s: SimpleNamespace, flows: FlowSnapshot | None) -> bool:
    return ControlStack._battery_present(s, flows)  # type: ignore[arg-type]


def test_a_measured_battery_flow_is_a_battery_for_the_session() -> None:
    s = stack()
    assert not present(s, snapshot(0.0))  # the synthetic 0 W without a configured battery
    assert present(s, snapshot(-800.0))
    assert present(s, snapshot(None))  # a sensor drop-out never removes it again
    assert present(s, None)


def test_a_configured_or_wired_battery_is_present() -> None:
    assert present(stack(battery=object()), None)
    assert present(stack(driver=object()), snapshot(0.0))
