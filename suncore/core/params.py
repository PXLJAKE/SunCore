# SPDX-License-Identifier: Apache-2.0
"""The parameter registry (Phase 5 WP5, ADR-0015): one source for expert UI, validation, docs.

Every tunable the expert mode exposes is a :class:`ParamSpec`: key, type, unit, default, hard
range, group, tags (``expert``, ``safety``, ``restart``, ``whatif``) and the catalogue keys of
its name and help text. Two kinds of parameters exist:

* **Config parameters** are fields of a configuration section (``tariff.export.*``,
  ``forecast.learning.*``). Their metadata sits on the Pydantic field (``json_schema_extra``
  key ``param``, the mechanism of ADR-0006); default and range come from the field itself, so
  there is no second copy. They are written into their own section.
* **Expert parameters** were constants or environment variables (controller tick, smoothing,
  write interval, solver budget, ...). They live in the sparse ``expert`` section
  (:class:`~suncore.core.expert.ExpertConfig`); the explicit table below lists them. The
  defaults of the controller table are read from :class:`~suncore.core.control.ControlSettings`,
  the safety ranges from :data:`~suncore.core.control.SAFETY_LIMITS`; the planner defaults are
  copied from the planner runtime and tied to it by a test.

**Expert mode never weakens the safety layer.** The range of a ``safety``-tagged parameter is
the hard bound of the corresponding invariant (``SAFETY_LIMITS``); a value outside it is
rejected everywhere (API, document model, resolution at start-up); no value inside it switches
a check off. Expert mode writes configuration, never device set-points or modes.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticCustomError

from suncore.core.config import ExportTariff, LearningSettings
from suncore.core.control import (
    INPUTS_MAX_AGE_LIMIT_S,
    SAFETY_LIMITS,
    ControlSettings,
    inputs_max_age_ticks_within,
)
from suncore.core.expert import ExpertConfig
from suncore.core.validation import invalid

ParamType = Literal["float", "int", "bool", "enum", "entity"]
Store = Literal["expert", "config"]
ParamValue = bool | int | float | str | None

TAG_EXPERT: Final = "expert"
TAG_SAFETY: Final = "safety"
TAG_RESTART: Final = "restart"
TAG_WHATIF: Final = "whatif"
KNOWN_TAGS: Final = frozenset({TAG_EXPERT, TAG_SAFETY, TAG_RESTART, TAG_WHATIF})

GROUPS: Final = ("controller", "planner", "tariff", "forecast")
"""Display order of the groups; also the values accepted by ``POST /params/reset``."""

ENTITY_PATTERN: Final = re.compile(r"^(sensor|input_number)\.[a-z0-9_]{1,200}$")


class ParamError(ValueError):
    """A value does not fit its spec. ``code`` is the validation key (``param_*``)."""

    def __init__(self, code: str, key: str, **context: str | int | float | bool) -> None:
        super().__init__(f"{key}: {code}")
        self.code = code
        self.key = key
        self.context = context

    def to_pydantic(self) -> PydanticCustomError:
        return invalid(self.code, f"{self.key}: {self.code}", param=self.key, **self.context)


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """One tunable parameter."""

    key: str
    kind: ParamType
    group: str
    default: ParamValue
    store: Store
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    tags: frozenset[str] = frozenset({TAG_EXPERT})
    section: str | None = None
    """Config parameters: the configuration section that holds the field."""
    path: tuple[str, ...] = ()
    """Config parameters: the field's path inside the section body."""
    target: str | None = None
    """Expert parameters: the name of the field in ``ControlSettings`` / ``PlanningSettings``."""
    see: str | None = None
    """Optional link into the user guide."""
    model: type[BaseModel] | None = field(default=None, compare=False, repr=False)

    @property
    def name_key(self) -> str:
        return f"param.{self.key}.name"

    @property
    def help_key(self) -> str:
        return f"param.{self.key}.help"

    @property
    def safety(self) -> bool:
        return TAG_SAFETY in self.tags

    @property
    def restart(self) -> bool:
        return TAG_RESTART in self.tags

    def check(self, value: object) -> ParamValue:
        """The value as stored (``int`` for an integral float of an ``int`` parameter, ``float``
        for an ``int`` of a ``float`` parameter) or :class:`ParamError`."""
        if value is None:
            if self.kind == "entity":
                return None
            raise ParamError("param_wrong_type", self.key, expected=self.kind)
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise ParamError("param_wrong_type", self.key, expected="bool")
            return value
        if self.kind in ("int", "float"):
            return self._check_number(value)
        if not isinstance(value, str):
            raise ParamError("param_wrong_type", self.key, expected="string")
        if self.kind == "enum":
            if value not in self.choices:
                raise ParamError("param_not_in_choices", self.key, choices=", ".join(self.choices))
            return value
        if not ENTITY_PATTERN.match(value):
            raise ParamError("param_wrong_type", self.key, expected="entity_id")
        return value

    def _check_number(self, value: object) -> int | float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ParamError("param_wrong_type", self.key, expected=self.kind)
        if isinstance(value, float) and not math.isfinite(value):
            raise ParamError("param_wrong_type", self.key, expected=self.kind)
        number: int | float = value
        if self.kind == "int":
            if isinstance(value, float):
                if not value.is_integer():
                    raise ParamError("param_wrong_type", self.key, expected="int")
                number = int(value)
        else:
            number = float(value)
        lo, hi = self.minimum, self.maximum
        if (lo is not None and number < lo) or (hi is not None and number > hi):
            raise ParamError(
                "param_out_of_range",
                self.key,
                min=0.0 if lo is None else lo,
                max=0.0 if hi is None else hi,
            )
        return number


