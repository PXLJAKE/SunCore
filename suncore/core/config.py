# SPDX-License-Identifier: Apache-2.0
"""Configuration sections besides ``site`` (region, tariff, forecast, devices, goals, ...).

Pure, frozen models; storage and API only move them around. ``operation`` is *stored*
but never read by the safety gate before Phase 3: the gate stays shadow-only.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from collections.abc import Collection, Mapping
from typing import Annotated, Any, Final, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    SerializationInfo,
    StringConstraints,
    field_serializer,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from suncore.core.battery import BatterySettings, GridChargePolicy
from suncore.core.decisions import OperatingMode
from suncore.core.devices import DeviceConfig, DeviceId, RoleName, TemplateId
from suncore.core.expert import ExpertConfig
from suncore.core.series import PriceIncludes
from suncore.core.site import EntityRef, SiteConfig
from suncore.core.tariff import HHMM_PATTERN, NIGHT_WINDOW, PriceFormula, TouTariff, WeeklySchedule
from suncore.core.validation import ENTITY_ID_PATTERN, invalid

REGION_ID_PATTERN: Final = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$"
"""Region id: ``de`` or ``<country>.<variant>``; also the region template id pattern."""
STEP_PATTERN: Final = r"^[a-z][a-z0-9_]{0,47}$"
TIME_PATTERN: Final = HHMM_PATTERN

Price = Annotated[float, Field(ge=0, le=100)]
"""Currency per kWh."""
ActionParamName = Annotated[str, StringConstraints(min_length=1, max_length=64)]
ActionParamValue = (
    Annotated[str, StringConstraints(min_length=1, max_length=200)]
    | Annotated[
        list[Annotated[str, StringConstraints(min_length=1, max_length=64)]],
        Field(min_length=1, max_length=16),
    ]
)
"""A request field value of a tariff template's action: text, or a list of texts."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- region -------------------------------------------------------------------


class RegionConfig(_Frozen):
    """Region template choice, currency, time zone and UI language."""

    schema_version: int = 1
    region_id: Annotated[str, StringConstraints(pattern=REGION_ID_PATTERN, max_length=64)]
    """Region template id, e.g. ``de`` or ``generic``."""
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    """ISO 4217 code."""
    timezone: str
    """IANA time zone name."""
    language: Literal["auto", "en", "de"] = "auto"

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            raise invalid(
                "unknown_timezone", "unknown IANA time zone {timezone!r}", timezone=value
            ) from exc
        return value


# --- tariff -------------------------------------------------------------------


class ImportTariff(_Frozen):
    """How the price of imported energy is obtained.

    * ``fixed``: one ``fixed_price``.
    * ``dynamic``: a price source (``template_id`` and/or ``price_entity``); ``formula``
      turns a spot-only price into the end price (tariff v3; ``None`` = the source's price
      is used as it is, e.g. Tibber's all-in price).
    * ``tou``: fixed time-of-use prices (HT/NT, seasonal) in ``tou`` (tariff v3).
    """

    kind: Literal["fixed", "dynamic", "tou"]
    template_id: TemplateId | None = None
    price_entity: EntityRef | None = None
    fixed_price: Price | None = None
    formula: PriceFormula | None = None
    tou: TouTariff | None = None
    role_entities: dict[RoleName, EntityRef] = Field(default_factory=dict)
    """Entities of the template's roles besides the main one (``price_entity`` is the role
    ``price``), e.g. Octopus ``current_day_rates`` and ``next_day_rates`` (Phase 4 WP4)."""
    action_params: dict[ActionParamName, ActionParamValue] = Field(default_factory=dict)
    """Values for the request fields the template's action asks the user for (Nord Pool
    ``config_entry``, ``areas``, ``currency``); the template names which are required."""
    series_key: Annotated[str, StringConstraints(min_length=1, max_length=120)] | None = None
    """Which entry of a multi-entry action response to read (Tibber home name, Nord Pool
    area) when the response holds more than one; ``None`` = the only one."""
    price_includes: PriceIncludes | None = None
    """The user's statement about a source whose content depends on their setup (ENTSO-E
    with a modifier template): overrides the template's ``price_includes``."""

    @model_validator(mode="after")
    def _source_settings(self) -> Self:
        if self.kind != "dynamic" and (
            self.role_entities
            or self.action_params
            or self.series_key is not None
            or self.price_includes is not None
        ):
            raise invalid(
                "import_source_dynamic_only",
                "tariff.import: source settings (`role_entities`, `action_params`, "
                "`series_key`, `price_includes`) need kind `dynamic`",
            )
        if "price" in self.role_entities:
            raise invalid("import_role_price", "tariff.import: the role `price` is `price_entity`")
        return self

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.kind == "fixed":
            if self.fixed_price is None:
                raise invalid(
                    "import_fixed_needs_price", "tariff.import: kind `fixed` needs `fixed_price`"
                )
            if self.template_id is not None or self.price_entity is not None:
                raise invalid(
                    "import_fixed_extra",
                    "tariff.import: kind `fixed` takes no `template_id`/`price_entity`",
                )
        elif self.kind == "dynamic":
            if self.template_id is None and self.price_entity is None:
                raise invalid(
                    "import_dynamic_needs_source",
                    "tariff.import: kind `dynamic` needs `template_id` or `price_entity`",
                )
            if self.fixed_price is not None:
                raise invalid(
                    "import_dynamic_extra", "tariff.import: kind `dynamic` takes no `fixed_price`"
                )
        else:
            if self.tou is None:
                raise invalid("import_tou_needs_periods", "tariff.import: kind `tou` needs `tou`")
            if (
                self.fixed_price is not None
                or self.template_id is not None
                or self.price_entity is not None
            ):
                raise invalid(
                    "import_tou_extra",
                    "tariff.import: kind `tou` takes no fixed price, template or price entity",
                )
        if self.formula is not None and self.kind != "dynamic":
            raise invalid(
                "import_formula_dynamic_only", "tariff.import: `formula` needs kind `dynamic`"
            )
        if self.tou is not None and self.kind != "tou":
            raise invalid("import_tou_only", "tariff.import: `tou` needs kind `tou`")
        return self


