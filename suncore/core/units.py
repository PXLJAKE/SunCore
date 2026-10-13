# SPDX-License-Identifier: Apache-2.0
"""Units, conversion to the SI base used inside SunCore, and sign helpers.

Internal base units are W, Wh and percent (ADR-0011). Sign convention, fixed
for all of ``core/``: grid + = import, battery + = charging, PV + = generation,
device + = consumption. Adapters convert at the edge; nothing else flips signs.
"""

from __future__ import annotations

from enum import StrEnum


class Dimension(StrEnum):
    """Physical dimension of a unit."""

    POWER = "power"
    ENERGY = "energy"
    RATIO = "ratio"


class Unit(StrEnum):
    """Supported units; the value is the Home Assistant ``unit_of_measurement``."""

    W = "W"
    KW = "kW"
    WH = "Wh"
    KWH = "kWh"
    PERCENT = "%"

    @property
    def dimension(self) -> Dimension:
        return _DIMENSION[self]

    @property
    def base(self) -> Unit:
        """The base unit of this unit's dimension (W, Wh or %)."""
        return _BASE[self.dimension]

    @property
    def factor(self) -> float:
        """Multiplier converting a value in this unit to its base unit."""
        return _FACTOR[self]


_DIMENSION: dict[Unit, Dimension] = {
    Unit.W: Dimension.POWER,
    Unit.KW: Dimension.POWER,
    Unit.WH: Dimension.ENERGY,
    Unit.KWH: Dimension.ENERGY,
    Unit.PERCENT: Dimension.RATIO,
}
_BASE: dict[Dimension, Unit] = {
    Dimension.POWER: Unit.W,
    Dimension.ENERGY: Unit.WH,
    Dimension.RATIO: Unit.PERCENT,
}
_FACTOR: dict[Unit, float] = {
    Unit.W: 1.0,
    Unit.KW: 1000.0,
    Unit.WH: 1.0,
    Unit.KWH: 1000.0,
    Unit.PERCENT: 1.0,
}


def parse_unit(text: str | None) -> Unit | None:
    """Parse a HA ``unit_of_measurement`` string; unknown or missing -> ``None``.

    Matching is exact (case-sensitive: ``kW`` is not ``KW``, ``mW`` is not
    ``MW``). Surrounding whitespace is ignored. We never guess.
    """
    if text is None:
        return None
    try:
        return Unit(text.strip())
    except ValueError:
        return None


def to_base(value: float, unit: Unit) -> float:
    """Convert ``value`` in ``unit`` to the base unit (W, Wh or %)."""
    return value * unit.factor


def from_base(value: float, unit: Unit) -> float:
    """Convert a base-unit ``value`` into ``unit`` (inverse of :func:`to_base`)."""
    return value / unit.factor


def convert(value: float, source: Unit, target: Unit) -> float:
    """Convert between two units of the same dimension.

    Raises:
        ValueError: if the dimensions differ.
    """
    if source.dimension is not target.dimension:
        msg = f"cannot convert {source.value} to {target.value}: different dimensions"
        raise ValueError(msg)
    return from_base(to_base(value, source), target)


# --- sign helpers -----------------------------------------------------------


def apply_invert(value: float, invert_sign: bool) -> float:
    """Flip the sign of a raw reading whose source uses the opposite convention."""
    return -value if invert_sign else value


def net_grid_w(import_w: float, export_w: float) -> float:
    """Signed grid power from separate import and export magnitudes (+ = import)."""
    return import_w - export_w


def is_importing(grid_w: float) -> bool:
    return grid_w > 0


def is_exporting(grid_w: float) -> bool:
    return grid_w < 0


def is_charging(battery_w: float) -> bool:
    return battery_w > 0


def is_discharging(battery_w: float) -> bool:
    return battery_w < 0


def house_load_w(pv_w: float, grid_w: float, battery_w: float) -> float:
    """House consumption from the power balance: ``pv + grid - battery``.

    PV and grid import supply the house and the battery charge; so
    ``pv + grid == house + battery`` with the core sign convention.
    """
    return pv_w + grid_w - battery_w
