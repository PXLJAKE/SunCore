# SPDX-License-Identifier: Apache-2.0
"""Template schema v1 (ADR-0006): Pydantic models for the four template kinds.

Templates are declarative YAML. They never contain entity ids: entities are found
through role matchers (domain, platform, device class, units, translation key, state
class) and mapped by the user in the wizard. Facts that could not be verified against
an integration source or vendor document are listed in ``unverified``.

The JSON Schemas in ``templates/schema/`` are generated from these models
(``scripts/gen_schemas.py``).
"""

from __future__ import annotations

import itertools
from datetime import date
from enum import StrEnum, unique
from typing import Annotated, Final, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

from suncore.core.decisions import REASON_KEY_PATTERN
from suncore.core.devices import (
    TEMPLATE_ID_PATTERN,
    ComfortBand,
    ControlKind,
    DeviceKind,
    PowerModelKind,
    RoleName,
)

SCHEMA_VERSION: Final = 1
REGION_ID_PATTERN: Final = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$"
"""Region ids may be a single segment (``de``, ``generic``) or ``<country>.<variant>``."""

TemplateKind = Literal["device", "tariff", "forecast", "region"]
TEMPLATE_KINDS: Final[tuple[TemplateKind, ...]] = ("device", "tariff", "forecast", "region")

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=48)]
MessageKey = Annotated[str, StringConstraints(pattern=REASON_KEY_PATTERN, max_length=120)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Url = Annotated[str, StringConstraints(pattern=r"^https?://\S+$", max_length=500)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceRef(_Frozen):
    """Documentation a template (or one of its facts) is based on."""

    title: Text
    url: Url
    date: date
    """Date the source was read."""


class TestedWith(_Frozen):
    """Integration version the template was verified against."""

    # Not a pytest test class.
    __test__ = False

    integration: Text
    version: Text


class EntityMatcher(_Frozen):
    """Role matcher; every given criterion must match, ``domain`` is mandatory.

    ``units`` lists acceptable ``unit_of_measurement`` strings (exact, case-sensitive).
    """

    domain: Annotated[str, StringConstraints(pattern=r"^[a-z_]+$")]
    platform: str | None = None
    device_class: str | None = None
    units: Annotated[list[Text], Field(min_length=1, max_length=8)] | None = None
    translation_key: str | None = None
    state_class: str | None = None


class RoleSpec(_Frozen):
    """One entity role of a template."""

    required: bool = True
    matcher: EntityMatcher
    description: MessageKey
    """i18n key, e.g. ``template.lg.thinq_climate.role.energy_today``."""


class TemplateHeader(_Frozen):
    """Fields common to all template kinds."""

    id: Annotated[str, StringConstraints(pattern=REGION_ID_PATTERN, max_length=64)]
    schema_version: Literal[1]
    name: MessageKey
    source: Annotated[list[SourceRef], Field(min_length=1)]
    tested_with: list[TestedWith]
    capabilities: Annotated[list[Slug], Field(max_length=32)]
    unverified: list[Text] = Field(default_factory=list)

    @model_validator(mode="after")
    def _header(self) -> Self:
        if self.name != f"template.{self.id}.name":
            msg = f"name: must be the i18n key `template.{self.id}.name`, got `{self.name}`"
            raise ValueError(msg)
        if len(set(self.capabilities)) != len(self.capabilities):
            msg = "capabilities: entries must be unique"
            raise ValueError(msg)
        if not self.tested_with and not self.unverified:
            msg = "tested_with is empty: list what is unverified in `unverified`"
            raise ValueError(msg)
        return self

    def message_keys(self) -> list[str]:
        """i18n keys this template needs in the catalogues."""
        return [self.name]


def _check_roles(roles: dict[RoleName, RoleSpec]) -> None:
    if not roles:
        msg = "roles: at least one role is required"
        raise ValueError(msg)


# --- device ------------------------------------------------------------------


class PowerModelDefault(_Frozen):
    """Default power model; ``power_role``/``energy_role`` refer to roles of the template."""

    kind: PowerModelKind
    power_role: RoleName | None = None
    energy_role: RoleName | None = None
    nominal_w: Annotated[float, Field(gt=0, le=1_000_000)] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.kind is PowerModelKind.MEASURED and self.power_role is None:
            msg = "defaults.power_model: kind `measured` needs `power_role`"
            raise ValueError(msg)
        if self.kind is PowerModelKind.COUNTER_DERIVED and self.energy_role is None:
            msg = "defaults.power_model: kind `counter_derived` needs `energy_role`"
            raise ValueError(msg)
        if self.kind in (PowerModelKind.NOMINAL, PowerModelKind.LEARNED) and self.nominal_w is None:
            msg = f"defaults.power_model: kind `{self.kind.value}` needs `nominal_w`"
            raise ValueError(msg)
        return self


class DeviceDefaults(_Frozen):
    """Defaults copied into a new :class:`~suncore.core.devices.DeviceConfig`."""

    power_model: PowerModelDefault
    control: ControlKind
    min_run_s: Annotated[int, Field(ge=0, le=86_400)] = 0
    min_pause_s: Annotated[int, Field(ge=0, le=86_400)] = 0
    max_switches_per_hour: Annotated[int, Field(ge=1, le=3600)] | None = None
    comfort: ComfortBand | None = None


class DeviceTemplate(TemplateHeader):
    kind: Literal["device"]
    device_kind: DeviceKind
    roles: dict[RoleName, RoleSpec]
    defaults: DeviceDefaults

    @model_validator(mode="after")
    def _device(self) -> Self:
        _check_roles(self.roles)
        if "." not in self.id:
            msg = "id: must be `<vendor>.<model>`"
            raise ValueError(msg)
        pm = self.defaults.power_model
        for field, role in (("power_role", pm.power_role), ("energy_role", pm.energy_role)):
            if role is not None and role not in self.roles:
                msg = f"defaults.power_model.{field}: unknown role `{role}`"
                raise ValueError(msg)
        if self.defaults.comfort is not None and self.device_kind.value not in (
            "climate",
            "water_heater",
            "heat_pump",
        ):
            msg = f"defaults.comfort: not applicable to device_kind `{self.device_kind.value}`"
            raise ValueError(msg)
        return self

    def message_keys(self) -> list[str]:
        return [self.name, *(r.description for r in self.roles.values())]


# --- tariff ------------------------------------------------------------------


class TariffTemplate(TemplateHeader):
    kind: Literal["tariff"]
    direction: Literal["import", "export"]
    tariff_type: Literal["fixed", "dynamic"]
    roles: dict[RoleName, RoleSpec] = Field(default_factory=dict)
    price_unit: Annotated[str, StringConstraints(pattern=r"^(\{currency\}|[A-Z]{3})/kWh$")]
    """``<currency>/kWh``; ``{currency}`` takes the region's currency."""
    resolution_min: Literal[15, 60]

    @model_validator(mode="after")
    def _tariff(self) -> Self:
        if "." not in self.id:
            msg = "id: must be `<vendor>.<model>`"
            raise ValueError(msg)
        if self.tariff_type == "dynamic" and "price" not in self.roles:
            msg = "roles: a dynamic tariff needs a `price` role"
            raise ValueError(msg)
        return self

    def message_keys(self) -> list[str]:
        return [self.name, *(r.description for r in self.roles.values())]


# --- forecast ----------------------------------------------------------------


class ForecastAttributeMap(_Frozen):
    """Where quantiles and timestamps live in the entity attributes.

    ``series_attribute`` names the attribute holding the list of points; each point
    is a mapping with the given field names. ``None`` quantile fields are not offered.
    """

    series_attribute: Text
    timestamp_field: Text
    p50_field: Text
    p10_field: Text | None = None
    p90_field: Text | None = None
    value_unit: Text
    """Unit of the values, as in the source (e.g. ``kW``, ``Wh``)."""


class ForecastTemplate(TemplateHeader):
    kind: Literal["forecast"]
    forecast_kind: Literal["pv", "load", "price"]
    roles: dict[RoleName, RoleSpec]
    attributes: ForecastAttributeMap
    resolution_min: Annotated[int, Field(ge=1, le=1440)]

    @model_validator(mode="after")
    def _forecast(self) -> Self:
        _check_roles(self.roles)
        if "." not in self.id:
            msg = "id: must be `<vendor>.<model>`"
            raise ValueError(msg)
        return self

    def message_keys(self) -> list[str]:
        return [self.name, *(r.description for r in self.roles.values())]


# --- region ------------------------------------------------------------------


@unique
class NegativePriceRule(StrEnum):
    """Feed-in remuneration during negative spot prices."""

    NONE = "none"
    """No special rule."""
    NO_REMUNERATION = "no_remuneration"
    """No remuneration while the price is negative (e.g. DE, new plants)."""
    CURTAILMENT_REQUIRED = "curtailment_required"
    """Export must be curtailed while the price is negative."""


class FeedInTier(_Frozen):
    """Feed-in remuneration band, data only (no legal advice).

    Bands apply to the share of the plant up to ``up_to_kwp`` (cumulative, lower bounds
    are the previous band); ``None`` = no upper bound and only allowed on the last band.
    """

    up_to_kwp: Annotated[float, Field(gt=0)] | None = None
    rate_per_kwh: Annotated[float, Field(ge=0)]
    """In the region's currency per kWh."""
    mode: Literal["surplus", "full_feed_in"] = "surplus"
    valid_from: date | None = None
    valid_until: date | None = None

    @model_validator(mode="after")
    def _dates(self) -> Self:
        if self.valid_from and self.valid_until and self.valid_from > self.valid_until:
            msg = "feed_in_tiers: valid_from is after valid_until"
            raise ValueError(msg)
        return self


class RegionTemplate(TemplateHeader):
    kind: Literal["region"]
    country: Annotated[str, StringConstraints(pattern=r"^([A-Z]{2}|generic)$")]
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    timezone: (
        Annotated[str, StringConstraints(pattern=r"^(UTC|[A-Za-z_]+(/[A-Za-z_+\-0-9]+){1,2})$")]
        | None
    ) = None
    negative_price_rule: NegativePriceRule = NegativePriceRule.NONE
    feed_in_tiers: list[FeedInTier] = Field(default_factory=list)

    @model_validator(mode="after")
    def _region(self) -> Self:
        bounds = [t.up_to_kwp for t in self.feed_in_tiers]
        for i, bound in enumerate(bounds):
            if bound is None and i != len(bounds) - 1:
                msg = "feed_in_tiers: only the last tier may be unbounded"
                raise ValueError(msg)
        finite = [b for b in bounds if b is not None]
        if any(a >= b for a, b in itertools.pairwise(finite)):
            msg = "feed_in_tiers: `up_to_kwp` must be strictly increasing"
            raise ValueError(msg)
        return self


Template = Annotated[
    DeviceTemplate | TariffTemplate | ForecastTemplate | RegionTemplate,
    Field(discriminator="kind"),
]
"""Discriminated union on ``kind``."""

TEMPLATE_ADAPTER: Final[
    TypeAdapter[DeviceTemplate | TariffTemplate | ForecastTemplate | RegionTemplate]
] = TypeAdapter(Template)

KIND_MODELS: Final[dict[str, type[BaseModel]]] = {
    "device": DeviceTemplate,
    "tariff": TariffTemplate,
    "forecast": ForecastTemplate,
    "region": RegionTemplate,
}

__all__ = [
    "KIND_MODELS",
    "REGION_ID_PATTERN",
    "SCHEMA_VERSION",
    "TEMPLATE_ADAPTER",
    "TEMPLATE_ID_PATTERN",
    "TEMPLATE_KINDS",
    "DeviceDefaults",
    "DeviceTemplate",
    "EntityMatcher",
    "FeedInTier",
    "ForecastAttributeMap",
    "ForecastTemplate",
    "NegativePriceRule",
    "PowerModelDefault",
    "RegionTemplate",
    "RoleSpec",
    "SourceRef",
    "TariffTemplate",
    "Template",
    "TemplateHeader",
    "TemplateKind",
    "TestedWith",
]