class ExportTier(_Frozen):
    """Feed-in price up to ``up_to_kwp`` of installed power (``None`` = no upper bound)."""

    up_to_kwp: Annotated[float, Field(gt=0, le=10_000)] | None = None
    price: Price


class ExportTariff(_Frozen):
    """Feed-in remuneration."""

    kind: Literal["fixed", "tiered", "none"]
    fixed_price: Price | None = None
    tiers: tuple[ExportTier, ...] = ()
    remunerate_negative: bool = Field(
        default=False,
        json_schema_extra={
            "param": {"group": "tariff", "tags": ["expert"], "see": "docs/user/tariffs.md"}
        },
    )
    """Per-plant switch for the region's negative-price rule (§51 EEG in Germany; Phase 5
    WP5, additive in ``tariff`` v3). ``True`` = this plant is remunerated also in slots with a
    negative spot price (older plants, exempt plants), so the rule ``no_remuneration`` does not
    zero its export price. ``False`` (default) = the region template's rule applies."""
    negative_reference_entity: EntityRef | None = Field(
        default=None,
        json_schema_extra={"param": {"group": "tariff", "tags": ["expert"]}},
    )
    """Optional sensor with the *spot* price (currency/kWh) for the negative-price rule when
    the import source is an all-in tariff (Tibber): the rule then looks at this price instead
    of the import end price. It gives the current slot only; later slots keep using the proxy.
    ``None`` (default) = the proxy as before (Phase 5 WP5, additive in ``tariff`` v3)."""

    @field_validator("negative_reference_entity")
    @classmethod
    def _reference_domain(cls, value: EntityRef | None) -> EntityRef | None:
        if value is not None and not value.entity_id.startswith(("sensor.", "input_number.")):
            raise invalid(
                "export_reference_domain",
                "tariff.export.negative_reference_entity: {entity_id} is not a sensor",
                entity_id=value.entity_id,
            )
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.kind == "fixed":
            if self.fixed_price is None or self.tiers:
                raise invalid(
                    "export_fixed_invalid",
                    "tariff.export: kind `fixed` needs `fixed_price` and no tiers",
                )
        elif self.kind == "tiered":
            if self.fixed_price is not None or not self.tiers:
                raise invalid(
                    "export_tiered_invalid",
                    "tariff.export: kind `tiered` needs tiers and no `fixed_price`",
                )
            bounds = [t.up_to_kwp for t in self.tiers]
            if None in bounds[:-1]:
                raise invalid(
                    "tier_unbounded", "tariff.export: only the last tier may be unbounded"
                )
            finite = [b for b in bounds if b is not None]
            if any(a >= b for a, b in itertools.pairwise(finite)):
                raise invalid(
                    "tier_order", "tariff.export: tier bounds must be strictly increasing"
                )
        elif self.fixed_price is not None or self.tiers:
            raise invalid("export_none_extra", "tariff.export: kind `none` takes no price or tiers")
        return self


