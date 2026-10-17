# SPDX-License-Identifier: Apache-2.0
"""Configuration sections besides ``site`` (region, tariff, forecast, devices, goals, ...).

Pure, frozen models; storage and API only move them around. ``operation`` is *stored*
but never read by the safety gate before Phase 3: the gate stays shadow-only.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from typing import Annotated, Any, Final, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from suncore.core.battery import BatterySettings, GridChargePolicy
from suncore.core.decisions import OperatingMode
from suncore.core.devices import DeviceConfig, DeviceId, RoleName, TemplateId
from suncore.core.series import PriceIncludes
from suncore.core.site import EntityRef, SiteConfig
from suncore.core.tariff import HHMM_PATTERN, NIGHT_WINDOW, PriceFormula, TouTariff, WeeklySchedule
from suncore.core.validation import invalid

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

    min_history_days: Annotated[int, Field(ge=1, le=365)] = 30
    load_ml_mode: Literal["off", "shadow", "auto"] = "shadow"


class ForecastConfig(_Frozen):
    """PV forecast sources (schema v2: several sources, blending, learning settings).

    Independent of the tariff so that the wizard can store the choice without prices.
    Forecast uncertainty is handled by the learned risk model and the battery section's
    ``risk`` level, not by a flat discount.
    """

    schema_version: int = 2
    sources: Annotated[list[ForecastSourceConfig], Field(max_length=8)] = Field(
        default_factory=list
    )
    learning: LearningSettings = LearningSettings()

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
}
"""All configuration sections by name and their document model (API and storage share it)."""
