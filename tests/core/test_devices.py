# SPDX-License-Identifier: Apache-2.0
"""Device instance model: validation of power models, comfort, windows and config."""

from __future__ import annotations

from datetime import time
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from suncore.core.decisions import OperatingMode
from suncore.core.devices import (
    ComfortBand,
    ControlKind,
    DeviceConfig,
    DeviceKind,
    PowerModel,
    PowerModelKind,
    TimeWindow,
)
from suncore.core.entities import EntityMeta
from suncore.core.units import Unit


def _config(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "living_ac",
        "template_id": "generic.climate",
        "name": "Living room AC",
        "kind": "climate",
        "entities": {"climate": {"entity_id": "climate.living"}},
        "power_model": {"kind": "nominal", "nominal_w": 900},
        "control": "setpoint",
    }
    base.update(over)
    return base


def test_valid_config_defaults() -> None:
    cfg = DeviceConfig.model_validate(_config())
    assert cfg.mode is OperatingMode.SHADOW
    assert cfg.priority == 0
    assert cfg.min_run_s == 0
    assert cfg.windows == ()
    assert cfg.kind is DeviceKind.CLIMATE
    assert cfg.control is ControlKind.SETPOINT


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
    [{"priority": -1}, {"min_run_s": -1}, {"min_pause_s": -5}, {"max_switches_per_hour": 0}],
)
def test_numeric_bounds(over: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        DeviceConfig.model_validate(_config(**over))


def test_duplicate_entity_across_roles_rejected() -> None:
    entities = {"a": {"entity_id": "switch.x"}, "b": {"entity_id": "switch.x"}}
    with pytest.raises(ValidationError, match="mapped to"):
        DeviceConfig.model_validate(_config(entities=entities))


def test_comfort_only_for_thermal_kinds() -> None:
    comfort = {"min_c": 21, "max_c": 24}
    DeviceConfig.model_validate(_config(comfort=comfort))
    with pytest.raises(ValidationError, match="not applicable"):
        DeviceConfig.model_validate(_config(kind="switch", control="on_off", comfort=comfort))


def test_entity_refs_include_power_model() -> None:
    cfg = DeviceConfig.model_validate(
        _config(
            power_model={
                "kind": "counter_derived",
                "energy_counter": {"entity_id": "sensor.ac_energy"},
                "energy_unit": "Wh",
            }
        )
    )
    ids = {r.entity_id for r in cfg.entity_refs()}
    assert ids == {"climate.living", "sensor.ac_energy"}


def test_mode_can_be_set() -> None:
    cfg = DeviceConfig.model_validate(_config(mode="assisted"))
    assert cfg.mode is OperatingMode.ASSISTED


def test_json_round_trip() -> None:
    cfg = DeviceConfig.model_validate(
        _config(
            windows=[{"start": "22:00", "end": "06:00", "days": [0, 1]}],
            comfort={"min_c": 22, "max_c": 23},
            max_switches_per_hour=4,
        )
    )
    assert DeviceConfig.model_validate_json(cfg.model_dump_json()) == cfg


# --- PowerModel ---------------------------------------------------------------


def test_power_model_measured_needs_power() -> None:
    with pytest.raises(ValidationError, match="measured"):
        PowerModel(kind=PowerModelKind.MEASURED)
    ok = PowerModel.model_validate({"kind": "measured", "power": {"entity_id": "sensor.p"}})
    assert ok.power is not None


def test_power_model_counter_needs_counter_and_unit() -> None:
    with pytest.raises(ValidationError, match="counter_derived"):
        PowerModel(kind=PowerModelKind.COUNTER_DERIVED)
    with pytest.raises(ValidationError, match="energy_unit"):
        PowerModel.model_validate(
            {"kind": "counter_derived", "energy_counter": {"entity_id": "sensor.e"}}
        )
    with pytest.raises(ValidationError, match="Wh or kWh"):
        PowerModel.model_validate(
            {
                "kind": "counter_derived",
                "energy_counter": {"entity_id": "sensor.e"},
                "energy_unit": "W",
            }
        )
    with pytest.raises(ValidationError, match="without"):
        PowerModel.model_validate({"kind": "nominal", "nominal_w": 5, "energy_unit": "Wh"})
    with pytest.raises(ValidationError, match="counter"):
        PowerModel.model_validate(
            {
                "kind": "counter_derived",
                "energy_counter": {"entity_id": "sensor.e", "invert_sign": True},
                "energy_unit": Unit.KWH,
            }
        )


@pytest.mark.parametrize("kind", ["nominal", "learned"])
def test_power_model_nominal_and_learned_need_nominal_w(kind: str) -> None:
    with pytest.raises(ValidationError, match="nominal_w"):
        PowerModel.model_validate({"kind": kind})
    assert PowerModel.model_validate({"kind": kind, "nominal_w": 1200}).nominal_w == 1200


def test_power_model_nominal_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        PowerModel.model_validate({"kind": "nominal", "nominal_w": 0})


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