class TariffConfig(_Frozen):
    """Import and export prices (the forecast sources live in :class:`ForecastConfig`).

    v3 (Phase 4): ``import.formula`` and kind ``tou``; v2 documents are valid v3 bodies.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)
    """``import`` is a Python keyword: the JSON key is ``import``, ``import_`` is accepted too.

    Always dump with ``by_alias=True`` (ConfigStore and the API do); documents stored
    before the alias existed (key ``import_``) still load.
    """

    schema_version: int = 3
    import_: ImportTariff = Field(alias="import")
    export: ExportTariff

    def entity_refs(self) -> list[EntityRef]:
        refs = [self.import_.price_entity] if self.import_.price_entity is not None else []
        if self.export.negative_reference_entity is not None:
            refs.append(self.export.negative_reference_entity)
        return [*refs, *self.import_.role_entities.values()]


# --- forecast -----------------------------------------------------------------


class ForecastSourceConfig(_Frozen):
    """One PV forecast source: its template, the entities of its roles and its weight.

    ``weight_mode = auto`` blends by measured skill (inverse MAE, WP5); ``manual`` uses
    ``weight`` (relative, normalised over all sources by the blender).
    """

    template_id: TemplateId
    entities: dict[RoleName, EntityRef] = Field(default_factory=dict)
    """Role of the template (e.g. ``forecast_today``) to entity."""
    weight_mode: Literal["auto", "manual"] = "auto"
    weight: Annotated[float, Field(gt=0, le=1)] | None = None
    config_entry_id: (
        Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")] | None
    ) = None
    """Template ``series.source: energy_solar_forecast`` (Forecast.Solar): the config entry id
    under which ``energy/solar_forecast`` lists the integration (the wizard offers the ids of
    ``energy/get_prefs`` ``config_entry_solar_forecast``). ``None`` = the only entry there is."""

    @model_validator(mode="after")
    def _weight(self) -> Self:
        if self.weight_mode == "manual" and self.weight is None:
            raise invalid("forecast_manual_weight", "forecast: a manual weight needs `weight`")
        if self.weight_mode == "auto" and self.weight is not None:
            raise invalid(
                "forecast_auto_weight", "forecast: `weight` is only used with weight_mode manual"
            )
        return self


class LearningSettings(_Frozen):
    """What SunCore may learn from history (forecast calibration, load model).

    Kept in the ``forecast`` section because it governs how forecasts are produced. Before
    ``min_history_days`` of history exist, calibration and the ML load model only collect
    statistics and change nothing. ``load_ml_mode``: ``off``; ``shadow`` (default) = the
    optional ML load model runs in parallel and is only scored and shown for comparison;
    ``auto`` = it is used when it beats the profile forecast in the accuracy statistics -
    it never replaces the profiles otherwise.
    """

    min_history_days: Annotated[
        int,
        Field(
            ge=1,
            le=365,
            json_schema_extra={"param": {"group": "forecast", "unit": "d", "tags": ["expert"]}},
        ),
    ] = 30
    load_ml_mode: Literal["off", "shadow", "auto"] = Field(
        default="shadow",
        json_schema_extra={"param": {"group": "forecast", "tags": ["expert"]}},
    )


class LoadForecastConfig(_Frozen):
    """Optional inputs of the load forecast (Phase 4 WP6; additive in ``forecast`` v2).

    ``workday``: a ``binary_sensor`` of the ``workday`` integration (``on`` = today is a
    workday); with it the day types come from the user's own holiday calendar (today from the
    state, other days through ``workday.check_date``), without it from the weekday (Mon-Fri
    workday, Sat/Sun weekend, no holidays). ``weather``: a ``weather`` entity whose outside
    temperature (state attribute ``temperature``) is recorded and whose hourly forecast
    (``weather.get_forecasts``) drives the temperature term of the load profile.
    """

    workday: EntityRef | None = None
    weather: EntityRef | None = None

    @field_validator("workday")
    @classmethod
    def _binary_sensor(cls, value: EntityRef | None) -> EntityRef | None:
        if value is not None and not value.entity_id.startswith("binary_sensor."):
            raise invalid(
                "forecast_workday_domain",
                "forecast.load.workday: {entity_id} is not a binary_sensor",
                entity_id=value.entity_id,
            )
        return value

    @field_validator("weather")
    @classmethod
    def _weather(cls, value: EntityRef | None) -> EntityRef | None:
        if value is not None and not value.entity_id.startswith("weather."):
            raise invalid(
                "forecast_weather_domain",
                "forecast.load.weather: {entity_id} is not a weather entity",
                entity_id=value.entity_id,
            )
        return value

    def entity_refs(self) -> list[EntityRef]:
        return [r for r in (self.workday, self.weather) if r is not None]


class ForecastConfig(_Frozen):
    """PV forecast sources (schema v2: several sources, blending, learning settings) and the
    optional inputs of the load forecast (``load``, added in Phase 4 WP6 without a version
    bump: an absent ``load`` is the default, so every stored v2 document stays valid).

    Independent of the tariff so that the wizard can store the choice without prices.
    Forecast uncertainty is handled by the learned risk model and the battery section's
    ``risk`` level, not by a flat discount.
    """

    schema_version: int = 2
    sources: Annotated[list[ForecastSourceConfig], Field(max_length=8)] = Field(
        default_factory=list
    )
    learning: LearningSettings = LearningSettings()
    load: LoadForecastConfig = LoadForecastConfig()

    @model_validator(mode="after")
    def _unique_templates(self) -> Self:
        ids = [s.template_id for s in self.sources]
        if len(set(ids)) != len(ids):
            raise invalid("forecast_templates_unique", "forecast: every template only once")
        return self

    @property
    def template_ids(self) -> list[str]:
        return [s.template_id for s in self.sources]

    def entity_refs(self) -> list[EntityRef]:
        refs: dict[str, EntityRef] = {}
        for source in self.sources:
            for ref in source.entities.values():
                refs.setdefault(ref.entity_id, ref)
        for ref in self.load.entity_refs():
            refs.setdefault(ref.entity_id, ref)
        return list(refs.values())


def migrate_forecast_v1_to_v2(body: dict[str, Any]) -> dict[str, Any]:
    """v1 (template ids + one role map) -> v2 (one source per template, weights auto).

    v1 kept a single role map for all templates, so it cannot say which entity belongs to
    which template. Every source gets the whole map: the adapter reads only the roles its
    template defines, nothing is lost, and the wizard writes clean per-source maps on the
    next save. (Only one forecast template, ``solcast.pv``, existed in v1.)
    """
    ids = body.pop("forecast_template_ids", None) or []
    entities = body.pop("forecast_entities", None) or {}
    if not isinstance(ids, list) or not isinstance(entities, dict):
        msg = "forecast v1: `forecast_template_ids` must be a list, `forecast_entities` a map"
        raise TypeError(msg)
    body["sources"] = [{"template_id": i, "entities": dict(entities)} for i in ids]
    return body


def formula_on_all_in_source(
    tariff: TariffConfig, price_includes: PriceIncludes | None
) -> PydanticCustomError | None:
    """The error for a price formula on top of an all-in price source, else ``None``.

    ``price_includes`` is that of the import tariff's template (``None`` = unknown, e.g. a
    bare ``price_entity``). An all-in price (Tibber) already contains grid fees, levies and
    VAT; the formula would add them a second time. :class:`ImportTariff` cannot check this
    because it does not know the template catalogue - the API does (``PUT /config/tariff``).
    """
    if tariff.import_.formula is None or price_includes != "all_in":
        return None
    return invalid(
        "import_formula_all_in",
        "tariff.import: template {template_id} delivers all-in prices, a formula would "
        "count fees and taxes twice",
        template_id=tariff.import_.template_id or "",
    )


def missing_action_params(
    tariff: TariffConfig, template_id: str, required: Collection[str]
) -> list[tuple[str, PydanticCustomError]]:
    """The request fields a template's action needs that ``tariff.import.action_params`` lacks.

    ``required`` are the names the template marks as required (the catalogue knows them, the
    model does not - the API passes them in, like for :func:`formula_on_all_in_source`).
    """
    return [
        (
            name,
            invalid(
                "import_param_missing",
                "tariff.import: template {template_id} needs the setting {field}",
                template_id=template_id,
                field=name,
            ),
        )
        for name in required
        if name not in tariff.import_.action_params
    ]


def migrate_tariff_v2_to_v3(body: dict[str, Any]) -> dict[str, Any]:
    """Tariff v3 only adds optional fields (``formula``, kind ``tou``): unchanged body."""
    return body


# --- devices ------------------------------------------------------------------


class DevicesConfig(_Frozen):
    """All configured devices."""

    schema_version: int = 1
    devices: list[DeviceConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [d.id for d in self.devices]
        if len(set(ids)) != len(ids):
            raise invalid("device_ids_unique", "devices: ids must be unique")
        return self

    def by_priority(self) -> list[DeviceConfig]:
        """Devices ordered by ``priority`` (lower first), ties broken by id."""
        return sorted(self.devices, key=lambda d: (d.priority, d.id))

    def entity_refs(self) -> list[EntityRef]:
        return [r for d in self.devices for r in d.entity_refs()]

    def ids(self) -> frozenset[str]:
        return frozenset(d.id for d in self.devices)


# --- goals --------------------------------------------------------------------


class BatteryTarget(_Frozen):
    """Be at ``soc_pct`` by local time ``time`` (``HH:MM``)."""

    soc_pct: Annotated[float, Field(ge=0, le=100)]
    time: Annotated[str, StringConstraints(pattern=TIME_PATTERN)]


class GoalsConfig(_Frozen):
    """User goals (schema v2).

    v2 drops ``grid_charge_night_only`` (answers 2026-10-16, question 7): grid charging is
    allowed by a price level and/or a schedule in the ``battery`` section's
    :class:`~suncore.core.battery.GridChargePolicy`. ``grid_charge_schedule`` carries the
    window a v1 document implied (``True`` -> 22:00-06:00 daily) so the choice is not
    lost; :func:`effective_grid_charge_policy` uses it as the policy's schedule when the
    policy has none. **It never enables grid charging**: that stays off until the user
    enables the policy with a level or a schedule. How cautiously targets are planned is
    the ``battery`` section's ``risk`` level, not a goal.
    """

    schema_version: int = 2
    battery_target: BatteryTarget | None = None
    climate_before_export: bool = False
    grid_charge_schedule: WeeklySchedule | None = None


def effective_grid_charge_policy(goals: GoalsConfig, battery: BatterySettings) -> GridChargePolicy:
    """The grid-charge policy the planner applies (single source of the precedence rule).

    1. The ``battery`` section's policy wins: its ``enabled``, ``max_price`` and
       ``schedule`` are used as they are.
    2. ``goals.grid_charge_schedule`` only fills in the schedule when the policy is
       enabled and has none of its own (the migrated v1 "night only" choice then narrows a
       price-level policy to the night window).
    3. It never enables charging: a disabled policy is returned unchanged.

    :class:`GridChargePolicy` validation matches this: an enabled policy must carry its
    own limit (price level or schedule), so it is complete without ``goals`` and the goals
    schedule can only narrow it, never be its only limit.
    """
    policy = battery.grid_charge
    if not policy.enabled or policy.schedule is not None or goals.grid_charge_schedule is None:
        return policy
    return policy.model_copy(update={"schedule": goals.grid_charge_schedule})


def migrate_goals_v1_to_v2(body: dict[str, Any]) -> dict[str, Any]:
    """``grid_charge_night_only`` (v1 default ``True``) -> ``grid_charge_schedule``.

    ``True`` becomes the night window 22:00-06:00 every day, ``False`` no schedule. Grid
    charging itself is *not* enabled by this migration (see :class:`GoalsConfig`).
    """
    night_only = body.pop("grid_charge_night_only", True)
    body["grid_charge_schedule"] = (
        NIGHT_WINDOW.model_dump(mode="json") if night_only is True else None
    )
    return body


# --- operation ----------------------------------------------------------------


class OperationConfig(_Frozen):
    """Stored operating modes. Not read by the safety gate before Phase 3.

    Devices that are new at ``devices`` put/import time lose any entry here (shadow).
    """

    schema_version: int = 1
    mode: OperatingMode = OperatingMode.SHADOW
    device_modes: dict[DeviceId, OperatingMode] = Field(default_factory=dict)
    """Missing entry = shadow. Single store for per-device modes (ADR-0012)."""
    battery_mode: OperatingMode = OperatingMode.SHADOW
    """Mode of the battery asset (ADR-0013 decision 6; additive in v1, Phase 4 WP10). The
    same rules as a device's: raising it needs the battery's identity token, a changed battery
    identity or an import drops it to shadow."""
    inverter_mode: OperatingMode = OperatingMode.SHADOW
    """Mode of the inverter limit asset (ADR-0018 decision 3; additive in v1, PR 6-G): capped at
    assisted until ``inverter.limit_verified``; a changed inverter adapter or an import drops
    it to shadow."""

    def without_modes_of(self, device_ids: frozenset[str]) -> Self:
        """Drop the non-shadow entries of ``device_ids`` (they fall back to shadow)."""
        kept = {
            k: v
            for k, v in self.device_modes.items()
            if k not in device_ids or v is OperatingMode.SHADOW
        }
        if len(kept) == len(self.device_modes):
            return self
        return self.model_copy(update={"device_modes": kept})

    def without_inverter_mode(self) -> Self:
        """The inverter limit back in shadow (adapter changed, import)."""
        if self.inverter_mode is OperatingMode.SHADOW:
            return self
        return self.model_copy(update={"inverter_mode": OperatingMode.SHADOW})

    def without_battery_mode(self) -> Self:
        """The battery back in shadow (identity changed, import)."""
        if self.battery_mode is OperatingMode.SHADOW:
            return self
        return self.model_copy(update={"battery_mode": OperatingMode.SHADOW})


# --- mqtt (Phase 6, ADR-0016) ---------------------------------------------------

SECRET_MASK: Final = "**********"
"""What a secret looks like outside the storage (API, export, history). A ``PUT`` or an import
that sends the mask back keeps the stored secret (``MqttConfig.keep_secret_of``)."""
REVEAL_SECRETS: Final = "reveal_secrets"
"""Serialization context flag: only the config storage dumps secrets in clear text."""
HOST_PATTERN: Final = r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$"
TOPIC_PREFIX_PATTERN: Final = r"^[A-Za-z0-9_-]{1,64}$"


class MqttConfig(_Frozen):
    """The HA entity surface over MQTT discovery (ADR-0016 decisions 2, 5).

    ``broker``: ``supervisor`` = host, port and credentials from the Supervisor services API
    (the default; nothing to enter), ``manual`` = ``host``/``port``/``tls``/``username``/
    ``password``. The password is a secret: masked in the API, the export and the history.
    ``allow_mode_raise`` lets HA raise a mode or clear the emergency stop (default off).
    Without a broker the feature is unavailable with a hint; nothing else depends on it.
    """

    schema_version: int = 1
    enabled: bool = True
    broker: Literal["supervisor", "manual"] = "supervisor"
    host: Annotated[str, StringConstraints(pattern=HOST_PATTERN)] | None = None
    port: Annotated[int, Field(ge=1, le=65_535)] = 1883
    tls: bool = False
    username: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    password: Annotated[SecretStr, Field(max_length=256)] | None = None
    discovery_prefix: Annotated[str, StringConstraints(pattern=TOPIC_PREFIX_PATTERN)] = (
        "homeassistant"
    )
    allow_mode_raise: bool = False

    @field_serializer("password")
    def _dump_password(self, value: SecretStr | None, info: SerializationInfo) -> str | None:
        if value is None:
            return None
        context = info.context
        if isinstance(context, Mapping) and context.get(REVEAL_SECRETS) is True:
            return value.get_secret_value()
        return SECRET_MASK

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.broker == "manual" and self.host is None:
            raise invalid("mqtt_manual_needs_host", "mqtt: a manual broker needs `host`")
        if self.broker == "supervisor" and (
            self.host is not None or self.username is not None or self.password is not None
        ):
            raise invalid(
                "mqtt_supervisor_extra",
                "mqtt: the Supervisor broker takes no host or credentials",
            )
        if self.password is not None and self.username is None:
            raise invalid("mqtt_password_needs_user", "mqtt: a password needs `username`")
        return self

    @property
    def password_masked(self) -> bool:
        """The password is the mask (sent back by the UI or read from an export)."""
        return self.password is not None and self.password.get_secret_value() == SECRET_MASK

    def keep_secret_of(self, previous: MqttConfig | None) -> Self:
        """The masked password replaced by ``previous``'s (none when there is none)."""
        if not self.password_masked:
            return self
        password = previous.password if previous is not None else None
        if password is not None and password.get_secret_value() == SECRET_MASK:
            password = None
        return self.model_copy(update={"password": password})

    def require_secret_of(self, previous: MqttConfig | None) -> Self:
        """Like :meth:`keep_secret_of`, for a ``PUT``: a mask with nothing stored to keep is
        rejected (:class:`MqttPasswordIsMaskError`) instead of silently dropped, so a password
        that really is the mask never vanishes unnoticed."""
        kept = self.keep_secret_of(previous)
        if self.password_masked and kept.password is None:
            raise MqttPasswordIsMaskError
        return kept


