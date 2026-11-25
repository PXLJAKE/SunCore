# SPDX-License-Identifier: Apache-2.0
"""The parameter registry (Phase 5 WP5, ADR-0015): specs, ranges, safety bounds, expert section."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError

from suncore.core.config import SECTION_MODELS, ExportTariff, TariffConfig
from suncore.core.control import INPUTS_MAX_AGE_LIMIT_S, SAFETY_LIMITS, ControlSettings
from suncore.core.expert import ExpertConfig
from suncore.core.params import (
    GROUPS,
    KNOWN_TAGS,
    REGISTRY,
    UNEXPOSED_CONTROL_FIELDS,
    ParamError,
    ParamSpec,
    SectionMissingError,
    apply_controller,
    current_value,
    expert_safety_diff,
    expert_value,
    overrides_for,
    plan_write,
    reset_keys,
    sanitise_stored_overrides,
    spec,
)

LOCALES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "i18n" / "locales"
SAFETY_SPECS = [s for s in REGISTRY.values() if s.safety]
NUMERIC = [s for s in REGISTRY.values() if s.kind in ("int", "float") and s.store == "expert"]


def test_every_spec_is_well_formed() -> None:
    assert REGISTRY
    for s in REGISTRY.values():
        assert s.key.startswith(s.group) or s.group == s.key.split(".")[0], s.key
        assert s.group in GROUPS
        assert s.tags <= KNOWN_TAGS
        assert "expert" in s.tags
        assert (s.store == "config") == (s.section is not None)
        assert (s.store == "expert") == (s.target is not None)
        if s.kind in ("int", "float"):
            assert s.minimum is not None
            assert s.maximum is not None
            assert s.minimum <= s.maximum
        if s.kind == "enum":
            assert s.default in s.choices


@pytest.mark.parametrize("parameter", list(REGISTRY.values()), ids=lambda s: s.key)
def test_the_default_is_inside_its_range(parameter: ParamSpec) -> None:
    assert parameter.check(parameter.default) == parameter.default


@pytest.mark.parametrize("lang", ["en", "de"])
def test_every_spec_has_a_name_and_a_help_text_in_both_catalogues(lang: str) -> None:
    path = LOCALES / f"params.{lang}.json"
    catalogue: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
    for s in REGISTRY.values():
        assert catalogue.get(s.name_key, "").strip(), s.name_key
        assert catalogue.get(s.help_key, "").strip(), s.help_key
    for group in GROUPS:
        assert catalogue.get(f"param.group.{group}"), group


def test_the_controller_table_covers_every_control_setting() -> None:
    exposed = {s.target for s in REGISTRY.values() if s.group == "controller"}
    assert exposed | UNEXPOSED_CONTROL_FIELDS == set(ControlSettings.model_fields)
    assert not exposed & UNEXPOSED_CONTROL_FIELDS


def test_controller_defaults_are_those_of_control_settings() -> None:
    today = ControlSettings()
    for s in REGISTRY.values():
        if s.group == "controller":
            assert s.target is not None
            assert getattr(today, s.target) == s.default, s.key


def test_safety_ranges_are_the_safety_limits_and_inside_the_model_bounds() -> None:
    """Registry and ``SAFETY_LIMITS`` are one table; the limits sit inside what the model
    accepts, and contain the default."""
    tagged = {s.target for s in SAFETY_SPECS if s.group == "controller"}
    assert tagged == set(SAFETY_LIMITS)
    for s in SAFETY_SPECS:
        assert s.target is not None
        assert (s.minimum, s.maximum) == SAFETY_LIMITS[s.target]
        info = ControlSettings.model_fields[s.target]
        for item in info.metadata:
            lo, hi = getattr(item, "ge", None), getattr(item, "le", None)
            if lo is not None:
                assert lo <= SAFETY_LIMITS[s.target][0], s.key
            if hi is not None:
                assert hi >= SAFETY_LIMITS[s.target][1], s.key


def test_safety_limits_are_finite_and_never_allow_a_check_to_be_disabled() -> None:
    """A protection that is a limit (interval, count, age, window) has a positive minimum;
    the only zero-able safety value is the battery budget (0 = no deficit allowed at all)."""
    for name, (lo, hi) in SAFETY_LIMITS.items():
        assert math.isfinite(lo)
        assert math.isfinite(hi)
        assert lo <= hi
        if name != "deficit_budget_wh":
            assert lo > 0, name


def test_config_params_are_the_fields_with_param_metadata() -> None:
    """Every model field that carries ``param`` metadata is registered, and the other way."""
    found: set[tuple[str, tuple[str, ...]]] = set()

    def walk(section: str, model: type[BaseModel], path: tuple[str, ...]) -> None:
        for name, info in model.model_fields.items():
            extra = info.json_schema_extra
            if isinstance(extra, dict) and "param" in extra:
                found.add((section, (*path, name)))
            for ann in (info.annotation, *getattr(info.annotation, "__args__", ())):
                if isinstance(ann, type) and issubclass(ann, BaseModel):
                    walk(section, ann, (*path, name))

    for section, model in SECTION_MODELS.items():
        walk(section, model, ())
    registered = {(s.section, s.path) for s in REGISTRY.values() if s.store == "config"}
    assert found == registered


def test_config_param_defaults_are_the_model_defaults() -> None:
    export = ExportTariff(kind="none")
    assert export.remunerate_negative is spec("tariff.export.remunerate_negative").default
    assert export.negative_reference_entity is None
    assert spec("tariff.export.negative_reference_entity").default is None


# --- range checks ------------------------------------------------------------------------


def test_check_rejects_out_of_range_wrong_type_and_non_finite() -> None:
    parameter = spec("controller.min_write_interval_s")
    assert parameter.check(10) == 10.0
    for bad in (0.5, 61, True, "5", None, math.nan, math.inf):
        with pytest.raises(ParamError):
            parameter.check(bad)
    with pytest.raises(ParamError) as info:
        parameter.check(0.5)
    assert info.value.code == "param_out_of_range"
    assert info.value.context == {"min": 1.0, "max": 60.0}


def test_check_int_accepts_integral_floats_only() -> None:
    parameter = spec("controller.default_max_switches_per_hour")
    assert parameter.check(4.0) == 4
    assert isinstance(parameter.check(4.0), int)
    with pytest.raises(ParamError):
        parameter.check(4.5)
    with pytest.raises(ParamError):
        parameter.check(31)


def test_check_enum_bool_and_entity() -> None:
    assert spec("forecast.learning.load_ml_mode").check("auto") == "auto"
    with pytest.raises(ParamError) as info:
        spec("forecast.learning.load_ml_mode").check("magic")
    assert info.value.code == "param_not_in_choices"
    assert spec("tariff.export.remunerate_negative").check(True) is True
    with pytest.raises(ParamError):
        spec("tariff.export.remunerate_negative").check(1)
    entity = spec("tariff.export.negative_reference_entity")
    assert entity.check(None) is None
    assert entity.check("sensor.spot_price") == "sensor.spot_price"
    for bad in ("light.kitchen", "sensor.", "Sensor.X", 5):
        with pytest.raises(ParamError):
            entity.check(bad)


def test_unknown_key_and_group() -> None:
    with pytest.raises(ParamError) as info:
        spec("controller.nope")
    assert info.value.code == "param_unknown"
    with pytest.raises(ParamError):
        reset_keys(group="nope")
    with pytest.raises(ValueError, match="exactly one"):
        reset_keys()
    assert reset_keys(key="controller.tick_s") == ["controller.tick_s"]
    assert all(k.startswith("planner.") for k in reset_keys(group="planner"))


# --- the expert section --------------------------------------------------------------------


def test_the_empty_section_changes_nothing() -> None:
    """Golden test: no override = today's ControlSettings, bit for bit."""
    assert apply_controller(ControlSettings(), None) == ControlSettings()
    assert apply_controller(ControlSettings(), ExpertConfig()) == ControlSettings()
    base = ControlSettings(tick_s=7.0)
    assert apply_controller(base, ExpertConfig()) is base
    assert overrides_for(ExpertConfig(), "planner") == {}


