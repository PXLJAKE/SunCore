# SPDX-License-Identifier: Apache-2.0
"""Site configuration (schema v2): which entities and limits describe the installation."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# Equivalent to HA's ``valid_entity_id`` (research/device-integrations.md): lowercase
# letters and digits in domain and object id, single underscores only between
# characters, no look-arounds so the Rust regex engine behind Pydantic accepts it.
ENTITY_ID_PATTERN = r"^[a-z0-9]+(?:_[a-z0-9]+)*\.[a-z0-9]+(?:_[a-z0-9]+)*$"

EntityId = Annotated[str, StringConstraints(pattern=ENTITY_ID_PATTERN)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EntityRef(_Frozen):
    """Reference to a HA entity. ``invert_sign`` flips a source with opposite polarity."""

    entity_id: EntityId
    invert_sign: bool = False


Pct = Annotated[float, Field(ge=0, le=100)]
NonNegW = Annotated[float, Field(ge=0, le=1_000_000)]
PosW = Annotated[float, Field(gt=0, le=1_000_000)]


class PvArray(_Frozen):
    """One PV array (roof plane / inverter input). Everything but ``name`` is optional."""

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    power: EntityRef | None = None
    kwp: Annotated[float, Field(gt=0, le=10_000)] | None = None
    azimuth_deg: Annotated[float, Field(ge=0, le=360)] | None = None
    """Compass bearing of the array normal: 0 = north, 90 = east, 180 = south."""
    tilt_deg: Annotated[float, Field(ge=0, le=90)] | None = None
    inverter: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)] | None
    ) = None


class GridConfig(_Frozen):
    """Grid connection: one signed sensor (+ = import) OR an import/export pair, plus limits."""

    power: EntityRef | None = None
    import_power: EntityRef | None = None
    export_power: EntityRef | None = None
    import_limit_w: NonNegW | None = None
    export_limit_w: NonNegW | None = None
    export_limit_pct_kwp: Pct | None = None
    """Feed-in limit as percent of the installed kWp; exclusive with ``export_limit_w``."""
    para14a: bool = False
    para14a_signal: EntityRef | None = None
    energy_import: EntityRef | None = None
    """Cumulative import counter (energy entity)."""
    energy_export: EntityRef | None = None

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
        if self.export_limit_w is not None and self.export_limit_pct_kwp is not None:
            msg = "grid: `export_limit_w` and `export_limit_pct_kwp` are mutually exclusive"
            raise ValueError(msg)
        if self.para14a_signal is not None and not self.para14a:
            msg = "grid: `para14a_signal` needs `para14a`"
            raise ValueError(msg)
        for name in ("para14a_signal", "energy_import", "energy_export"):
            ref: EntityRef | None = getattr(self, name)
            if ref is not None and ref.invert_sign:
                msg = f"grid.{name}: invert_sign is meaningless here"
                raise ValueError(msg)
        return self

    @property
    def is_pair(self) -> bool:
        return self.power is None


class BatteryConfig(_Frozen):
    """Battery: power (+ = charging), state of charge in percent, limits.

    ``control`` is ``observe`` only: SunCore reads the battery; adapters that write
    set-points come in a later phase.
    """

    power: EntityRef
    soc: EntityRef
    capacity_wh: Annotated[float, Field(gt=0, le=100_000_000)] | None = None
    min_soc_pct: Pct | None = None
    max_soc_pct: Pct | None = None
    max_charge_w: PosW | None = None
    max_discharge_w: PosW | None = None
    control: Literal["observe"] = "observe"

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.soc.invert_sign:
            msg = "battery.soc: invert_sign is meaningless for a state of charge"
            raise ValueError(msg)
        lo, hi = self.min_soc_pct, self.max_soc_pct
        if lo is not None and hi is not None and not lo < hi:
            msg = f"battery: min_soc_pct ({lo}) must be below max_soc_pct ({hi})"
            raise ValueError(msg)
        return self


class SiteConfig(_Frozen):
    """Site description. Entity ids come from the user; none are hard-coded."""

    schema_version: int = 2
    grid: GridConfig
    pv: list[PvArray] = Field(default_factory=list)
    battery: BatteryConfig | None = None
    house: EntityRef | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        seen: set[str] = set()
        for ref in self.entity_refs():
            if ref.entity_id in seen:
                msg = f"entity {ref.entity_id} is used more than once"
                raise ValueError(msg)
            seen.add(ref.entity_id)
        names = [a.name for a in self.pv]
        if len(set(names)) != len(names):
            msg = "pv: array names must be unique"
            raise ValueError(msg)
        if self.grid.export_limit_pct_kwp is not None and self.total_kwp is None:
            msg = "grid: `export_limit_pct_kwp` needs `kwp` on every PV array"
            raise ValueError(msg)
        return self

    @property
    def total_kwp(self) -> float | None:
        """Sum of all array sizes; ``None`` if there is no array or one has no ``kwp``."""
        sizes = [a.kwp for a in self.pv]
        if not sizes or any(k is None for k in sizes):
            return None
        return float(sum(k for k in sizes if k is not None))

    @property
    def pv_power_refs(self) -> list[EntityRef]:
        """Power entities of the arrays that have one (summed for the PV flow)."""
        return [a.power for a in self.pv if a.power is not None]

    def entity_refs(self) -> list[EntityRef]:
        """All mapped entity references (for subscriptions)."""
        refs: list[EntityRef] = []
        g = self.grid
        refs.extend(
            r
            for r in (
                g.power,
                g.import_power,
                g.export_power,
                g.para14a_signal,
                g.energy_import,
                g.energy_export,
            )
            if r is not None
        )
        refs.extend(self.pv_power_refs)
        if self.battery is not None:
            refs.extend((self.battery.power, self.battery.soc))
        if self.house is not None:
            refs.append(self.house)
        return refs


def migrate_site_v1_to_v2(body: dict[str, Any]) -> dict[str, Any]:
    """v1 ``pv: [EntityRef]`` becomes one :class:`PvArray` per reference."""
    pv = body.get("pv") or []
    if not isinstance(pv, list):
        msg = "site v1: `pv` must be a list"
        raise TypeError(msg)
    # Decision: "PV n" is language-neutral data, not a UI string: it is stored, must be unique
    # per site and the UI shows the stored name, so it is not run through the i18n catalogue.
    body["pv"] = [{"name": f"PV {i}", "power": ref} for i, ref in enumerate(pv, start=1)]
    return body