class MqttPasswordIsMaskError(ValueError):
    """A ``PUT`` sent the mask as the MQTT password, but no password is stored to keep."""


def mqtt_password_is_mask() -> PydanticCustomError:
    """The validation error the API reports for :class:`MqttPasswordIsMaskError`."""
    return invalid(
        "mqtt_password_is_mask",
        "mqtt: the password equals the mask and no password is stored; enter another one",
    )


# --- notifications (Phase 6, ADR-0016 decision 7) ----------------------------------------

NOTIFY_SERVICE_PATTERN: Final = r"^[a-z0-9]+(?:_[a-z0-9]+)*$"
_NOTIFY_SERVICE_RE: Final = re.compile(NOTIFY_SERVICE_PATTERN)
_ENTITY_ID_RE: Final = re.compile(ENTITY_ID_PATTERN)
RESERVED_NOTIFY_SERVICES: Final = frozenset({"send_message", "persistent_notification"})


class NotificationCategories(_Frozen):
    """Which categories notify (plan WP5 defaults)."""

    error: bool = True
    safety: bool = True
    proposal: bool = True
    program: bool = True
    ev_target: bool = True
    cheap_window: bool = False
    daily_summary: bool = False


class NotifyTarget(_Frozen):
    """A push target: a ``notify`` entity (``notify.send_message``) or a legacy service name
    (``notify.<service>``, e.g. ``mobile_app_pixel``)."""

    kind: Literal["entity", "service"]
    id: Annotated[str, StringConstraints(min_length=1, max_length=255)]

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.kind == "entity":
            if not _ENTITY_ID_RE.match(self.id) or not self.id.startswith("notify."):
                raise invalid(
                    "notify_target_entity",
                    "notifications: {id} is not a notify entity",
                    id=self.id,
                )
        elif not _NOTIFY_SERVICE_RE.match(self.id) or self.id in RESERVED_NOTIFY_SERVICES:
            raise invalid(
                "notify_target_service",
                "notifications: {id} is not a notify service name",
                id=self.id,
            )
        return self


