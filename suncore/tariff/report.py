# SPDX-License-Identifier: Apache-2.0
"""What ``GET /api/v1/prices`` returns: the planning prices with source and freshness.

Pydantic models are the schema (OpenAPI is generated from them). Error ``key`` values are
catalogue keys (``tariff.error.*``); ``args`` are their ICU arguments.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from suncore.core.series import PriceIncludes

SourceKindOut = Literal["dynamic", "fixed", "tou"]
FreshnessStatus = Literal["fresh", "stale", "missing", "static"]
ExportKind = Literal["fixed", "tiered", "none"]
ErrorArg = str | float | int | list[str]


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PriceSlot(_Out):
    """One 15-minute interval; a price is ``null`` where no source value covers it."""

    start: AwareDatetime
    import_price: float | None
    """End price of imported energy, currency/kWh (may be negative)."""
    export_price: float | None
    """Remuneration of exported energy, currency/kWh (0 when the negative-price rule applies)."""


class PriceErrorOut(_Out):
    """Why the last refresh failed, as a catalogue key."""

    key: str
    args: dict[str, ErrorArg] = Field(default_factory=dict)


class PriceSourceInfo(_Out):
    import_source_id: str
    """Template id (``tibber.price``), ``fixed`` or ``tou``."""
    import_kind: SourceKindOut
    source_price_includes: PriceIncludes
    """What the source delivers: the bare spot price or the all-in end price."""
    price_includes: PriceIncludes
    """What ``import_price`` is: ``all_in`` once the formula was applied."""
    formula_applied: bool
    export_kind: ExportKind
    negative_price_rule: str
    """Region template value: ``none``, ``no_remuneration`` or ``curtailment_required``."""
    export_zeroed_slots: Annotated[int, Field(ge=0)]
    """Slots whose export price was set to 0 by the negative-price rule."""
    native_step_min: int | None
    """Finest native resolution of the source (15 or 60), ``null`` for fixed prices."""
    currency: str


class PriceFreshness(_Out):
    status: FreshnessStatus
    fetched_at: AwareDatetime | None
    age_s: float | None
    known_until: AwareDatetime | None
    """End of the last interval the source has a price for."""
    next_refresh_at: AwareDatetime | None
    last_error: PriceErrorOut | None


class PriceQuality(_Out):
    slots: Annotated[int, Field(ge=0)]
    known_slots: Annotated[int, Field(ge=0)]
    has_tomorrow: bool
    """Prices cover the whole next local day."""
    dropped_points: Annotated[int, Field(ge=0)]
    """Points of the last fetch that were unreadable, duplicated or overlapping."""
    carried_slots: Annotated[int, Field(ge=0)] = 0
    """Slots the latest fetch left unknown and an earlier fetch supplied (degraded data)."""


class PriceReport(_Out):
    unit: Literal["currency/kWh"] = "currency/kWh"
    step_min: Literal[15] = 15
    start: AwareDatetime
    slots: list[PriceSlot]
    source: PriceSourceInfo
    freshness: PriceFreshness
    quality: PriceQuality