# --- the controller table ---------------------------------------------------------------

_CONTROLLER: Final[tuple[tuple[str, str, ParamType, tuple[float, float] | None, bool], ...]] = (
    # field, unit, type, (min, max) for the non-safety ones, safety
    ("tick_s", "s", "float", (2.0, 60.0), False),
    ("smoothing_s", "s", "float", (0.0, 900.0), False),
    ("reserve_w", "W", "float", None, True),
    ("deficit_delay_s", "s", "float", None, True),
    ("deficit_budget_wh", "Wh", "float", None, True),
    ("proposal_ttl_s", "s", "float", (10.0, 3600.0), False),
    ("intent_max_age_s", "s", "float", None, True),
    ("inputs_max_age_ticks", "ticks", "int", None, True),
    ("default_hysteresis_w", "W", "float", (0.0, 5000.0), False),
    ("default_hysteresis_delay_s", "s", "float", (0.0, 3600.0), False),
    ("default_settle_s", "s", "float", None, True),
    ("default_max_switches_per_hour", "1/h", "int", None, True),
    ("min_write_interval_s", "s", "float", None, True),
    ("release_deadline_s", "s", "float", None, True),
    ("safe_state_cooldown_s", "s", "float", None, True),
    ("stale_cadence_factor", "x", "float", None, True),
    ("max_jump_w_per_s", "W/s", "float", None, True),
    ("frozen_pv_s", "s", "float", None, True),
    ("frozen_pv_min_w", "W", "float", None, True),
    ("balance_tolerance_w", "W", "float", None, True),
    ("balance_tolerance_pct", "%", "float", None, True),
)
"""``ControlSettings`` fields exposed in expert mode. Every field of ``ControlSettings`` is
either here or in :data:`UNEXPOSED_CONTROL_FIELDS` (a test keeps it so)."""

UNEXPOSED_CONTROL_FIELDS: Final = frozenset({"schema_version"})