class QuietHours(_Frozen):
    """Local quiet hours for non-critical categories (default 22:00-07:00, may wrap)."""

    enabled: bool = True
    start: Annotated[str, StringConstraints(pattern=TIME_PATTERN)] = "22:00"
    end: Annotated[str, StringConstraints(pattern=TIME_PATTERN)] = "07:00"

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.start == self.end:
            raise invalid("quiet_hours_empty", "notifications: quiet hours start equals end")
        return self


class NotificationsConfig(_Frozen):
    """Notification settings (ADR-0016 decision 7, plan WP5).

    Persistent notifications are on by default (errors and safety events); push needs a
    target the user picks. ``daily_push_cap`` limits push messages per local day.
    """

    schema_version: int = 1
    persistent: bool = True
    categories: NotificationCategories = NotificationCategories()
    targets: Annotated[tuple[NotifyTarget, ...], Field(max_length=8)] = ()
    quiet_hours: QuietHours = QuietHours()
    daily_push_cap: Annotated[int, Field(ge=0, le=200)] = 20

    @model_validator(mode="after")
    def _unique(self) -> Self:
        keys = [(t.kind, t.id) for t in self.targets]
        if len(set(keys)) != len(keys):
            raise invalid("notify_targets_unique", "notifications: every target only once")
        return self

    @property
    def push_enabled(self) -> bool:
        return bool(self.targets) and self.daily_push_cap > 0


