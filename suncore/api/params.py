# SPDX-License-Identifier: Apache-2.0
"""``/api/v1/params``: the parameter registry and the expert-mode write path (Phase 5 WP5).

``GET`` lists every parameter with its spec, default, current value and whether it is modified;
``PUT`` changes values (validated against the registry, all-or-nothing; a ``safety``-tagged
value that changes needs ``confirm_safety: true``); ``POST /params/reset`` restores defaults of
one key or a group. Values outside a parameter's hard range are always rejected, with or without
confirmation. Everything is stored through the normal section path (``Services.put_section``),
so it lands in the configuration history and takes effect through the owning service's own
rebuild (the control stack hands over like after a device edit). Nothing here touches a device,
a device mode or a set-point.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, Literal

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field

from suncore.api.models import ErrorOut
from suncore.api.services import Services
from suncore.core.expert import ExpertConfig
from suncore.core.params import (
    GROUPS,
    REGISTRY,
    ParamChange,
    ParamError,
    ParamSpec,
    ParamValue,
    SectionMissingError,
    current_value,
    expert_safety_diff,
    plan_write,
    reset_keys,
    section_available,
    sections_needed,
)

API_PREFIX: Final = "/api/v1"
ERR_UNKNOWN_PARAM: Final = "api.error.unknown_param"
ERR_PARAM_SECTION_MISSING: Final = "api.error.param_section_missing"
PARAMS_MAX_BYTES: Final = 16 * 1024


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParamOut(_Model):
    """One parameter: spec, default, current value."""

    key: str
    type: Literal["float", "int", "bool", "enum", "entity"]
    group: str
    unit: str | None
    tags: list[str]
    default: ParamValue
    value: ParamValue
    modified: bool
    minimum: float | None
    maximum: float | None
    choices: list[str]
    name_key: str
    help_key: str
    see: str | None
    restart: bool
    """Takes effect only after a restart of the app (none of the current parameters does)."""
    safety: bool
    """A change needs ``confirm_safety``; the range is the hard bound of the safety layer."""
    available: bool
    """``False`` while the configuration section that holds the parameter does not exist yet."""


class ParamsOut(_Model):
    groups: list[str]
    params: list[ParamOut]


class ParamsApplied(ParamsOut):
    changed: list[str]
    """The keys whose value changed."""


class ParamsPut(_Model):
    changes: dict[str, ParamValue] = Field(min_length=1, max_length=100)
    confirm_safety: bool = False


class ParamsReset(_Model):
    """Exactly one of ``key`` and ``group``."""

    key: str | None = None
    group: str | None = None


SectionMap = Mapping[str, BaseModel | None]


async def _load(services: Services) -> dict[str, BaseModel | None]:
    return {name: await services.get_section(name) for name in sections_needed()}


def _out(parameter: ParamSpec, sections: SectionMap) -> ParamOut:
    value = current_value(parameter, sections)
    return ParamOut(
        key=parameter.key,
        type=parameter.kind,
        group=parameter.group,
        unit=parameter.unit,
        tags=sorted(parameter.tags),
        default=parameter.default,
        value=value,
        modified=value != parameter.default,
        minimum=parameter.minimum,
        maximum=parameter.maximum,
        choices=list(parameter.choices),
        name_key=parameter.name_key,
        help_key=parameter.help_key,
        see=parameter.see,
        restart=parameter.restart,
        safety=parameter.safety,
        available=section_available(parameter, sections),
    )


def _listing(sections: SectionMap) -> dict[str, Any]:
    return {
        "groups": list(GROUPS),
        "params": [_out(p, sections) for p in REGISTRY.values()],
    }


def _invalid(*problems: tuple[str, str, dict[str, Any]]) -> RequestValidationError:
    """A 422 like any other validation error: ``(key, error type, context)`` per problem."""
    return RequestValidationError(
        [
            {
                "type": f"suncore.{code}",
                "loc": ("body", "changes", key),
                "msg": code,
                "ctx": ctx,
            }
            for key, code, ctx in problems
        ]
    )


def _from_param_error(exc: ParamError) -> RequestValidationError:
    return _invalid((exc.key, exc.code, {"param": exc.key, **exc.context}))


def _apply_to(sections: SectionMap, plan: list[ParamChange]) -> dict[str, BaseModel | None]:
    after = dict(sections)
    for change in plan:
        after[change.section] = change.model
    return after


async def _store(services: Services, sections: SectionMap, plan: list[ParamChange]) -> None:
    """Put the changed sections (unchanged ones are skipped); ``expert`` goes last."""
    for change in sorted(plan, key=lambda c: c.section == "expert"):
        if sections.get(change.section) != change.model:
            await services.put_section(change.section, change.model)


def _changed(before: SectionMap, after: SectionMap, keys: list[str]) -> list[str]:
    return [
        k for k in keys if current_value(REGISTRY[k], before) != current_value(REGISTRY[k], after)
    ]


def guard_expert_document(old: BaseModel | None, new: BaseModel) -> None:
    """``PUT /config/expert`` may not change a ``safety`` value: that needs ``/params`` with
    ``confirm_safety`` (the document model still rejects anything outside the hard range)."""
    assert isinstance(new, ExpertConfig)
    assert old is None or isinstance(old, ExpertConfig)
    keys = expert_safety_diff(old, new)
    if keys:
        raise _invalid(*((k, "param_confirm_required", {"param": k}) for k in keys))


def register_params(app: FastAPI, services: Services) -> None:
    """The three routes."""

    @app.get(
        f"{API_PREFIX}/params",
        response_model=ParamsOut,
        operation_id="get_params",
    )
    async def get_params() -> dict[str, Any]:
        """Every tunable parameter: type, unit, hard range, tags (``safety``: confirmation
        needed, ``restart``), the catalogue keys of its name and help text, the default, the
        current value and whether it differs from the default."""
        return _listing(await _load(services))

    @app.put(
        f"{API_PREFIX}/params",
        response_model=ParamsApplied,
        operation_id="put_params",
        responses={
            409: {"model": ErrorOut, "description": "A parameter's section is not configured"},
            413: {"model": ErrorOut, "description": "Body too large"},
        },
    )
    async def put_params(body: ParamsPut) -> dict[str, Any]:
        """Change parameters, all or nothing. A value outside its hard range, of the wrong type
        or for an unknown key is a 422 (nothing is stored). Changing a ``safety``-tagged value
        needs ``confirm_safety: true`` (422 otherwise); the range is never widened by it."""
        sections = await _load(services)
        try:
            plan = plan_write(body.changes, sections)
        except ParamError as exc:
            raise _from_param_error(exc) from exc
        except SectionMissingError as exc:
            raise HTTPException(status_code=409, detail=ERR_PARAM_SECTION_MISSING) from exc
        after = _apply_to(sections, plan)
        changed = _changed(sections, after, list(body.changes))
        unconfirmed = [k for k in changed if REGISTRY[k].safety]
        if unconfirmed and not body.confirm_safety:
            raise _invalid(*((k, "param_confirm_required", {"param": k}) for k in unconfirmed))
        await _store(services, sections, plan)
        return {**_listing(after), "changed": changed}

    @app.post(
        f"{API_PREFIX}/params/reset",
        response_model=ParamsApplied,
        operation_id="reset_params",
        responses={404: {"model": ErrorOut, "description": "Unknown key or group"}},
    )
    async def reset_params(body: ParamsReset) -> dict[str, Any]:
        """Restore the default of one ``key`` or of every parameter of a ``group``. Needs no
        confirmation: a default is inside the hard range by construction."""
        if (body.key is None) == (body.group is None):
            raise RequestValidationError(
                [
                    {
                        "type": "suncore.param_reset_target",
                        "loc": ("body",),
                        "msg": "key xor group",
                        "ctx": {},
                    }
                ]
            )
        try:
            keys = reset_keys(key=body.key, group=body.group)
        except ParamError as exc:
            raise HTTPException(status_code=404, detail=ERR_UNKNOWN_PARAM) from exc
        sections = await _load(services)
        # A config parameter whose section does not exist is at its default already.
        keys = [k for k in keys if section_available(REGISTRY[k], sections)]
        plan = plan_write({k: REGISTRY[k].default for k in keys}, sections) if keys else []
        after = _apply_to(sections, plan)
        changed = _changed(sections, after, keys)
        await _store(services, sections, plan)
        return {**_listing(after), "changed": changed}
