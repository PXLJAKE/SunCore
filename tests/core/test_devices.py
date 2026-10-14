# SPDX-License-Identifier: Apache-2.0
"""Device instance model: validation of power models, comfort, windows and config."""

from __future__ import annotations

import itertools
from datetime import time
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter, ValidationError

from suncore.core.devices import (
    THERMAL_KINDS,
    ComfortBand,
    ContinuousControl,
    ControlKind,
    ControlSpec,
    DeviceConfig,
    DeviceKind,
    ModeControl,
    OnOffControl,
    PowerModel,
    PowerModelKind,
    SetpointControl,
    StepsControl,
    TimeWindow,
)
from suncore.core.entities import EntityMeta


def _config(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "living_ac",
        "template_id": "generic.climate",
        "name": "Living room AC",
        "kind": "climate",
        "entities": {"climate": {"entity_id": "climate.living"}},
        "power_model": {"kind": "nominal", "nominal_w": 900},
        "control": {"kind": "setpoint", "role": "climate", "shift_max_c": 2},
        "comfort": {"min_c": 21, "max_c": 24},
    }
    base.update(over)
    return base


def test_valid_config_defaults() -> None:
    cfg = DeviceConfig.model_validate(_config())
    assert cfg.priority == 0
    assert cfg.min_run_s == 0
    assert cfg.windows == ()
    assert cfg.kind is DeviceKind.CLIMATE
    assert isinstance(cfg.control, SetpointControl)
    assert cfg.control.step_c == 0.5
    assert (cfg.hysteresis_w, cfg.hysteresis_delay_s, cfg.settle_s) == (None, None, None)
    assert cfg.max_switches_per_hour is None  # None = safety default, never unlimited


def test_mode_is_not_a_device_field() -> None:
    """ADR-0012 item 10: the `operation` section is the single store of the mode."""
    assert "mode" not in DeviceConfig.model_fields
    with pytest.raises(ValidationError, match="Extra inputs"):
        DeviceConfig.model_validate(_config(mode="assisted"))


def test_frozen_and_extra_forbidden() -> None:
    cfg = DeviceConfig.model_validate(_config())
    with pytest.raises(ValidationError):
        cfg.priority = 3  # type: ignore[misc]
    with pytest.raises(ValidationError, match="Extra inputs"):
        DeviceConfig.model_validate(_config(bogus=1))


@pytest.mark.parametrize("bad", ["Living", "1ac", "a-b", "", "a" * 49])
def test_device_id_slug(bad: str) -> None:
    with pytest.raises(ValidationError):
        DeviceConfig.model_validate(_config(id=bad))


@pytest.mark.parametrize("bad", ["generic", "Generic.climate", "a.b.c", "generic."])
def test_template_id_pattern(bad: str) -> None:
    with pytest.raises(ValidationError):
        DeviceConfig.model_validate(_config(template_id=bad))


