# SPDX-License-Identifier: Apache-2.0
"""Configuration sections besides ``site``: region, tariff, devices, goals, operation, wizard.

Pure, frozen models; storage and API only move them around. ``operation`` is *stored*
but never read by the safety gate before Phase 3: the gate stays shadow-only.
"""

from __future__ import annotations

import itertools
from typing import Annotated, Final, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from suncore.core.decisions import OperatingMode
from suncore.core.devices import DeviceConfig, DeviceId, RoleName, TemplateId
from suncore.core.site import EntityRef

REGION_ID_PATTERN: Final = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$"
"""Region id: ``de`` or ``<country>.<variant>``; also the region template id pattern."""
STEP_PATTERN: Final = r"^[a-z][a-z0-9_]{0,47}$"
TIME_PATTERN: Final = r"^([01]\d|2[0-3]):[0-5]\d$"

Price = Annotated[float, Field(ge=0, le=100)]
"""Currency per kWh."""


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
            msg = f"unknown IANA time zone {value!r}"
            raise ValueError(msg) from exc
        return value


# --- tariff -------------------------------------------------------------------


class ImportTariff(_Frozen):
    """How the price of imported energy is obtained."""

    kind: Literal["fixed", "dynamic"]
    template_id: TemplateId | None = None
    price_entity: EntityRef | None = None
    fixed_price: Price | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.kind == "fixed":
            if self.fixed_price is None:
                msg = "tariff.import: kind `fixed` needs `fixed_price`"
                raise ValueError(msg)
            if self.template_id is not None or self.price_entity is not None:
                msg = "tariff.import: kind `fixed` takes no `template_id`/`price_entity`"
                raise ValueError(msg)
        elif self.template_id is None and self.price_entity is None:
            msg = "tariff.import: kind `dynamic` needs `template_id` or `price_entity`"
            raise ValueError(msg)
        elif self.fixed_price is not None:
            msg = "tariff.import: kind `dynamic` takes no `fixed_price`"
            raise ValueError(msg)
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
                msg = "tariff.export: kind `fixed` needs `fixed_price` and no tiers"
                raise ValueError(msg)
        elif self.kind == "tiered":
            if self.fixed_price is not None or not self.tiers:
                msg = "tariff.export: kind `tiered` needs tiers and no `fixed_price`"
                raise ValueError(msg)
            bounds = [t.up_to_kwp for t in self.tiers]
            if None in bounds[:-1]:
                msg = "tariff.export: only the last tier may be unbounded"
                raise ValueError(msg)
            finite = [b for b in bounds if b is not None]
            if any(a >= b for a, b in itertools.pairwise(finite)):
                msg = "tariff.export: tier bounds must be strictly increasing"
                raise ValueError(msg)
        elif self.fixed_price is not None or self.tiers:
            msg = "tariff.export: kind `none` takes no price or tiers"
            raise ValueError(msg)
        return self


class TariffConfig(_Frozen):
    """Import/export prices and the forecast sources."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)
    """``import`` is a Python keyword: the JSON key is ``import``, ``import_`` is accepted too.

    Always dump with ``by_alias=True`` (ConfigStore and the API do); documents stored
    before the alias existed (key ``import_``) still load.
    """

    schema_version: int = 1
    import_: ImportTariff = Field(alias="import")
    export: ExportTariff
    forecast_template_ids: list[TemplateId] = Field(default_factory=list)
    forecast_entities: dict[RoleName, EntityRef] = Field(default_factory=dict)
    """Forecast role (e.g. ``today``, ``tomorrow``) to entity."""

    @model_validator(mode="after")
    def _unique_templates(self) -> Self:
        if len(set(self.forecast_template_ids)) != len(self.forecast_template_ids):
            msg = "tariff: forecast_template_ids must be unique"
            raise ValueError(msg)
        return self

    def entity_refs(self) -> list[EntityRef]:
        refs = list(self.forecast_entities.values())
        if self.import_.price_entity is not None:
            refs.append(self.import_.price_entity)
        return refs


# --- devices ------------------------------------------------------------------


class DevicesConfig(_Frozen):
    """All configured devices."""

    schema_version: int = 1
    devices: list[DeviceConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [d.id for d in self.devices]
        if len(set(ids)) != len(ids):
            msg = "devices: ids must be unique"
            raise ValueError(msg)
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
    schema_version: int = 1
    battery_target: BatteryTarget | None = None
    climate_before_export: bool = False
    grid_charge_night_only: bool = True


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
