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
import re
from datetime import date
from enum import StrEnum, unique
from functools import lru_cache
from typing import Annotated, Final, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

from suncore.core.actions import ERR_ACTION_NOT_ALLOWED, is_read_only_action
from suncore.core.capabilities import Capability
from suncore.core.config import REGION_ID_PATTERN
from suncore.core.decisions import REASON_KEY_PATTERN
from suncore.core.devices import (
    TEMPLATE_ID_PATTERN,
    THERMAL_KINDS,
    ComfortBand,
    ContinuousControl,
    ControlSpec,
    DeviceKind,
    OnOffControl,
    PowerModelKind,
    RelaysControl,
    RoleName,
    SetpointControl,
    StepsControl,
    control_roles,
)
from suncore.core.units import Unit, parse_unit

SCHEMA_VERSION: Final = 1
VERSION_PATTERN: Final = r"^\d+(\.\d+)*((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?$"
"""PEP 440 subset accepted for ``min_app_version`` (no epochs, no local versions)."""

TemplateKind = Literal["device", "tariff", "forecast", "region", "battery"]
TEMPLATE_KINDS: Final[tuple[TemplateKind, ...]] = (
    "device",
    "tariff",
    "forecast",
    "region",
    "battery",
)

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=48)]
MessageKey = Annotated[str, StringConstraints(pattern=REASON_KEY_PATTERN, max_length=120)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Url = Annotated[str, StringConstraints(pattern=r"^https?://\S+$", max_length=500)]
DottedPath = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$", max_length=200)
]
RegexText = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


@lru_cache(maxsize=512)
def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile a template regex once (cached by pattern text, cheap on the Pi)."""
    return re.compile(pattern)


def _valid_regex(name: str, pattern: str | None) -> None:
    if pattern is None:
        return
    try:
        compile_pattern(pattern)
    except re.error as exc:
        msg = f"{name}: invalid regular expression ({exc})"
        raise ValueError(msg) from exc


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

    ``units`` lists acceptable ``unit_of_measurement`` strings (exact, case-sensitive);
    ``unit_pattern`` is a regular expression matched with ``re.fullmatch`` on the stripped
    unit. ``translation_key`` / ``translation_key_pattern`` likewise. Exact and pattern
    forms exclude each other.
    """

    domain: Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+$")]
    platforms: Annotated[list[Slug], Field(min_length=1, max_length=8)] | None = None
    device_class: Annotated[str, StringConstraints(max_length=64)] | None = None
    units: Annotated[list[Text], Field(min_length=1, max_length=8)] | None = None
    unit_pattern: RegexText | None = None
    translation_key: Annotated[str, StringConstraints(max_length=128)] | None = None
    translation_key_pattern: RegexText | None = None
    state_class: str | None = None

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.translation_key is not None and self.translation_key_pattern is not None:
            msg = "matcher: `translation_key` and `translation_key_pattern` exclude each other"
            raise ValueError(msg)
        if self.units is not None and self.unit_pattern is not None:
            msg = "matcher: `units` and `unit_pattern` exclude each other"
            raise ValueError(msg)
        _valid_regex("matcher.translation_key_pattern", self.translation_key_pattern)
        _valid_regex("matcher.unit_pattern", self.unit_pattern)
        return self


class RoleSpec(_Frozen):
    """One entity role of a template."""

    required: bool = True
    matcher: EntityMatcher
    description: MessageKey
    """i18n key, e.g. ``template.lg.thinq_climate.role.energy_today``."""
    same_device_as: RoleName | None = None
    """Candidates must belong to the same registry device as the entity of that role."""


class TemplateHeader(_Frozen):
    """Fields common to all template kinds."""

    id: Annotated[str, StringConstraints(pattern=REGION_ID_PATTERN, max_length=64)]
    schema_version: Literal[1]
    min_app_version: Annotated[str, StringConstraints(pattern=VERSION_PATTERN)] | None = None
    """Needs SunCore at least this version (PEP 440 subset); ``None`` = any."""
    name: MessageKey
    source: Annotated[list[SourceRef], Field(min_length=1)]
    tested_with: list[TestedWith]
    capabilities: Annotated[list[Capability], Field(max_length=32)]
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