# --- inverter (Phase 6, ADR-0018 decision 3) ----------------------------------------------

InverterAdapter = Literal["none", "generic_number", "kostal_rest", "kostal_modbus"]
InverterCapability = Literal["export_limit", "ac_limit"]

_ADAPTER_CAPABILITY: Final[dict[str, InverterCapability]] = {
    "kostal_rest": "export_limit",  # Inverter:ActivePowerLimitation, semantics UNVERIFIED
    "kostal_modbus": "ac_limit",  # register 533 %, volatile; battery included UNVERIFIED
}


class InverterModbusLink(_Frozen):
    """How the ``kostal_modbus`` inverter adapter writes register 533 (``modbus.write_register``).

    ``hub`` is the ``name`` of the user's ``modbus:`` YAML hub, ``slave`` the inverter's unit id
    (Kostal default 71; mandatory because the action defaults to 1). Register 533 is volatile
    (lost on an inverter reset, Kostal §3.3): ``heartbeat_s`` is the re-write period while
    SunCore holds a limit. Verified 2026-11-24 against ``home-assistant/core`` ``dev``
    ``components/modbus/services.py``: an ``int`` value is written with function code 0x06
    (write single register), which is what Kostal lists for 533.
    """

    hub: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_\-]{1,64}$")] = "modbus_hub"
    slave: Annotated[int, Field(ge=1, le=255)]
    heartbeat_s: Annotated[float, Field(ge=10, le=3600)] = 60.0