# field, unit, type, default, min, max, tags. The defaults equal ``PlanningSettings`` and
# ``Settings`` (``planner_budget_s``, ``planner_mip_gap``); tests/app/test_params_wiring.py
# keeps them in step.
_PLANNER: Final[tuple[tuple[str, str, ParamType, float, float, float, frozenset[str]], ...]] = (
    ("budget_s", "s", "float", 8.0, 0.5, 120.0, frozenset({TAG_EXPERT, TAG_WHATIF})),
    ("mip_gap", "ratio", "float", 0.01, 0.0, 0.5, frozenset({TAG_EXPERT, TAG_WHATIF})),
    ("min_event_gap_s", "s", "float", 60.0, 10.0, 3600.0, frozenset({TAG_EXPERT})),
    ("stale_after_s", "s", "float", 7200.0, 900.0, 86_400.0, frozenset({TAG_EXPERT})),
    ("soc_deviation_pct", "%", "float", 10.0, 1.0, 50.0, frozenset({TAG_EXPERT})),
    ("net_deviation_w", "W", "float", 1500.0, 100.0, 10_000.0, frozenset({TAG_EXPERT})),
    ("net_deviation_s", "s", "float", 1800.0, 300.0, 7200.0, frozenset({TAG_EXPERT})),
)


def _controller_specs() -> list[ParamSpec]:
    specs: list[ParamSpec] = []
    fields = ControlSettings.model_fields
    for name, unit, kind, bounds, safety in _CONTROLLER:
        default = fields[name].default
        lo, hi = SAFETY_LIMITS[name] if safety else (bounds or (None, None))
        specs.append(
            ParamSpec(
                key=f"controller.{name}",
                kind=kind,
                group="controller",
                default=default,
                store="expert",
                unit=unit,
                minimum=lo,
                maximum=hi,
                tags=frozenset({TAG_EXPERT, TAG_SAFETY}) if safety else frozenset({TAG_EXPERT}),
                target=name,
            )
        )
    return specs


def _planner_specs() -> list[ParamSpec]:
    return [
        ParamSpec(
            key=f"planner.{name}",
            kind=kind,
            group="planner",
            default=default,
            store="expert",
            unit=unit,
            minimum=lo,
            maximum=hi,
            tags=tags,
            target=name,
        )
        for name, unit, kind, default, lo, hi, tags in _PLANNER
    ]


def _extra(model: type[BaseModel], name: str) -> dict[str, Any]:
    extra = model.model_fields[name].json_schema_extra
    param = extra.get("param") if isinstance(extra, dict) else None
    return dict(param) if isinstance(param, dict) else {}


def _field_meta(model: type[BaseModel], name: str) -> tuple[Any, float | None, float | None]:
    """Default, ``ge`` and ``le`` of a model field (from its constraint metadata)."""
    info = model.model_fields[name]
    lo = hi = None
    for item in info.metadata:
        lo = getattr(item, "ge", lo)
        hi = getattr(item, "le", hi)
    return info.default, lo, hi


def _config_spec(
    key: str, model: type[BaseModel], kind: ParamType, choices: tuple[str, ...] = ()
) -> ParamSpec:
    """A config parameter; ``key`` is ``<section>.<path to the field>``, ``model`` the model
    that declares the last path element."""
    section, *path = key.split(".")
    name = path[-1]
    meta = _extra(model, name)
    default, lo, hi = _field_meta(model, name)
    return ParamSpec(
        key=key,
        kind=kind,
        group=str(meta.get("group", section)),
        default=default,
        store="config",
        unit=meta.get("unit"),
        minimum=lo,
        maximum=hi,
        choices=choices,
        tags=frozenset(meta.get("tags", [TAG_EXPERT])),
        section=section,
        path=tuple(path),
        see=meta.get("see"),
        model=model,
    )


def _config_specs() -> list[ParamSpec]:
    return [
        _config_spec("tariff.export.remunerate_negative", ExportTariff, "bool"),
        _config_spec("tariff.export.negative_reference_entity", ExportTariff, "entity"),
        _config_spec("forecast.learning.min_history_days", LearningSettings, "int"),
        _config_spec(
            "forecast.learning.load_ml_mode",
            LearningSettings,
            "enum",
            choices=("off", "shadow", "auto"),
        ),
    ]