def _check_roles(roles: dict[RoleName, RoleSpec], *, required: bool = True) -> None:
    if required and not roles:
        msg = "roles: at least one role is required"
        raise ValueError(msg)
    for name, spec in roles.items():
        target = spec.same_device_as
        if target is None:
            continue
        if target == name:
            msg = f"roles.{name}.same_device_as: a role cannot refer to itself"
            raise ValueError(msg)
        if target not in roles:
            msg = f"roles.{name}.same_device_as: unknown role `{target}`"
            raise ValueError(msg)
        if roles[target].same_device_as is not None:
            msg = (
                f"roles.{name}.same_device_as: `{target}` itself refers to another role (no chains)"
            )
            raise ValueError(msg)


def _need(caps: list[Capability], cap: Capability, ok: bool, why: str) -> None:
    if cap in caps and not ok:
        msg = f"capabilities: `{cap.value}` declared but {why}"
        raise ValueError(msg)


# --- series sources ----------------------------------------------------------


class SeriesFields(_Frozen):
    """Names of the fields of one series point and the unit of its values.

    ``timestamp_field`` and ``value_field`` are required for a list of point objects
    (``action`` series, ``attribute`` series of shape ``list``) and absent for an
    ``attribute`` series of shape ``mapping``, whose keys are the timestamps and whose
    values are the values.
    """

    timestamp_field: Text | None = None
    value_field: Text | None = None
    p10_field: Text | None = None
    p90_field: Text | None = None
    value_unit: Text

    def require_point_fields(self, where: str) -> None:
        if self.timestamp_field is None or self.value_field is None:
            msg = f"{where}: `timestamp_field` and `value_field` are required"
            raise ValueError(msg)


class AttributeSeries(_Frozen):
    """Series held in an attribute of the entities of ``roles`` (concatenated in order).

    ``list_path`` (Phase 4, generic attribute adapter): dotted path from the attribute's
    value to the points when the attribute is a mapping (``None`` = the attribute itself
    holds the points). ``shape`` says what the points are:

    * ``list`` (default): a list of objects named by ``fields`` (Solcast
      ``detailedForecast``);
    * ``mapping``: an object ``{iso_timestamp: value}`` (Forecast.Solar ``wh_hours``,
      Open-Meteo Solar Forecast ``wh_period_15m``); ``fields`` then gives only
      ``value_unit`` - no point field names and no quantiles.

    The period length of a point is the template's ``resolution_min``.
    """

    source: Literal["attribute"]
    roles: Annotated[tuple[RoleName, ...], Field(min_length=1, max_length=16)]
    attribute: Text
    fields: SeriesFields
    list_path: DottedPath | None = None
    shape: Literal["list", "mapping"] = "list"

    @model_validator(mode="after")
    def _unique(self) -> Self:
        if len(set(self.roles)) != len(self.roles):
            msg = "series.roles: entries must be unique"
            raise ValueError(msg)
        f = self.fields
        if self.shape == "list":
            f.require_point_fields("series.fields")
        elif any(
            x is not None for x in (f.timestamp_field, f.value_field, f.p10_field, f.p90_field)
        ):
            msg = "series.fields: shape `mapping` takes only `value_unit` (keys are the timestamps)"
            raise ValueError(msg)
        return self


class ActionSeries(_Frozen):
    """Series returned by a read-only action (``return_response`` is implied)."""

    source: Literal["action"]
    domain: Slug
    service: Slug
    start_field: Text | None = None
    end_field: Text | None = None
    response_path: Annotated[
        str, StringConstraints(pattern=r"^[A-Za-z0-9_]+(\.([A-Za-z0-9_]+|\*))*$", max_length=200)
    ]
    """Dotted path into the response; a ``*`` segment means every value of that mapping."""
    fields: SeriesFields
    max_horizon_h: Annotated[int, Field(ge=1, le=336)]

    @model_validator(mode="after")
    def _allowed(self) -> Self:
        self.fields.require_point_fields("series.fields")
        if not is_read_only_action(self.domain, self.service):
            msg = (
                f"{ERR_ACTION_NOT_ALLOWED}: `{self.domain}.{self.service}` is not on the "
                "read-only action allowlist"
            )
            raise ValueError(msg)
        return self


