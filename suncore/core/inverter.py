# SPDX-License-Identifier: Apache-2.0
"""The inverter limit as a controlled asset (ADR-0018 decision 3; PR 6-G): pure helpers.

One asset ``inverter`` takes an :class:`~suncore.core.decisions.InverterLimitAction`: either an
export limit in W (capability ``export_limit``) or an AC output limit in % of the nominal AC
power (capability ``ac_limit``, Kostal register 533). Controller, gate and adapters share the
conversions here so they cannot drift:

* :func:`ac_limit_w` - the AC output a limit allows (``None`` for an export limit);
* :func:`export_cap_w` - the export a limit allows at a given house load: the export limit
  itself, or ``AC limit - house load`` (export = AC output - house);
* :func:`same_limit` - two limits equal within the adapter's resolution (write on change, X4).

Signs and units: W and %, export is a magnitude (>= 0); house load is consumption (+).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Literal

from suncore.core.decisions import InverterLimitAction

INVERTER_ASSET_ID: Final = "inverter"
"""The asset id of the inverter limit (never a device id: the composition root refuses one)."""

LimitCapability = Literal["export_limit", "ac_limit"]
InverterDriverKind = Literal["generic_number", "kostal_rest", "kostal_modbus"]


@dataclass(frozen=True, slots=True)
class InverterAdapterSpec:
    """An ``inverter`` template as the adapters see it (``suncore.devices`` imports no template).

    ``capability`` ``None`` = the configuration chooses (the generic number takes W or %).
    ``min``/``max`` bound the writable value in the capability's unit (W or %); the entity's own
    ``min``/``max`` attributes narrow it further. ``volatile``: the value is lost on an inverter
    reset (Kostal register 533) and is re-written every ``InverterModbusLink.heartbeat_s``.
    ``register`` / ``default_pct``: the modbus register and the inverter's default (release
    target when nothing was read at takeover).
    """

    template_id: str
    driver: InverterDriverKind
    capability: LimitCapability | None = None
    min: float = 0.0
    max: float | None = None
    settle_s: float = 10.0
    volatile: bool = False
    register: int | None = None
    default_pct: float = 100.0


EXPORT_TOLERANCE_W: Final = 1.0
"""Two export limits (W) closer than this are the same limit."""
PCT_TOLERANCE: Final = 0.5
"""Two AC limits (%) closer than this are the same limit (register 533 takes whole percent)."""
NO_IMPORT_MARGIN_W: Final = 500.0
"""X2: an AC limit keeps at least this much above house load plus battery charging."""


def ac_limit_w(action: InverterLimitAction, nominal_ac_w: float | None) -> float | None:
    """The AC output an ``ac_limit`` allows (``None`` for an export limit or without nominal)."""
    if action.max_ac_pct is None or nominal_ac_w is None:
        return None
    return action.max_ac_pct / 100.0 * nominal_ac_w


def export_cap_w(
    action: InverterLimitAction, nominal_ac_w: float | None, house_w: float | None
) -> float | None:
    """The export the limit allows; ``None`` when it cannot be computed (unknown house load)."""
    if action.max_export_w is not None:
        return action.max_export_w
    ac = ac_limit_w(action, nominal_ac_w)
    if ac is None or house_w is None:
        return None
    return max(0.0, ac - house_w)


def capability_of(action: InverterLimitAction) -> LimitCapability:
    return "export_limit" if action.max_export_w is not None else "ac_limit"


def same_limit(a: InverterLimitAction | None, b: InverterLimitAction | None) -> bool:
    """Equal within the resolution of the limit (both ``None`` is equal)."""
    if a is None or b is None:
        return a is None and b is None
    if a.max_export_w is not None and b.max_export_w is not None:
        return abs(a.max_export_w - b.max_export_w) <= EXPORT_TOLERANCE_W
    if a.max_ac_pct is not None and b.max_ac_pct is not None:
        return abs(a.max_ac_pct - b.max_ac_pct) <= PCT_TOLERANCE
    return False


def pct_for_ac_w(ac_w: float, nominal_ac_w: float) -> float:
    """The smallest whole percent that allows at least ``ac_w`` (rounded **up**, max 100)."""
    if nominal_ac_w <= 0:
        msg = "nominal_ac_w must be positive"
        raise ValueError(msg)
    return float(min(100, max(0, math.ceil(ac_w / nominal_ac_w * 100.0 - 1e-9))))


def looser(a: InverterLimitAction, b: InverterLimitAction) -> bool:
    """``a`` allows more than ``b`` (same capability; different capabilities: ``False``)."""
    if a.max_export_w is not None and b.max_export_w is not None:
        return a.max_export_w > b.max_export_w + EXPORT_TOLERANCE_W
    if a.max_ac_pct is not None and b.max_ac_pct is not None:
        return a.max_ac_pct > b.max_ac_pct + PCT_TOLERANCE
    return False


__all__ = [
    "EXPORT_TOLERANCE_W",
    "INVERTER_ASSET_ID",
    "NO_IMPORT_MARGIN_W",
    "PCT_TOLERANCE",
    "InverterAdapterSpec",
    "InverterDriverKind",
    "LimitCapability",
    "ac_limit_w",
    "capability_of",
    "export_cap_w",
    "looser",
    "pct_for_ac_w",
    "same_limit",
]