def _build() -> dict[str, ParamSpec]:
    specs = [*_controller_specs(), *_planner_specs(), *_config_specs()]
    order = {g: i for i, g in enumerate(GROUPS)}
    specs.sort(key=lambda s: (order[s.group], s.key))
    registry = {s.key: s for s in specs}
    assert len(registry) == len(specs), "duplicate parameter key"
    return registry


REGISTRY: Final[Mapping[str, ParamSpec]] = _build()
"""All parameters by key, in display order."""


def spec(key: str) -> ParamSpec:
    try:
        return REGISTRY[key]
    except KeyError:
        raise ParamError("param_unknown", key) from None


def specs_of_group(group: str) -> list[ParamSpec]:
    return [s for s in REGISTRY.values() if s.group == group]


# --- the expert section -----------------------------------------------------------------


def normalise_overrides(overrides: Mapping[str, object]) -> dict[str, bool | int | float | str]:
    """Check every override against the registry; the model validator of ``ExpertConfig``.

    Raises the first problem as a Pydantic custom error (``suncore.param_*``), so a bad
    document is a 422 like any other. A value equal to the default is kept (explicit).
    """
    out: dict[str, bool | int | float | str] = {}
    for key, value in overrides.items():
        try:
            parameter = spec(key)
            if parameter.store != "expert":
                raise ParamError("param_unknown", key)
            checked = parameter.check(value)
        except ParamError as exc:
            raise exc.to_pydantic() from exc
        assert checked is not None
        out[key] = checked
    try:
        check_inputs_window(out)
    except ParamError as exc:
        raise exc.to_pydantic() from exc
    return out


TICK_KEY: Final = "controller.tick_s"
INPUTS_AGE_KEY: Final = "controller.inputs_max_age_ticks"


def check_inputs_window(overrides: Mapping[str, object]) -> None:
    """Both overrides set: ``ticks * tick_s`` may not exceed ``INPUTS_MAX_AGE_LIMIT_S``. With
    the tick from the app option only :func:`apply_controller` can check it (it reduces)."""
    tick, ticks = overrides.get(TICK_KEY), overrides.get(INPUTS_AGE_KEY)
    if not isinstance(tick, int | float) or not isinstance(ticks, int | float):
        return
    if ticks * tick > INPUTS_MAX_AGE_LIMIT_S:
        raise ParamError(
            "param_out_of_range",
            INPUTS_AGE_KEY,
            min=1.0,
            max=float(inputs_max_age_ticks_within(float(tick), int(ticks))),
        )


def sanitise_stored_overrides(
    overrides: Mapping[str, object],
) -> tuple[dict[str, bool | int | float | str], list[str]]:
    """Make a *stored* ``expert`` map loadable after the registry changed (load path only).

    A document written by an older release can name a key that no longer exists or hold a
    value outside a range that was tightened since. Rejecting it would fail the start-up (no
    control stack, no crash recovery that releases devices). Instead: an unknown or
    mistyped key is dropped (= its default), a number outside its range is clamped to the
    nearest bound, which is inside the safety envelope. Returns the map and the keys that
    were changed (the caller logs them). The API and the import keep rejecting such values.
    """
    out: dict[str, bool | int | float | str] = {}
    fixed: list[str] = []
    for key, value in overrides.items():
        parameter = REGISTRY.get(key)
        if parameter is None or parameter.store != "expert":
            fixed.append(key)
            continue
        candidate = _clamped(parameter, value) if _is_number(value) else value
        try:
            checked = parameter.check(candidate)
        except ParamError:
            fixed.append(key)
            continue
        assert checked is not None
        if checked != value or type(checked) is not type(value):
            fixed.append(key)
        out[key] = checked
    tick, ticks = out.get(TICK_KEY), out.get(INPUTS_AGE_KEY)
    if isinstance(tick, int | float) and isinstance(ticks, int) and not isinstance(ticks, bool):
        within = inputs_max_age_ticks_within(float(tick), ticks)
        if within != ticks:
            out[INPUTS_AGE_KEY] = within
            fixed.append(INPUTS_AGE_KEY)
    return out, fixed


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def expert_value(expert: ExpertConfig | None, key: str) -> ParamValue:
    """The effective value of an expert parameter (override, else the registry default)."""
    parameter = spec(key)
    if expert is None:
        return parameter.default
    return expert.overrides.get(key, parameter.default)