SeriesSource = Annotated[AttributeSeries | ActionSeries, Field(discriminator="source")]


class EnergyDashboardSeries(_Frozen):
    """Series read from the WebSocket command ``energy/solar_forecast`` (Phase 4, forecasts only).

    The command returns ``{config_entry_id: {"wh_hours": {iso_timestamp: Wh}}}`` for the forecast
    integrations linked in the Energy dashboard (Forecast.Solar in Home Assistant core). The
    template names no entity: the user binds the source to a config entry
    (``ForecastSourceConfig.config_entry_id``), or SunCore takes the only entry there is.
    ``fields`` gives only ``value_unit`` (energy per period; no quantiles). The length of a
    period is inferred from the spacing of the timestamps; the template's ``resolution_min``
    is the expected length (used for a series of one point).
    """

    source: Literal["energy_solar_forecast"]
    fields: SeriesFields
    missing_is_zero: bool = False
    """The integration drops periods of value 0 (Forecast.Solar: every zero except at midnight),
    so a period missing between two known ones is 0 Wh, not unknown."""

    @model_validator(mode="after")
    def _fields(self) -> Self:
        f = self.fields
        if any(x is not None for x in (f.timestamp_field, f.value_field, f.p10_field, f.p90_field)):
            msg = "series.fields: `energy_solar_forecast` takes only `value_unit`"
            raise ValueError(msg)
        return self


ForecastSeriesSource = Annotated[
    AttributeSeries | ActionSeries | EnergyDashboardSeries, Field(discriminator="source")
]


def _check_series_roles(
    series: AttributeSeries | ActionSeries | EnergyDashboardSeries,
    roles: dict[RoleName, RoleSpec],
) -> None:
    if isinstance(series, AttributeSeries):
        for role in series.roles:
            if role not in roles:
                msg = f"series.roles: unknown role `{role}`"
                raise ValueError(msg)


# --- device ------------------------------------------------------------------


class PowerModelDefault(_Frozen):
    """Default power model; ``power_role``/``energy_role`` refer to roles of the template.

    ``nominal_w`` is only a hint: a template cannot know the user's model, the wizard asks
    (the instance-level :class:`~suncore.core.devices.PowerModel` stays strict).
    """

    kind: PowerModelKind
    power_role: RoleName | None = None
    energy_role: RoleName | None = None
    nominal_w: Annotated[float, Field(gt=0, le=1_000_000)] | None = None
    counter_update_s: Annotated[int, Field(ge=1, le=86_400)] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.kind is PowerModelKind.MEASURED and self.power_role is None:
            msg = "defaults.power_model: kind `measured` needs `power_role`"
            raise ValueError(msg)
        if self.kind is PowerModelKind.COUNTER_DERIVED and self.energy_role is None:
            msg = "defaults.power_model: kind `counter_derived` needs `energy_role`"
            raise ValueError(msg)
        if self.counter_update_s is not None and self.energy_role is None:
            msg = "defaults.power_model: `counter_update_s` without `energy_role`"
            raise ValueError(msg)
        return self