def test_overrides_apply_to_their_group_only() -> None:
    expert = ExpertConfig(
        overrides={"controller.tick_s": 10, "planner.budget_s": 20.0, "controller.reserve_w": 200}
    )
    settings = apply_controller(ControlSettings(), expert)
    assert settings.tick_s == 10.0
    assert settings.reserve_w == 200.0
    assert settings.smoothing_s == ControlSettings().smoothing_s
    assert overrides_for(expert, "planner") == {"budget_s": 20.0}


@pytest.mark.parametrize(
    "overrides",
    [
        {"controller.nope": 1},
        {"tariff.export.remunerate_negative": True},  # a config parameter, not an override
        {"controller.min_write_interval_s": 0.5},
        {"controller.min_write_interval_s": "5"},
        {"controller.default_max_switches_per_hour": 100},
    ],
)
def test_the_document_model_rejects_bad_overrides(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ExpertConfig(overrides=overrides)


def test_the_document_model_rejects_unknown_fields_and_keeps_numbers_typed() -> None:
    with pytest.raises(ValidationError):
        ExpertConfig.model_validate({"overrides": {}, "extra": 1})
    doc = ExpertConfig(overrides={"controller.tick_s": 10})
    assert isinstance(doc.overrides["controller.tick_s"], float)
    assert doc.schema_version == 1


@given(data=st.data())
def test_no_value_inside_a_safety_range_leaves_the_safety_limits(data: st.DataObject) -> None:
    """The property of ADR-0015: any value the registry accepts yields a ``ControlSettings``
    whose safety fields are inside ``SAFETY_LIMITS``."""
    parameter = data.draw(st.sampled_from(SAFETY_SPECS))
    assert parameter.minimum is not None
    assert parameter.maximum is not None
    if parameter.kind == "int":
        value: float = data.draw(st.integers(int(parameter.minimum), int(parameter.maximum)))
    else:
        value = data.draw(
            st.floats(parameter.minimum, parameter.maximum, allow_nan=False, allow_infinity=False)
        )
    expert = ExpertConfig(overrides={parameter.key: value})
    settings = apply_controller(ControlSettings(), expert)
    for name, (lo, hi) in SAFETY_LIMITS.items():
        assert lo <= getattr(settings, name) <= hi, name


@given(
    value=st.one_of(
        st.floats(allow_nan=False, allow_infinity=True, width=32),
        st.integers(-(10**9), 10**9),
    ),
    data=st.data(),
)
def test_a_value_outside_a_range_is_rejected_by_the_model_and_clamped_when_forced(
    value: float, data: st.DataObject
) -> None:
    """A row that bypassed the model (hand-patched database) is clamped on the way in."""
    parameter = data.draw(st.sampled_from(NUMERIC))
    assert parameter.minimum is not None
    assert parameter.maximum is not None
    inside = parameter.minimum <= value <= parameter.maximum and (
        parameter.kind != "int" or float(value).is_integer()
    )
    if not inside:
        with pytest.raises(ValidationError):
            ExpertConfig(overrides={parameter.key: value})
    forced = ExpertConfig.model_construct(overrides={parameter.key: value})
    group = overrides_for(forced, parameter.group)
    assert parameter.target is not None
    assert parameter.minimum <= group[parameter.target] <= parameter.maximum
    if parameter.group == "controller":
        settings = apply_controller(ControlSettings(), forced)
        for name, (lo, hi) in SAFETY_LIMITS.items():
            assert lo <= getattr(settings, name) <= hi, name


def test_safety_diff_names_only_changed_safety_keys() -> None:
    old = ExpertConfig(overrides={"controller.reserve_w": 200})
    new = ExpertConfig(overrides={"controller.reserve_w": 200, "controller.tick_s": 9})
    assert expert_safety_diff(old, new) == []
    assert expert_safety_diff(None, ExpertConfig(overrides={"controller.tick_s": 9})) == []
    assert expert_safety_diff(None, old) == ["controller.reserve_w"]
    assert expert_safety_diff(old, ExpertConfig()) == ["controller.reserve_w"]


# --- plan_write ----------------------------------------------------------------------------


def _tariff() -> TariffConfig:
    return TariffConfig.model_validate(
        {
            "import": {"kind": "fixed", "fixed_price": 0.3},
            "export": {"kind": "fixed", "fixed_price": 0.08},
        }
    )


def test_plan_write_expert_values_stay_sparse() -> None:
    sections: dict[str, BaseModel | None] = {"expert": None}
    plan = plan_write({"controller.tick_s": 9, "planner.budget_s": 8.0}, sections)
    assert [c.section for c in plan] == ["expert"]
    model = plan[0].model
    assert isinstance(model, ExpertConfig)
    assert model.overrides == {"controller.tick_s": 9.0}  # the default is not stored
    again = plan_write({"controller.tick_s": 5}, {"expert": model})
    assert again[0].model == ExpertConfig()
    assert expert_value(model, "controller.tick_s") == 9.0


def test_plan_write_config_values_go_into_their_section() -> None:
    sections: dict[str, BaseModel | None] = {"tariff": _tariff(), "expert": None}
    plan = plan_write(
        {
            "tariff.export.remunerate_negative": True,
            "tariff.export.negative_reference_entity": "sensor.spot",
        },
        sections,
    )
    assert [c.section for c in plan] == ["tariff"]
    tariff = plan[0].model
    assert isinstance(tariff, TariffConfig)
    assert tariff.export.remunerate_negative is True
    assert tariff.export.negative_reference_entity is not None
    assert tariff.export.negative_reference_entity.entity_id == "sensor.spot"
    after = {"tariff": tariff}
    assert current_value(spec("tariff.export.negative_reference_entity"), after) == "sensor.spot"
    cleared = plan_write({"tariff.export.negative_reference_entity": None}, after)
    model = cleared[0].model
    assert isinstance(model, TariffConfig)
    assert model.export.negative_reference_entity is None


def test_plan_write_is_all_or_nothing_and_needs_the_section() -> None:
    sections: dict[str, BaseModel | None] = {"tariff": None, "expert": None}
    with pytest.raises(SectionMissingError):
        plan_write({"tariff.export.remunerate_negative": True}, sections)
    with pytest.raises(ParamError):
        plan_write({"controller.tick_s": 9, "controller.reserve_w": 1}, sections)


def test_current_value_falls_back_to_the_default_without_a_section() -> None:
    parameter = spec("forecast.learning.min_history_days")
    assert current_value(parameter, {"forecast": None}) == 30
    assert current_value(spec("controller.tick_s"), {}) == 5.0


# --- control-safety review of PR 5-E ---------------------------------------------------------


def test_the_input_staleness_window_cannot_exceed_its_hard_bound() -> None:
    """``inputs_max_age_ticks * tick_s`` is how old the gate's flows may be (import limit,
    battery lease); both overrides together may not push it past 180 s."""
    with pytest.raises(ValidationError):
        ExpertConfig(overrides={"controller.tick_s": 60, "controller.inputs_max_age_ticks": 10})
    ExpertConfig(overrides={"controller.tick_s": 18, "controller.inputs_max_age_ticks": 10})
    # The tick of the app option is unknown to the document: the resolution reduces instead.
    expert = ExpertConfig(overrides={"controller.inputs_max_age_ticks": 10})
    assert apply_controller(ControlSettings(tick_s=60.0), expert).inputs_max_age_ticks == 3
    assert apply_controller(ControlSettings(tick_s=5.0), expert).inputs_max_age_ticks == 10


def test_apply_controller_validates_the_result() -> None:
    """``model_copy`` would skip validation; a forced override outside the model's own bounds
    must not reach the stack (the clamp keeps it inside, the validation is the backstop)."""
    forced = ExpertConfig.model_construct(overrides={"controller.tick_s": "fast"})
    with pytest.raises(ValidationError):
        apply_controller(ControlSettings(), forced)


@given(data=st.data(), tick=st.integers(2, 60))
def test_any_accepted_document_keeps_every_safety_bound_at_any_app_tick(
    data: st.DataObject, tick: int
) -> None:
    """The ADR-0015 property over whole documents (several keys at once) and every app option
    tick: the safety fields stay in ``SAFETY_LIMITS`` and the staleness window in 180 s."""
    chosen = data.draw(st.lists(st.sampled_from(NUMERIC), unique=True, max_size=8))
    overrides: dict[str, float] = {}
    for parameter in chosen:
        if parameter.group != "controller":
            continue
        assert parameter.minimum is not None
        assert parameter.maximum is not None
        if parameter.kind == "int":
            overrides[parameter.key] = data.draw(
                st.integers(int(parameter.minimum), int(parameter.maximum))
            )
        else:
            overrides[parameter.key] = data.draw(
                st.floats(parameter.minimum, parameter.maximum, allow_nan=False)
            )
    try:
        expert = ExpertConfig(overrides=overrides)
    except ValidationError:
        tick_s = overrides["controller.tick_s"]
        assert overrides["controller.inputs_max_age_ticks"] * tick_s > INPUTS_MAX_AGE_LIMIT_S
        return
    settings = apply_controller(ControlSettings(tick_s=float(tick)), expert)
    for name, (lo, hi) in SAFETY_LIMITS.items():
        assert lo <= getattr(settings, name) <= hi, name
    assert settings.inputs_max_age_ticks * settings.tick_s <= INPUTS_MAX_AGE_LIMIT_S


@given(value=st.one_of(st.floats(allow_nan=True, allow_infinity=True), st.integers(), st.text()))
def test_the_load_repair_never_yields_a_value_outside_the_registry(value: object) -> None:
    for parameter in REGISTRY.values():
        if parameter.store != "expert":
            continue
        repaired, fixed = sanitise_stored_overrides({parameter.key: value})
        if parameter.key in repaired:
            assert parameter.check(repaired[parameter.key]) == repaired[parameter.key]
        else:
            assert fixed == [parameter.key]
        ExpertConfig(overrides=repaired)  # always loadable