def overrides_for(expert: ExpertConfig | None, group: str) -> dict[str, Any]:
    """``{field name: value}`` of the overrides of ``group`` (``controller`` or ``planner``).

    Values are clamped to the hard range once more (defence in depth: the document model
    already rejects anything outside and the store sanitises a stored document at load, this
    protects against a model built without validation).
    """
    if expert is None:
        return {}
    out: dict[str, Any] = {}
    for key, value in expert.overrides.items():
        parameter = REGISTRY.get(key)
        if parameter is None or parameter.group != group or parameter.target is None:
            continue
        out[parameter.target] = _clamped(parameter, value)
    return out


def _clamped(parameter: ParamSpec, value: object) -> object:
    if parameter.kind not in ("int", "float") or not isinstance(value, int | float):
        return value  # not a number: the validation of the result refuses it
    if isinstance(value, bool):
        return value
    lo, hi = parameter.minimum, parameter.maximum
    number = float(value)
    if math.isnan(number):
        return parameter.default  # no nearest bound; the default is inside the range
    if lo is not None:
        number = max(number, lo)
    if hi is not None:
        number = min(number, hi)
    return int(number) if parameter.kind == "int" else number


def apply_controller(base: ControlSettings, expert: ExpertConfig | None) -> ControlSettings:
    """``base`` with the expert overrides of the ``controller`` group (clamped).

    The result is validated as a whole (``model_copy`` would skip the model's own bounds),
    and ``inputs_max_age_ticks`` is reduced so that ``ticks * tick_s`` stays within
    :data:`~suncore.core.control.INPUTS_MAX_AGE_LIMIT_S` also when the tick comes from the
    app option (control-safety review of PR 5-E).
    """
    changes = overrides_for(expert, "controller")
    if not changes:
        return base
    merged = ControlSettings.model_validate({**base.model_dump(), **changes})
    if INPUTS_AGE_KEY.removeprefix("controller.") not in changes:
        return merged
    ticks = inputs_max_age_ticks_within(merged.tick_s, merged.inputs_max_age_ticks)
    if ticks == merged.inputs_max_age_ticks:
        return merged
    return merged.model_copy(update={"inputs_max_age_ticks": ticks})


# --- reading and writing the values -----------------------------------------------------


def _walk(body: Mapping[str, Any], path: tuple[str, ...]) -> object:
    node: object = body
    for part in path:
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def current_value(parameter: ParamSpec, sections: Mapping[str, BaseModel | None]) -> ParamValue:
    """The effective value; the default while the owning section is missing."""
    if parameter.store == "expert":
        expert = sections.get("expert")
        assert expert is None or isinstance(expert, ExpertConfig)
        return expert_value(expert, parameter.key)
    assert parameter.section is not None
    model = sections.get(parameter.section)
    if model is None:
        return parameter.default
    raw = _walk(model.model_dump(mode="json", by_alias=True), parameter.path)
    if parameter.kind == "entity":
        return raw.get("entity_id") if isinstance(raw, Mapping) else None
    return raw if raw is not None else parameter.default  # type: ignore[return-value]


def section_available(parameter: ParamSpec, sections: Mapping[str, BaseModel | None]) -> bool:
    """Whether the parameter can be changed now (its config section exists)."""
    return parameter.store == "expert" or sections.get(parameter.section or "") is not None


def sections_needed() -> tuple[str, ...]:
    """The section names a parameter listing reads."""
    return ("expert", *sorted({s.section for s in REGISTRY.values() if s.section}))


@dataclass(frozen=True, slots=True)
class ParamChange:
    """What a write does to one section."""

    section: str
    model: BaseModel


