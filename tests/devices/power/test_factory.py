# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from suncore.core.devices import (
    ComfortBand,
    DeviceConfig,
    DeviceKind,
    PowerModel,
    PowerModelKind,
    SetpointControl,
)
from suncore.core.site import EntityRef
from suncore.devices.power import (
    CounterDerivedModel,
    LearnedPowerModel,
    MeasuredPowerModel,
    NominalPowerModel,
    create_power_model,
    restore_power_model,
)
from tests.devices.helpers import T0, at, climate_state, power_sample


def device(power_model: PowerModel) -> DeviceConfig:
    return DeviceConfig(
        id="ac",
        template_id="lg.thinq_climate",
        name="AC",
        kind=DeviceKind.CLIMATE,
        entities={
            "climate": EntityRef(entity_id="climate.ac"),
            "power": EntityRef(entity_id="sensor.ac_power"),
            "energy": EntityRef(entity_id="sensor.ac_energy"),
        },
        power_model=power_model,
        control=SetpointControl(kind="setpoint", role="climate", shift_max_c=2),
        comfort=ComfortBand(min_c=20, max_c=26),
    )


def test_every_kind_has_a_model() -> None:
    measured = create_power_model(
        device(PowerModel(kind=PowerModelKind.MEASURED, power_role="power")), power_cadence_s=300
    )
    assert isinstance(measured, MeasuredPowerModel)
    assert measured.cadence_s == 300
    assert measured.inputs == ("power",)
    counter = create_power_model(
        device(
            PowerModel(
                kind=PowerModelKind.COUNTER_DERIVED,
                energy_role="energy",
                counter_update_s=3600,
                nominal_w=900,
            )
        )
    )
    assert isinstance(counter, CounterDerivedModel)
    assert counter.update_s == 3600
    learned = create_power_model(
        device(
            PowerModel(
                kind=PowerModelKind.LEARNED,
                nominal_w=900,
                energy_role="energy",
                counter_update_s=3600,
            )
        )
    )
    assert isinstance(learned, LearnedPowerModel)
    assert "energy" in learned.inputs
    nominal = create_power_model(device(PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=900)))
    assert isinstance(nominal, NominalPowerModel)
    assert nominal.estimate(T0, climate_state(), {}).value_w == 900


def test_default_cadence_applies_without_a_hint() -> None:
    model = create_power_model(device(PowerModel(kind=PowerModelKind.MEASURED, power_role="power")))
    assert isinstance(model, MeasuredPowerModel)
    assert model.cadence_s == 10.0


def test_restore_is_a_noop_without_stored_state() -> None:
    model = create_power_model(device(PowerModel(kind=PowerModelKind.LEARNED, nominal_w=900)))
    restore_power_model(model, None)
    restore_power_model(model, {})
    assert model.estimate(T0, climate_state(), {}).value_w == 900


def test_learned_state_survives_through_the_factory() -> None:
    cfg = device(PowerModel(kind=PowerModelKind.LEARNED, nominal_w=900, power_role="power"))
    model = create_power_model(cfg, power_cadence_s=10)
    for i in range(20):
        model.observe(at(i * 30.0), climate_state(), {"power": power_sample(400, at(i * 30.0))})
    restored = create_power_model(cfg, power_cadence_s=10)
    restore_power_model(restored, model.dump_state())
    assert restored.estimate(T0, climate_state(), {}).value_w == pytest.approx(
        model.estimate(T0, climate_state(), {}).value_w
    )


def test_models_satisfy_the_protocol() -> None:
    from suncore.devices.power import PowerEstimator  # noqa: PLC0415

    for kind, extra in (
        (PowerModelKind.MEASURED, {"power_role": "power"}),
        (PowerModelKind.NOMINAL, {"nominal_w": 100.0}),
    ):
        assert isinstance(
            create_power_model(device(PowerModel(kind=kind, **extra))), PowerEstimator
        )  # type: ignore[arg-type]