class DeviceDefaults(_Frozen):
    """Defaults copied into a new :class:`~suncore.core.devices.DeviceConfig`."""

    power_model: PowerModelDefault
    control: ControlSpec
    min_run_s: Annotated[int, Field(ge=0, le=86_400)] = 0
    min_pause_s: Annotated[int, Field(ge=0, le=86_400)] = 0
    max_switches_per_hour: Annotated[int, Field(ge=1, le=3600)] | None = None
    hysteresis_w: Annotated[float, Field(ge=0, le=1_000_000)] | None = None
    hysteresis_delay_s: Annotated[int, Field(ge=0, le=3600)] | None = None
    settle_s: Annotated[int, Field(ge=0, le=600)] | None = None
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
        for control_role in control_roles(self.defaults.control):
            if control_role not in self.roles:
                msg = f"defaults.control.role: unknown role `{control_role}`"
                raise ValueError(msg)
        if self.defaults.comfort is not None and self.device_kind not in THERMAL_KINDS:
            msg = f"defaults.comfort: not applicable to device_kind `{self.device_kind.value}`"
            raise ValueError(msg)
        self._capabilities()
        return self

    def _capabilities(self) -> None:
        caps = self.capabilities
        classes = {r.matcher.device_class for r in self.roles.values()}
        control = self.defaults.control
        _need(
            caps, Capability.POWER_MEASURED, "power" in classes, "no role with device_class power"
        )
        _need(
            caps, Capability.ENERGY_COUNTER, "energy" in classes, "no role with device_class energy"
        )
        _need(caps, Capability.ON_OFF, isinstance(control, OnOffControl), "control is not on_off")
        _need(
            caps,
            Capability.POWER_SETPOINT,
            isinstance(control, StepsControl | RelaysControl | ContinuousControl),
            "control is neither steps, relays nor continuous",
        )
        _need(
            caps,
            Capability.TEMPERATURE_SETPOINT,
            isinstance(control, SetpointControl),
            "control is not setpoint",
        )

    def message_keys(self) -> list[str]:
        return [self.name, *(r.description for r in self.roles.values())]


# --- tariff ------------------------------------------------------------------

PRICE_UNIT_PATTERN: Final = r"^(\{currency\}|[A-Z]{3})/(kWh|MWh)$"
"""Unit of a tariff price: a currency (``{currency}`` = the region's) per kWh or MWh."""

_PRICE_UNIT_RE: Final = re.compile(PRICE_UNIT_PATTERN)

PRICE_ENERGY_FACTORS: Final[dict[str, float]] = {"kWh": 1.0, "MWh": 1 / 1000}
"""Factor that turns a price per <energy> into a price per kWh (adapter contract)."""


def price_unit_factor(unit: str) -> float:
    """Multiply a price in ``unit`` by this to get currency/kWh (``EUR/MWh`` -> 0.001).

    Raises ``ValueError`` for a unit that is not ``<currency>/kWh`` or ``<currency>/MWh``.
    """
    match = _PRICE_UNIT_RE.match(unit)
    if match is None:
        msg = f"`{unit}` is not a price unit (<currency>/kWh or <currency>/MWh)"
        raise ValueError(msg)
    return PRICE_ENERGY_FACTORS[match.group(2)]


def _same_currency(unit: str, price_unit: str) -> bool:
    """``unit`` is a price unit whose currency is ``price_unit``'s or ``{currency}``."""
    match = _PRICE_UNIT_RE.match(unit)
    reference = _PRICE_UNIT_RE.match(price_unit)
    if match is None or reference is None:
        return False
    return match.group(1) in (reference.group(1), "{currency}")


class TariffTemplate(TemplateHeader):
    kind: Literal["tariff"]
    direction: Literal["import", "export"]
    tariff_type: Literal["fixed", "dynamic"]
    roles: dict[RoleName, RoleSpec] = Field(default_factory=dict)
    price_unit: Annotated[str, StringConstraints(pattern=PRICE_UNIT_PATTERN)]
    """``<currency>/kWh`` or ``<currency>/MWh``; ``{currency}`` takes the region's currency.
    Adapters convert to currency/kWh with :func:`price_unit_factor` (MWh: / 1000)."""
    resolution_min: Literal[15, 60] | None = None
    """``None`` = derive from the timestamps."""
    price_series: SeriesSource | None = None
    """Where the price curve comes from; required for ``dynamic``."""
    price_includes: Literal["spot_only", "all_in"] | None = None
    """Required for ``dynamic``: whether taxes and fees are already included."""

    @model_validator(mode="after")
    def _tariff(self) -> Self:
        if "." not in self.id:
            msg = "id: must be `<vendor>.<model>`"
            raise ValueError(msg)
        _check_roles(self.roles, required=False)
        if self.tariff_type == "dynamic":
            if "price" not in self.roles:
                msg = "roles: a dynamic tariff needs a `price` role"
                raise ValueError(msg)
            if self.price_series is None:
                msg = "price_series: required for a dynamic tariff"
                raise ValueError(msg)
            if self.price_includes is None:
                msg = "price_includes: required for a dynamic tariff"
                raise ValueError(msg)
        elif self.price_series is not None:
            msg = "price_series: only valid for a dynamic tariff"
            raise ValueError(msg)
        if self.price_series is not None:
            _check_series_roles(self.price_series, self.roles)
            unit = self.price_series.fields.value_unit
            if not _same_currency(unit, self.price_unit):
                msg = (
                    f"price_series.fields.value_unit: `{unit}` does not match the currency of "
                    f"price_unit `{self.price_unit}` (allowed: <currency>/kWh or /MWh)"
                )
                raise ValueError(msg)
        caps = self.capabilities
        _need(caps, Capability.PRICE_CURVE, self.price_series is not None, "no price_series")
        _need(caps, Capability.PRICE_NOW, "price" in self.roles, "no `price` role")
        return self

    def message_keys(self) -> list[str]:
        return [self.name, *(r.description for r in self.roles.values())]


