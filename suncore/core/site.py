# SPDX-License-Identifier: Apache-2.0
"""Minimal site configuration (Phase 1): which entities describe the energy flows."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# Stricter than HA's VALID_ENTITY_ID on purpose (no digits in the domain).
ENTITY_ID_PATTERN = r"^[a-z_]+\.[a-z0-9_]+$"

EntityId = Annotated[str, StringConstraints(pattern=ENTITY_ID_PATTERN)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EntityRef(_Frozen):
    """Reference to a HA entity. ``invert_sign`` flips a source with opposite polarity."""

    entity_id: EntityId
    invert_sign: bool = False


class GridConfig(_Frozen):
    """Grid connection: one signed sensor (+ = import) OR an import/export pair."""

    power: EntityRef | None = None
    import_power: EntityRef | None = None
    export_power: EntityRef | None = None

    @model_validator(mode="after")
    def _exactly_one_variant(self) -> Self:
        pair = (self.import_power, self.export_power)
        has_pair = any(p is not None for p in pair)
        if self.power is not None and has_pair:
            msg = "grid: configure either `power` or `import_power`+`export_power`, not both"
            raise ValueError(msg)
        if self.power is None and not has_pair:
            msg = "grid: configure `power` or `import_power`+`export_power`"
            raise ValueError(msg)
        if has_pair and None in pair:
            msg = "grid: `import_power` and `export_power` must be given together"
            raise ValueError(msg)
        return self

    @property
    def is_pair(self) -> bool:
        return self.power is None


class BatteryConfig(_Frozen):
    """Battery: power (+ = charging) and state of charge in percent."""

    power: EntityRef
    soc: EntityRef

    @model_validator(mode="after")
    def _soc_not_inverted(self) -> Self:
        if self.soc.invert_sign:
            msg = "battery.soc: invert_sign is meaningless for a state of charge"
            raise ValueError(msg)
        return self


class SiteConfig(_Frozen):
    """Site description. Entity ids come from the user; none are hard-coded."""

    schema_version: int = 1
    grid: GridConfig
    pv: list[EntityRef] = Field(default_factory=list)
    battery: BatteryConfig | None = None
    house: EntityRef | None = None

    @model_validator(mode="after")
    def _entities_unique(self) -> Self:
        seen: set[str] = set()
        for ref in self.entity_refs():
            if ref.entity_id in seen:
                msg = f"entity {ref.entity_id} is used more than once"
                raise ValueError(msg)
            seen.add(ref.entity_id)
        return self

    def entity_refs(self) -> list[EntityRef]:
        """All configured entity references (for subscriptions)."""
        refs: list[EntityRef] = []
        g = self.grid
        refs.extend(r for r in (g.power, g.import_power, g.export_power) if r is not None)
        refs.extend(self.pv)
        if self.battery is not None:
            refs.extend((self.battery.power, self.battery.soc))
        if self.house is not None:
            refs.append(self.house)
        return refs
