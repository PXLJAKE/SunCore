# SPDX-License-Identifier: Apache-2.0
"""Controller -> real gate -> real drivers (test-only import of ``suncore.devices``).

The controller asks only for values the drivers really write (``CallableQuantiser`` over
``NumberDriver.snap_power_w`` / ``ClimateDriver.effective_step_c``), so the gate's
read-back (1 W / set-point tolerance) confirms every write - review 3-B finding F1.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from suncore.controller import CallableQuantiser, Controller, ControllerInputs, ControllerLoop
from suncore.controller.levels import build_levels
from suncore.core.config import GoalsConfig
from suncore.core.control import ControlSettings, GateInputs, PowerEstimate
from suncore.core.decisions import Decision, DecisionKind, OperatingMode, PowerSetpointAction
from suncore.devices.drivers import ClimateDriver, NumberDriver, create_driver
from suncore.safety.gate import SafetyGate
from suncore.safety.modes import StaticModes
from suncore.safety.runtime import MemoryRuntimeStore
from tests.devices.drivers.fakes import Call, FakeHA, climate_device, number_device
from tests.safety.helpers import T0, flows

ROD = "number.rod"
AC = "climate.ac"
SETTINGS = ControlSettings()


def test_levels_are_what_the_drivers_write() -> None:
    ha = FakeHA()
    ha.set(ROD, "6", min=6, max=16, step=1)
    ha.set(AC, "heat", temperature=21, target_temp_step=1, hvac_modes=["off", "heat"])
    rod_cfg = number_device("A")
    ac_cfg = climate_device().model_copy(update={"id": "ac"})
    rod = create_driver(rod_cfg, write=ha.write, states=ha.lookup)
    ac = create_driver(ac_cfg, write=ha.write, states=ha.lookup)
    assert isinstance(rod, NumberDriver)
    assert isinstance(ac, ClimateDriver)
    quantiser = CallableQuantiser(
        setpoint_steps={"ac": ac.effective_step_c}, power_snaps={"rod": rod.snap_power_w}
    )
    table = build_levels(rod_cfg, rod.device_state(), None, SETTINGS, quantiser)
    assert table is not None
    for level in table.levels:
        assert isinstance(level.action, PowerSetpointAction)
        assert rod.snap_power_w(level.action.power_w) == level.action.power_w
    state = ac.device_state()
    table = build_levels(ac_cfg, state, None, SETTINGS, quantiser)
    assert table is not None
    assert [lv.setpoint_c for lv in table.levels] == [21.0, 22.0, 23.0]
    # Unknown devices and odd callables fall back safely.
    assert quantiser.setpoint_step_c("x") is None
    assert quantiser.power_w("x", -1.0) == 0.0
    odd = CallableQuantiser(
        setpoint_steps={"ac": lambda: float("nan")}, power_snaps={"rod": lambda _w: -5.0}
    )
    assert odd.setpoint_step_c("ac") is None
    assert odd.power_w("rod", 100.0) == 0.0


def test_a_continuous_load_follows_the_surplus_through_the_real_driver() -> None:
    ha = FakeHA()
    attrs: dict[str, Any] = {"min": 0, "max": 3000, "step": 100, "unit_of_measurement": "W"}
    ha.set(ROD, "0", **attrs)

    def follow(call: Call) -> None:  # the entity cache follows the write
        assert call.service_data is not None
        ha.set(ROD, str(call.service_data["value"]), **attrs)

    ha.on_call = follow
    cfg = number_device("W")
    driver = create_driver(cfg, write=ha.write, states=ha.lookup)
    assert isinstance(driver, NumberDriver)
    now = [T0]
    holder: dict[str, ControllerLoop] = {}
    decisions: list[Decision] = []

    async def sink(decision: Decision) -> None:
        decisions.append(decision)

    async def no_sleep(_: float) -> None:
        return None

    def gate_inputs() -> GateInputs | None:
        return holder["loop"].gate_inputs

    gate = SafetyGate(
        clock=lambda: now[0],
        sink=sink,
        actuators={cfg.id: driver},
        modes=StaticModes(OperatingMode.AUTOMATIC, {cfg.id: OperatingMode.AUTOMATIC}),
        devices={cfg.id: cfg}.get,
        states=lambda _id: driver.device_state(),
        inputs=gate_inputs,
        store=MemoryRuntimeStore(),
        settings=SETTINGS,
        sleep=no_sleep,
    )
    controller = Controller(
        [cfg],
        settings=SETTINGS,
        quantiser=CallableQuantiser(power_snaps={"rod": driver.snap_power_w}),
    )
    loop = ControllerLoop(controller, submit=gate.submit, poll=gate.poll)
    holder["loop"] = loop

    async def go() -> None:
        for _ in range(60):
            state = driver.device_state()
            power = state.value or 0.0
            grid = 300.0 + power - 2250.0  # 2250 W PV, 300 W base load
            inputs = ControllerInputs(
                flows=flows(grid_w=grid),
                states={cfg.id: state},
                estimates={cfg.id: PowerEstimate(value_w=power, uncertainty_w=10.0)},
                goals=GoalsConfig(climate_before_export=True),
            )
            await loop.tick(now[0], inputs)
            await loop.drain()
            now[0] += timedelta(seconds=5)

    asyncio.run(go())
    kinds = {d.kind for d in decisions}
    assert kinds == {DecisionKind.ACCEPTED}, [d.reasons for d in decisions]
    written = [c.service_data["value"] for c in ha.calls if c.service_data]
    assert written
    assert all(v % 100 == 0 for v in written)
    # 1950 W total - hysteresis/uncertainty: within one dead band of the available power.
    assert 1600 <= written[-1] <= 1900