# --- forecast ----------------------------------------------------------------

_FORECAST_UNITS: Final = frozenset({Unit.W, Unit.KW, Unit.WH, Unit.KWH})


class ForecastTemplate(TemplateHeader):
    kind: Literal["forecast"]
    forecast_kind: Literal["pv", "load"]
    roles: dict[RoleName, RoleSpec] = Field(default_factory=dict)
    series: ForecastSeriesSource
    fallback_series: ActionSeries | None = None
    """Used when the primary ``series`` yields nothing (Solcast: the attribute is switched off,
    then the read-only action ``query_forecast_data``)."""
    resolution_min: Annotated[int, Field(ge=1, le=1440)]

    @model_validator(mode="after")
    def _forecast(self) -> Self:
        if "." not in self.id:
            msg = "id: must be `<vendor>.<model>`"
            raise ValueError(msg)
        _check_roles(self.roles, required=isinstance(self.series, AttributeSeries))
        _check_series_roles(self.series, self.roles)
        for name, series in (("series", self.series), ("fallback_series", self.fallback_series)):
            if series is None:
                continue
            unit = parse_unit(series.fields.value_unit)
            if unit not in _FORECAST_UNITS:
                msg = (
                    f"{name}.fields.value_unit: `{series.fields.value_unit}` must be one of "
                    "W, kW, Wh, kWh (power = average over the slot, energy = per slot)"
                )
                raise ValueError(msg)
        fields = self.series.fields
        has_quantiles = fields.p10_field is not None and fields.p90_field is not None
        _need(
            self.capabilities, Capability.PV_QUANTILES, has_quantiles, "p10_field/p90_field missing"
        )
        _need(
            self.capabilities,
            Capability.PV_CURVE,
            self.forecast_kind == "pv",
            "forecast_kind is not pv",
        )
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
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None
    """ISO 4217; ``None`` = take the currency from the Home Assistant configuration."""
    timezone: (
        Annotated[str, StringConstraints(pattern=r"^(UTC|[A-Za-z_]+(/[A-Za-z_+\-0-9]+){1,2})$")]
        | None
    ) = None
    negative_price_rule: NegativePriceRule = NegativePriceRule.NONE
    feed_in_tiers: list[FeedInTier] = Field(default_factory=list)

    @model_validator(mode="after")
    def _region(self) -> Self:
        if self.currency is None and self.country != "generic":
            msg = "currency: required unless country is `generic`"
            raise ValueError(msg)
        bounds = [t.up_to_kwp for t in self.feed_in_tiers]
        for i, bound in enumerate(bounds):
            if bound is None and i != len(bounds) - 1:
                msg = "feed_in_tiers: only the last tier may be unbounded"
                raise ValueError(msg)
        finite = [b for b in bounds if b is not None]
        if any(a >= b for a, b in itertools.pairwise(finite)):
            msg = "feed_in_tiers: `up_to_kwp` must be strictly increasing"
            raise ValueError(msg)
        _need(
            self.capabilities,
            Capability.FEED_IN_TIERS,
            bool(self.feed_in_tiers),
            "no feed_in_tiers",
        )
        _need(
            self.capabilities,
            Capability.NEGATIVE_PRICE_RULE,
            self.negative_price_rule is not NegativePriceRule.NONE,
            "negative_price_rule is `none`",
        )
        return self