def _set_path(body: dict[str, Any], path: tuple[str, ...], value: object) -> None:
    node = body
    for part in path[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[path[-1]] = value


def plan_write(
    changes: Mapping[str, object],
    sections: Mapping[str, BaseModel | None],
) -> list[ParamChange]:
    """Apply ``changes`` (``key -> value``, ``None`` only for ``entity``) to the sections.

    Pure and all-or-nothing: every value is checked against its spec first; raises
    :class:`ParamError` (``param_*``) or :class:`SectionMissingError`. The result holds one
    new, validated model per touched section. A value equal to the default of an expert
    parameter removes the override (the document stays sparse).
    """
    checked: dict[str, ParamValue] = {}
    for key, value in changes.items():
        checked[key] = spec(key).check(value)
    expert_now = sections.get("expert")
    assert expert_now is None or isinstance(expert_now, ExpertConfig)
    overrides = dict(expert_now.overrides) if expert_now is not None else {}
    bodies: dict[str, dict[str, Any]] = {}
    touched_expert = False
    for key, value in checked.items():
        parameter = REGISTRY[key]
        if parameter.store == "expert":
            touched_expert = True
            if value == parameter.default:
                overrides.pop(key, None)
            else:
                assert value is not None
                overrides[key] = value
            continue
        assert parameter.section is not None
        model = sections.get(parameter.section)
        if model is None:
            raise SectionMissingError(key, parameter.section)
        body = bodies.setdefault(parameter.section, model.model_dump(mode="json", by_alias=True))
        stored: object = (
            {"entity_id": value, "invert_sign": False}
            if parameter.kind == "entity" and value is not None
            else value
        )
        _set_path(body, parameter.path, stored)
    result: list[ParamChange] = []
    for name, body in bodies.items():
        model = sections[name]
        assert model is not None
        try:
            result.append(ParamChange(name, type(model).model_validate(body)))
        except ValidationError as exc:
            culprit = next(k for k in checked if REGISTRY[k].section == name)
            raise ParamError("param_invalid", culprit) from exc
    if touched_expert:
        check_inputs_window(overrides)  # ParamError (422), not a ValidationError (500)
        result.append(ParamChange("expert", ExpertConfig(overrides=overrides)))
    return result


class SectionMissingError(LookupError):
    """A config parameter's section was never stored (nothing to change yet)."""

    def __init__(self, key: str, section: str) -> None:
        super().__init__(f"{key}: section {section!r} is not configured")
        self.key = key
        self.section = section


def reset_keys(*, key: str | None = None, group: str | None = None) -> list[str]:
    """The keys a reset touches: one key, or all of a group; ``ParamError`` if unknown."""
    if (key is None) == (group is None):
        raise ValueError("exactly one of key and group")
    if key is not None:
        return [spec(key).key]
    if group not in GROUPS:
        raise ParamError("param_unknown", str(group))
    return [s.key for s in specs_of_group(str(group))]


def safety_keys_changed(
    keys: Iterable[str], before: Mapping[str, ParamValue], after: Mapping[str, ParamValue]
) -> list[str]:
    """The ``safety``-tagged keys among ``keys`` whose value differs between the two maps."""
    return [k for k in keys if REGISTRY[k].safety and before.get(k) != after.get(k)]


def expert_safety_diff(old: ExpertConfig | None, new: ExpertConfig) -> list[str]:
    """Safety-tagged keys whose effective value differs between two ``expert`` documents."""
    keys = [k for k, s in REGISTRY.items() if s.store == "expert" and s.safety]
    return [k for k in keys if expert_value(old, k) != expert_value(new, k)]


__all__ = [
    "GROUPS",
    "KNOWN_TAGS",
    "REGISTRY",
    "ParamChange",
    "ParamError",
    "ParamSpec",
    "ParamValue",
    "SectionMissingError",
    "apply_controller",
    "check_inputs_window",
    "current_value",
    "expert_safety_diff",
    "expert_value",
    "normalise_overrides",
    "overrides_for",
    "plan_write",
    "reset_keys",
    "safety_keys_changed",
    "sanitise_stored_overrides",
    "section_available",
    "sections_needed",
    "spec",
    "specs_of_group",
]
