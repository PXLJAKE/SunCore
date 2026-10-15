# SPDX-License-Identifier: Apache-2.0
"""A water heater's ``comfort.max_c`` is enforced through its tank probe (final review 3-B, B2).

Nothing read the ``temperature`` role before: ``check_comfort`` only judges set-points, so a tank
at its maximum could still be heated. Now ``core.control.heat_capped`` (probe at/above
``max_c``, or unknown/stale/not °C) caps the controller at level 0 and the gate rejects any load
increase.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from suncore.app.control import probe_temperature_c
from suncore.controller import Controller, ControllerInputs
from suncore.core.config import GoalsConfig
from suncore.core.control import ControlSettings, DeviceState, PowerEstimate, heat_capped
from suncore.core.decisions import DecisionKind, StepAction, SwitchAction
from suncore.core.devices import ComfortBand, DeviceConfig
from suncore.core.site import EntityRef
from suncore.ha.cache import EntityCache
from suncore.ha.connection import EntityState, EntityUpdate
from suncore.safety.invariants import InvariantContext, check_heat_limit, power_delta_w
from tests.safety.helpers import T0, FakeClock, climate_device, flows, make_intent, runtime
from tests.safety.helpers import steps_device as plain_steps_device
from tests.safety.test_gate import make_harness

MAX_C = 60.0
PROBE = "sensor.tank"


def heater(*, probe: bool = True) -> DeviceConfig:
    device = plain_steps_device()
    entities = dict(device.entities)
    if probe:
        entities["temperature"] = EntityRef(entity_id=PROBE)
    return device.model_copy(
        update={"entities": entities, "comfort": ComfortBand(min_c=35.0, max_c=MAX_C)}
    )


def at_step(step: int, temperature_c: float | None) -> DeviceState:
    return DeviceState(step=step, on=step > 0, temperature_c=temperature_c)


# --- the shared rule ---------------------------------------------------------------------------


def test_heat_capped_rule() -> None:
    assert heat_capped(heater(), at_step(0, MAX_C))
    assert heat_capped(heater(), at_step(0, MAX_C + 5))
    assert heat_capped(heater(), at_step(0, None))  # unknown counts as full
    assert heat_capped(heater(), None)
    assert not heat_capped(heater(), at_step(0, MAX_C - 0.5))
    assert not heat_capped(heater(probe=False), at_step(0, None))  # no probe: not enforceable
    assert not heat_capped(climate_device(), DeviceState(on=True, setpoint_c=24.0))


# --- the gate invariant --------------------------------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(
    target=st.integers(min_value=0, max_value=2),
    current=st.integers(min_value=0, max_value=2),
    temperature=st.one_of(st.none(), st.floats(min_value=0.0, max_value=95.0)),
    estimate_w=st.one_of(st.none(), st.floats(min_value=0.0, max_value=3500.0)),
)
def test_no_load_increase_at_the_tank_maximum(
    target: int, current: int, temperature: float | None, estimate_w: float | None
) -> None:
    device = heater()
    state = at_step(current, temperature)
    ctx = InvariantContext(
        action=StepAction(step=target),
        device=device,
        state=state,
        runtime=runtime("heater"),
        now=T0,
        settings=ControlSettings(),
        estimate=None
        if estimate_w is None
        else PowerEstimate(value_w=estimate_w, uncertainty_w=10.0),
    )
    reason = check_heat_limit(ctx)
    delta = power_delta_w(ctx)
    increase = delta is None or delta > 0
    full = temperature is None or temperature >= MAX_C
    if full and increase:
        assert reason is not None
        expected = "safety.heat_temperature_unknown" if temperature is None else "safety.heat_limit"
        assert reason.key == expected
    else:
        assert reason is None  # below the maximum, or not more heat: never blocked


def test_switching_off_is_always_allowed() -> None:
    ctx = InvariantContext(
        action=StepAction(step=0),
        device=heater(),
        state=at_step(2, None),
        runtime=runtime("heater"),
        now=T0,
        settings=ControlSettings(),
    )
    assert check_heat_limit(ctx) is None


def test_the_gate_rejects_heat_for_a_full_tank() -> None:
    h = make_harness((heater(),))
    h.states["heater"] = at_step(0, MAX_C + 1)
    h.actuators["heater"].observed = StepAction(step=0)
    decision = asyncio.run(
        h.gate.submit(make_intent(StepAction(step=2), asset_id="heater", ts=h.clock.now))
    )
    assert decision.kind is DecisionKind.REJECTED
    assert "safety.heat_limit" in [r.key for r in decision.reasons]
    assert h.actuators["heater"].applied == []
    h.states["heater"] = at_step(0, 45.0)
    accepted = asyncio.run(
        h.gate.submit(
            make_intent(StepAction(step=2), asset_id="heater", intent_id="i-2", ts=h.clock.now)
        )
    )
    assert accepted.kind is DecisionKind.ACCEPTED


# --- the controller ------------------------------------------------------------------------------


def run_controller(temperature: float | None, *, step: int) -> list[object]:
    device = heater()
    controller = Controller([device])
    actions: list[object] = []
    for n in range(60):
        result = controller.step(
            T0 + timedelta(seconds=5 * n),
            ControllerInputs(
                flows=flows(grid_w=-6000.0),
                states={"heater": at_step(step, temperature)},
                estimates={
                    "heater": PowerEstimate(value_w=[0.0, 1000.0, 3000.0][step], uncertainty_w=10.0)
                },
                goals=GoalsConfig(),
            ),
        )
        actions.extend(i.action for i in result.intents)
    return actions


def test_the_controller_never_heats_a_full_tank_and_turns_a_running_heater_down() -> None:
    assert run_controller(MAX_C, step=0) == []
    assert run_controller(None, step=0) == []  # unknown probe
    down = run_controller(MAX_C + 2, step=2)
    assert down
    assert all(isinstance(a, StepAction) and a.step < 2 for a in down)


def test_the_controller_heats_below_the_maximum() -> None:
    up = run_controller(MAX_C - 10, step=0)
    assert any(isinstance(a, StepAction) and a.step > 0 for a in up)


def test_a_heater_without_a_probe_is_not_capped() -> None:
    controller = Controller([heater(probe=False)])
    intents = []
    for n in range(60):
        result = controller.step(
            T0 + timedelta(seconds=5 * n),
            ControllerInputs(
                flows=flows(grid_w=-6000.0),
                states={"heater": at_step(0, None)},
                estimates={"heater": PowerEstimate(value_w=0.0, uncertainty_w=10.0)},
                goals=GoalsConfig(),
            ),
        )
        intents.extend(result.intents)
    assert intents
    assert not isinstance(intents[0].action, SwitchAction)


# --- reading the probe ---------------------------------------------------------------------------


def probe_cache(state: str, unit: str | None) -> tuple[EntityCache, FakeClock]:
    clock = FakeClock()
    cache = EntityCache(clock=clock, max_age_s=120.0)
    attributes = {} if unit is None else {"unit_of_measurement": unit}
    cache.ingest(EntityUpdate(changed={PROBE: EntityState(PROBE, state, attributes)}))
    return cache, clock


def test_the_probe_reading_counts_only_when_fresh_and_in_celsius() -> None:
    cache, clock = probe_cache("55.5", "°C")
    assert probe_temperature_c(cache, heater()) == 55.5
    clock.advance(121)
    assert probe_temperature_c(cache, heater()) is None  # stale
    assert probe_temperature_c(probe_cache("130", "°F")[0], heater()) is None
    assert probe_temperature_c(probe_cache("unavailable", "°C")[0], heater()) is None
    assert probe_temperature_c(probe_cache("55", None)[0], heater()) is None
    assert probe_temperature_c(probe_cache("55", "°C")[0], heater(probe=False)) is None