# --- battery -----------------------------------------------------------------


class NumberLimitControl(_Frozen):
    """A ``number`` role that sets a battery limit.

    ``min_soc``: discharge floor in ``%`` (discharge lock / reserve); ``max_charge`` and
    ``max_discharge``: power limits; ``grid_charge_power``: forced charging power from the
    grid. ``min``/``max``/``step`` are the entity's range in ``unit`` (``%`` of the
    battery's maximum power for the power kinds).
    """

    kind: Literal["min_soc", "max_charge", "max_discharge", "grid_charge_power"]
    unit: Literal["W", "kW", "%"]
    min: Annotated[float, Field(ge=0)]
    max: Annotated[float, Field(gt=0)]
    step: Annotated[float, Field(gt=0)] | None = None

    @model_validator(mode="after")
    def _range(self) -> Self:
        if not self.min < self.max:
            msg = "controls: `min` must be below `max`"
            raise ValueError(msg)
        if self.unit == "%" and self.max > 100:
            msg = "controls: a value in % allows at most 100"
            raise ValueError(msg)
        if self.kind == "min_soc" and self.unit != "%":
            msg = "controls: `min_soc` is set in %"
            raise ValueError(msg)
        return self


@unique
class BatteryModeMeaning(StrEnum):
    """What selecting a battery mode option does."""

    AUTO = "auto"
    """The inverter's own control (the release baseline)."""
    FORCE_CHARGE = "force_charge"
    """Charge, also from the grid."""
    NO_DISCHARGE = "no_discharge"
    NO_CHARGE = "no_charge"
    OTHER = "other"
    """Any other option; SunCore never selects it."""


OptionValue = Annotated[str, StringConstraints(min_length=1, max_length=200)]
"""A select option exactly as the entity lists it (whitespace is significant)."""


class BatteryModeOption(_Frozen):
    value: OptionValue
    """The select option written to the entity, verbatim (not stripped)."""
    meaning: BatteryModeMeaning


class ModeSelectControl(_Frozen):
    """A ``select`` role choosing the battery's operating mode."""

    kind: Literal["mode"]
    options: Annotated[tuple[BatteryModeOption, ...], Field(min_length=2, max_length=16)]

    @model_validator(mode="after")
    def _options(self) -> Self:
        values = [o.value for o in self.options]
        if len(set(values)) != len(values):
            msg = "controls: mode values must be unique"
            raise ValueError(msg)
        autos = [o for o in self.options if o.meaning is BatteryModeMeaning.AUTO]
        if len(autos) != 1:
            msg = "controls: a mode select needs exactly one option with meaning `auto`"
            raise ValueError(msg)
        return self


class ForceChargeSwitchControl(_Frozen):
    """A ``switch`` role: on = force charging (from the grid if PV is short), off = auto."""

    kind: Literal["force_charge"]


BatteryControl = Annotated[
    NumberLimitControl | ModeSelectControl | ForceChargeSwitchControl,
    Field(discriminator="kind"),
]
"""What a writable battery role can express (``controls`` of a battery template)."""

_CONTROL_DOMAINS: Final = {
    "min_soc": "number",
    "max_charge": "number",
    "max_discharge": "number",
    "grid_charge_power": "number",
    "mode": "select",
    "force_charge": "switch",
}