@pytest.mark.parametrize(
    "over",
    [
        {"priority": -1},
        {"min_run_s": -1},
        {"min_pause_s": -5},
        {"max_switches_per_hour": 0},
        {"hysteresis_w": -1},
        {"hysteresis_delay_s": 3601},
        {"settle_s": 601},
    ],
)
def test_numeric_bounds(over: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        DeviceConfig.model_validate(_config(**over))


def test_controller_parameters_accepted() -> None:
    cfg = DeviceConfig.model_validate(_config(hysteresis_w=50, hysteresis_delay_s=30, settle_s=5))
    assert (cfg.hysteresis_w, cfg.hysteresis_delay_s, cfg.settle_s) == (50, 30, 5)


def test_duplicate_entity_across_roles_rejected() -> None:
    entities = {"a": {"entity_id": "switch.x"}, "b": {"entity_id": "switch.x"}}
    with pytest.raises(ValidationError, match="mapped to"):
        DeviceConfig.model_validate(
            _config(entities=entities, control={"kind": "on_off", "role": "a"})
        )


def test_comfort_only_for_thermal_kinds() -> None:
    assert {DeviceKind.CLIMATE, DeviceKind.WATER_HEATER, DeviceKind.HEAT_PUMP} == THERMAL_KINDS
    with pytest.raises(ValidationError, match="not applicable"):
        DeviceConfig.model_validate(
            _config(
                kind="switch",
                entities={"sw": {"entity_id": "switch.x"}},
                control={"kind": "on_off", "role": "sw"},
            )
        )


def test_entity_refs_are_the_role_mapping() -> None:
    cfg = DeviceConfig.model_validate(
        _config(
            entities={
                "climate": {"entity_id": "climate.living"},
                "energy": {"entity_id": "sensor.ac_energy"},
            },
            power_model={"kind": "counter_derived", "energy_role": "energy"},
        )
    )
    assert {r.entity_id for r in cfg.entity_refs()} == {"climate.living", "sensor.ac_energy"}


def test_power_model_roles_must_be_mapped() -> None:
    with pytest.raises(ValidationError, match="not mapped"):
        DeviceConfig.model_validate(_config(power_model={"kind": "measured", "power_role": "p"}))
    with pytest.raises(ValidationError, match="not mapped"):
        DeviceConfig.model_validate(
            _config(power_model={"kind": "counter_derived", "energy_role": "e"})
        )


def test_energy_role_must_not_invert_sign() -> None:
    entities = {
        "climate": {"entity_id": "climate.living"},
        "energy": {"entity_id": "sensor.e", "invert_sign": True},
    }
    with pytest.raises(ValidationError, match="counter"):
        DeviceConfig.model_validate(
            _config(
                entities=entities, power_model={"kind": "counter_derived", "energy_role": "energy"}
            )
        )


def test_json_round_trip() -> None:
    cfg = DeviceConfig.model_validate(
        _config(
            windows=[
                {"start": "22:00", "end": "06:00", "days": [0, 1], "mode": "block"},
                {"start": "10:00", "end": "16:00"},
            ],
            comfort={"min_c": 22, "max_c": 23},
            max_switches_per_hour=4,
        )
    )
    assert DeviceConfig.model_validate_json(cfg.model_dump_json()) == cfg
    assert [w.mode for w in cfg.windows] == ["block", "allow"]


# --- PowerModel ---------------------------------------------------------------


def test_power_model_measured_needs_power_role() -> None:
    with pytest.raises(ValidationError, match="measured"):
        PowerModel(kind=PowerModelKind.MEASURED)
    assert PowerModel(kind=PowerModelKind.MEASURED, power_role="power").power_role == "power"


def test_power_model_counter_needs_energy_role() -> None:
    with pytest.raises(ValidationError, match="counter_derived"):
        PowerModel(kind=PowerModelKind.COUNTER_DERIVED)
    ok = PowerModel(kind=PowerModelKind.COUNTER_DERIVED, energy_role="e", counter_update_s=3600)
    assert ok.counter_update_s == 3600
    with pytest.raises(ValidationError, match="without"):
        PowerModel(kind=PowerModelKind.NOMINAL, nominal_w=5, counter_update_s=60)
    for bad in (0, 86_401):
        with pytest.raises(ValidationError):
            PowerModel(kind=PowerModelKind.COUNTER_DERIVED, energy_role="e", counter_update_s=bad)


@pytest.mark.parametrize("kind", ["nominal", "learned"])
def test_power_model_nominal_and_learned_need_nominal_w(kind: str) -> None:
    with pytest.raises(ValidationError, match="nominal_w"):
        PowerModel.model_validate({"kind": kind})
    assert PowerModel.model_validate({"kind": kind, "nominal_w": 1200}).nominal_w == 1200


def test_power_model_nominal_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        PowerModel.model_validate({"kind": "nominal", "nominal_w": 0})


# --- ControlSpec --------------------------------------------------------------

_CONTROL = TypeAdapter(ControlSpec)


def test_control_kinds_match_the_enum() -> None:
    kinds = {
        c.model_fields["kind"].annotation.__args__[0]  # type: ignore[union-attr]
        for c in (OnOffControl, StepsControl, ContinuousControl, SetpointControl, ModeControl)
    }
    assert kinds == {k.value for k in ControlKind}


def test_control_discriminator_and_roles() -> None:
    assert isinstance(_CONTROL.validate_python({"kind": "on_off", "role": "sw"}), OnOffControl)
    with pytest.raises(ValidationError):
        _CONTROL.validate_python({"kind": "on_off"})  # role is mandatory
    with pytest.raises(ValidationError):
        _CONTROL.validate_python({"kind": "teleport", "role": "x"})
    with pytest.raises(ValidationError, match="Extra inputs"):
        _CONTROL.validate_python({"kind": "on_off", "role": "sw", "min": 1})


def test_steps_control() -> None:
    steps = [{"value": "off", "power_w": 0}, {"value": "low", "power_w": 500}]
    ok = _CONTROL.validate_python({"kind": "steps", "role": "r", "steps": steps})
    assert isinstance(ok, StepsControl)
    for bad in (
        steps[:1],
        [steps[0], {"value": "x", "power_w": 0}],  # not strictly increasing
        [{"value": "a", "power_w": 5}, {"value": "b", "power_w": 3}],
        [{"value": i, "power_w": i} for i in range(17)],
        [{"value": "a", "power_w": -1}, {"value": "b", "power_w": 3}],
    ):
        with pytest.raises(ValidationError):
            _CONTROL.validate_python({"kind": "steps", "role": "r", "steps": bad})


@given(powers=st.lists(st.floats(0, 10_000), min_size=2, max_size=16))
def test_steps_strictly_increasing_property(powers: list[float]) -> None:
    steps = [{"value": i, "power_w": p} for i, p in enumerate(powers)]
    increasing = all(a < b for a, b in itertools.pairwise(powers))
    if increasing:
        assert _CONTROL.validate_python({"kind": "steps", "role": "r", "steps": steps})
    else:
        with pytest.raises(ValidationError):
            _CONTROL.validate_python({"kind": "steps", "role": "r", "steps": steps})


def test_continuous_control() -> None:
    w = {"kind": "continuous", "role": "n", "unit": "W", "min": 0, "max": 3000, "step": 100}
    assert isinstance(_CONTROL.validate_python(w), ContinuousControl)
    amps = {**w, "unit": "A", "min": 6, "max": 16, "voltage_v": 230, "phases": 3}
    assert _CONTROL.validate_python(amps).phases == 3  # type: ignore[union-attr]
    for bad in (
        {**w, "max": 0},
        {**w, "min": 5, "max": 5},
        {**w, "min": -1},
        {**w, "step": 0},
        {**w, "unit": "kW"},
        {**w, "phases": 3},  # only valid for A
        {**w, "voltage_v": 400},
        {**w, "unit": "%", "max": 101},
        {**amps, "phases": 2},
    ):
        with pytest.raises(ValidationError):
            _CONTROL.validate_python(bad)


def test_percent_needs_nominal_power_on_the_instance() -> None:
    control = {"kind": "continuous", "role": "n", "unit": "%", "min": 10, "max": 100}
    entities = {"n": {"entity_id": "number.x"}}
    kw: dict[str, Any] = {"kind": "number_load", "entities": entities, "control": control}
    kw["comfort"] = None
    with pytest.raises(ValidationError, match="nominal_w"):
        DeviceConfig.model_validate(
            _config(**kw, power_model={"kind": "measured", "power_role": "n"})
        )
    assert DeviceConfig.model_validate(_config(**kw)).control.kind == "continuous"


def test_setpoint_needs_comfort_and_bounds() -> None:
    with pytest.raises(ValidationError, match="needs `comfort`"):
        DeviceConfig.model_validate(_config(comfort=None))
    for bad in (0, -1, 10.5):
        with pytest.raises(ValidationError):
            _CONTROL.validate_python({"kind": "setpoint", "role": "c", "shift_max_c": bad})


def test_mode_control() -> None:
    modes = [{"value": "eco", "power_w": 100}, {"value": "boost", "power_w": 900}]
    assert _CONTROL.validate_python({"kind": "mode", "role": "r", "modes": modes})
    with pytest.raises(ValidationError, match="unique"):
        _CONTROL.validate_python(
            {"kind": "mode", "role": "r", "modes": [modes[0], {**modes[1], "value": "eco"}]}
        )
    with pytest.raises(ValidationError):
        _CONTROL.validate_python({"kind": "mode", "role": "r", "modes": modes[:1]})


def test_control_role_must_be_mapped() -> None:
    with pytest.raises(ValidationError, match=r"control\.role"):
        DeviceConfig.model_validate(
            _config(control={"kind": "setpoint", "role": "missing", "shift_max_c": 2})
        )


# --- ComfortBand / TimeWindow ---------------------------------------------------


@given(a=st.floats(-50, 150), b=st.floats(-50, 150))
def test_comfort_band_requires_min_below_max(a: float, b: float) -> None:
    if a < b:
        assert ComfortBand(min_c=a, max_c=b).min_c == a
    else:
        with pytest.raises(ValidationError, match="below"):
            ComfortBand(min_c=a, max_c=b)


def test_time_window_rules() -> None:
    assert TimeWindow(start=time(22), end=time(6)).days == (0, 1, 2, 3, 4, 5, 6)
    with pytest.raises(ValidationError, match="differ"):
        TimeWindow(start=time(8), end=time(8))
    with pytest.raises(ValidationError, match="at least one"):
        TimeWindow(start=time(8), end=time(9), days=())
    with pytest.raises(ValidationError, match="unique"):
        TimeWindow(start=time(8), end=time(9), days=(1, 1))
    with pytest.raises(ValidationError):
        TimeWindow(start=time(8), end=time(9), days=(7,))


def test_entity_meta_of_derives_domain() -> None:
    meta = EntityMeta.of("sensor.x", unit="W")
    assert meta.domain == "sensor"
    assert meta.unit == "W"
    assert meta.platform is None


def test_entity_meta_carries_device_id() -> None:
    assert EntityMeta.of("sensor.x", device_id="abc").device_id == "abc"
    assert EntityMeta.of("sensor.x").device_id is None


# --- Phase 3 additive fields -------------------------------------------------------


def test_phase_3_fields_default_so_old_documents_stay_valid() -> None:
    from suncore.core.devices import OverridePolicy  # noqa: PLC0415

    dev = DeviceConfig.model_validate(
        {
            "id": "ac",
            "template_id": "generic.climate",
            "name": "AC",
            "kind": "climate",
            "entities": {"climate": {"entity_id": "climate.ac"}},
            "power_model": {"kind": "nominal", "nominal_w": 800},
            "control": {"kind": "setpoint", "role": "climate", "shift_max_c": 2},
            "comfort": {"min_c": 20, "max_c": 26},
        }
    )
    assert dev.allow_switch_on is False
    assert dev.switch_on_hvac_mode is None
    assert dev.override_policy == OverridePolicy()
    assert dev.override_policy.kind == "next_day"
    assert dev.override_policy.resume_time.hour == 4


def test_switch_on_opt_in_needs_a_mode_and_a_climate_device() -> None:
    from suncore.core.devices import OverridePolicy  # noqa: PLC0415

    base = {
        "id": "ac",
        "template_id": "generic.climate",
        "name": "AC",
        "kind": "climate",
        "entities": {"climate": {"entity_id": "climate.ac"}},
        "power_model": {"kind": "nominal", "nominal_w": 800},
        "control": {"kind": "setpoint", "role": "climate", "shift_max_c": 2},
        "comfort": {"min_c": 20, "max_c": 26},
    }
    ok = DeviceConfig.model_validate(
        {**base, "allow_switch_on": True, "switch_on_hvac_mode": "cool"}
    )
    assert ok.allow_switch_on
    assert ok.switch_on_hvac_mode == "cool"
    with pytest.raises(ValidationError, match="switch_on_hvac_mode"):
        DeviceConfig.model_validate({**base, "allow_switch_on": True})
    with pytest.raises(ValidationError):
        DeviceConfig.model_validate({**base, "allow_switch_on": True, "switch_on_hvac_mode": "dry"})
    switch = {
        "id": "pool",
        "template_id": "generic.switch",
        "name": "Pool",
        "kind": "switch",
        "entities": {"switch": {"entity_id": "switch.pool"}},
        "power_model": {"kind": "nominal", "nominal_w": 800},
        "control": {"kind": "on_off", "role": "switch"},
    }
    with pytest.raises(ValidationError, match="climate"):
        DeviceConfig.model_validate(
            {**switch, "allow_switch_on": True, "switch_on_hvac_mode": "heat"}
        )
    with pytest.raises(ValidationError, match="climate"):
        DeviceConfig.model_validate({**switch, "switch_on_hvac_mode": "heat"})
    hours = DeviceConfig.model_validate(
        {**switch, "override_policy": {"kind": "hours", "hours": 6}}
    )
    assert hours.override_policy == OverridePolicy(kind="hours", hours=6)
    with pytest.raises(ValidationError, match="hours"):
        OverridePolicy(kind="hours")
    with pytest.raises(ValidationError, match="next_day"):
        OverridePolicy(kind="next_day", hours=2)
    with pytest.raises(ValidationError):
        OverridePolicy(kind="hours", hours=49)