class InverterConfig(_Frozen):
    """The inverter limit adapter (ADR-0018 decision 3; writes since PR 6-G).

    ``limit_verified`` is set only after the documented verification test; until then the
    inverter asset runs at most assisted (X6) and the dynamic 0 W limit at negative prices stays
    off. A change of the adapter's identity (:meth:`identity_token`) resets it and drops
    ``operation.inverter_mode`` to shadow (storage). ``nominal_ac_w`` is needed for ``ac_limit``
    (the percent base, invariant X2). ``limit_entity`` is the entity that shows the limit: the
    number entity of the generic adapter (W for ``export_limit``, % for ``ac_limit``) and of
    ``kostal_rest`` (``Inverter:ActivePowerLimitation``), the modbus sensor that reads register
    533 back for ``kostal_modbus``; without it the adapter cannot read back and is not built.
    ``enforce_static``: ``False`` (default) = the static export limit is enforced by the
    inverter's own setting (the maintainer's 13.5 kW, plan §6 question 6) and SunCore writes a
    limit only below it (dynamic or zero export); ``True`` = SunCore enforces it too.
    """

    schema_version: int = 1
    adapter: InverterAdapter = "none"
    capability: InverterCapability | None = None
    limit_entity: EntityRef | None = None
    nominal_ac_w: Annotated[float, Field(gt=0, le=1_000_000, allow_inf_nan=False)] | None = None
    limit_verified: bool = False
    # PR 6-G (additive):
    template_id: TemplateId | None = None
    """The ``inverter`` template the roles were resolved from (informational, identity)."""
    modbus: InverterModbusLink | None = None
    enforce_static: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.adapter == "none":
            if (
                self.capability is not None
                or self.limit_entity is not None
                or self.limit_verified
                or self.modbus is not None
                or self.template_id is not None
            ):
                raise invalid(
                    "inverter_none_extra",
                    "inverter: without an adapter there is no capability, entity or verification",
                )
            return self
        if self.capability is None:
            raise invalid("inverter_needs_capability", "inverter: the adapter needs `capability`")
        fixed = _ADAPTER_CAPABILITY.get(self.adapter)
        if fixed is not None and self.capability != fixed:
            raise invalid(
                "inverter_capability_mismatch",
                "inverter: adapter {adapter} provides {capability}",
                adapter=self.adapter,
                capability=fixed,
            )
        if self.adapter == "generic_number" and self.limit_entity is None:
            raise invalid("inverter_entity", "inverter: the generic adapter needs `limit_entity`")
        if self.modbus is not None and self.adapter != "kostal_modbus":
            raise invalid(
                "inverter_modbus_only", "inverter: `modbus` belongs to the kostal_modbus adapter"
            )
        if self.limit_entity is not None and self.limit_entity.invert_sign:
            raise invalid("inverter_entity_invert", "inverter: invert_sign is meaningless here")
        if self.capability == "ac_limit" and self.nominal_ac_w is None:
            raise invalid("inverter_ac_needs_nominal", "inverter: `ac_limit` needs `nominal_ac_w`")
        return self

    @property
    def mode_capped(self) -> bool:
        """X6: at most assisted until the limit is verified."""
        return self.adapter != "none" and not self.limit_verified

    def entity_refs(self) -> list[EntityRef]:
        return [self.limit_entity] if self.limit_entity is not None else []

    def identity_token(self) -> str | None:
        """What makes the adapter the same adapter (``None`` = no adapter).

        Adapter, capability, template, entity, nominal power and the modbus link; the
        verification flag, the heartbeat and ``enforce_static`` are settings, not identity.
        """
        if self.adapter == "none":
            return None
        payload = json.dumps(
            [
                self.adapter,
                self.capability,
                self.template_id,
                None if self.limit_entity is None else self.limit_entity.entity_id,
                self.nominal_ac_w,
                None if self.modbus is None else [self.modbus.hub, self.modbus.slave],
            ],
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


# --- wizard -------------------------------------------------------------------


class WizardState(_Frozen):
    schema_version: int = 1
    completed: bool = False
    completed_steps: list[Annotated[str, StringConstraints(pattern=STEP_PATTERN)]] = Field(
        default_factory=list
    )
    version: Annotated[int, Field(ge=1)] = 1
    """Wizard flow version that produced ``completed_steps``."""


SECTION_MODELS: Final[Mapping[str, type[BaseModel]]] = {
    "site": SiteConfig,
    "region": RegionConfig,
    "tariff": TariffConfig,
    "forecast": ForecastConfig,
    "devices": DevicesConfig,
    "goals": GoalsConfig,
    "operation": OperationConfig,
    "wizard": WizardState,
    "battery": BatterySettings,
    "mqtt": MqttConfig,
    "notifications": NotificationsConfig,
    "inverter": InverterConfig,
    "expert": ExpertConfig,
}
"""All configuration sections by name and their document model (API and storage share it)."""