class BatteryTemplate(TemplateHeader):
    """Battery adapter template (Phase 4, ADR-0006/ADR-0012 kind ``battery``).

    ``roles`` are the readable and writable entities (e.g. ``soc``, ``power``,
    ``min_soc_number``, ``max_charge_number``, ``max_discharge_number``, ``mode_select``,
    ``force_charge_switch``); ``controls`` says what each *writable* role expresses. A
    role without a control is read only. Writes go through the safety gate only.
    """

    kind: Literal["battery"]
    roles: dict[RoleName, RoleSpec]
    controls: Annotated[dict[RoleName, BatteryControl], Field(max_length=8)] = Field(
        default_factory=dict
    )
    settle_s: Annotated[int, Field(ge=0, le=600)] | None = None
    """Delay before the read-back after a write."""

    @model_validator(mode="after")
    def _battery(self) -> Self:
        if "." not in self.id:
            msg = "id: must be `<vendor>.<model>`"
            raise ValueError(msg)
        _check_roles(self.roles)
        kinds: list[str] = []
        for role, control in self.controls.items():
            if role not in self.roles:
                msg = f"controls.{role}: unknown role"
                raise ValueError(msg)
            domain = _CONTROL_DOMAINS[control.kind]
            if self.roles[role].matcher.domain != domain:
                msg = f"controls.{role}: kind `{control.kind}` needs a `{domain}` entity"
                raise ValueError(msg)
            kinds.append(control.kind)
        if len(set(kinds)) != len(kinds):
            msg = "controls: every control kind at most once"
            raise ValueError(msg)
        self._capabilities(set(kinds))
        return self

    def _capabilities(self, kinds: set[str]) -> None:
        caps = self.capabilities
        classes = {r.matcher.device_class for r in self.roles.values()}
        modes = {
            o.meaning
            for c in self.controls.values()
            if isinstance(c, ModeSelectControl)
            for o in c.options
        }
        _need(
            caps, Capability.BATTERY_SOC, "battery" in classes, "no role with device_class battery"
        )
        _need(
            caps, Capability.POWER_MEASURED, "power" in classes, "no role with device_class power"
        )
        _need(caps, Capability.MIN_SOC_LIMIT, "min_soc" in kinds, "no `min_soc` control")
        _need(caps, Capability.CHARGE_LIMIT, "max_charge" in kinds, "no `max_charge` control")
        _need(
            caps, Capability.DISCHARGE_LIMIT, "max_discharge" in kinds, "no `max_discharge` control"
        )
        _need(
            caps,
            Capability.GRID_CHARGE,
            bool(kinds & {"grid_charge_power", "force_charge"})
            or BatteryModeMeaning.FORCE_CHARGE in modes,
            "no grid charge control",
        )
        _need(caps, Capability.BATTERY_MODES, "mode" in kinds, "no `mode` control")

    def message_keys(self) -> list[str]:
        return [self.name, *(r.description for r in self.roles.values())]


Template = Annotated[
    DeviceTemplate | TariffTemplate | ForecastTemplate | RegionTemplate | BatteryTemplate,
    Field(discriminator="kind"),
]
"""Discriminated union on ``kind``."""

TEMPLATE_ADAPTER: Final[
    TypeAdapter[
        DeviceTemplate | TariffTemplate | ForecastTemplate | RegionTemplate | BatteryTemplate
    ]
] = TypeAdapter(Template)

KIND_MODELS: Final[dict[str, type[BaseModel]]] = {
    "device": DeviceTemplate,
    "tariff": TariffTemplate,
    "forecast": ForecastTemplate,
    "region": RegionTemplate,
    "battery": BatteryTemplate,
}

__all__ = [
    "KIND_MODELS",
    "PRICE_ENERGY_FACTORS",
    "PRICE_UNIT_PATTERN",
    "REGION_ID_PATTERN",
    "SCHEMA_VERSION",
    "TEMPLATE_ADAPTER",
    "TEMPLATE_ID_PATTERN",
    "TEMPLATE_KINDS",
    "VERSION_PATTERN",
    "ActionSeries",
    "AttributeSeries",
    "BatteryControl",
    "BatteryModeMeaning",
    "BatteryModeOption",
    "BatteryTemplate",
    "DeviceDefaults",
    "DeviceTemplate",
    "EnergyDashboardSeries",
    "EntityMatcher",
    "FeedInTier",
    "ForceChargeSwitchControl",
    "ForecastSeriesSource",
    "ForecastTemplate",
    "ModeSelectControl",
    "NegativePriceRule",
    "NumberLimitControl",
    "PowerModelDefault",
    "RegionTemplate",
    "RoleSpec",
    "SeriesFields",
    "SeriesSource",
    "SourceRef",
    "TariffTemplate",
    "Template",
    "TemplateHeader",
    "TemplateKind",
    "TestedWith",
    "compile_pattern",
    "price_unit_factor",
]
