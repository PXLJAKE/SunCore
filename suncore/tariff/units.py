# SPDX-License-Identifier: Apache-2.0
"""Price units of integrations -> SunCore's ``currency/kWh``.

Documented additive alias (Phase 4 WP4): EPEX Spot labels its sensors with the *symbol* of the
currency (``"€/kWh"``, ``"£/kWh"``; ``custom_components/epex_spot/const.py:57``, tag ``4.1.0``)
while the template schema's price unit is ``<ISO code>/kWh|MWh``. The template keeps
``{currency}/kWh`` as the unit of the *values* and lists the literal symbols in its matcher
(``units``); :func:`normalise_price_unit` maps the symbol to the ISO code wherever an entity's
unit is compared with a template's (conformance test, wizard).
"""

from __future__ import annotations

from typing import Final

from suncore.templates.models import price_unit_factor

CURRENCY_SYMBOLS: Final[dict[str, str]] = {"€": "EUR", "£": "GBP"}
"""Currency symbols that integrations put in front of ``/kWh`` (EPEX Spot: EUR and GBP)."""


def normalise_price_unit(unit: str) -> str:
    """``"€/kWh"`` -> ``"EUR/kWh"``; any other unit is returned unchanged."""
    symbol, sep, rest = unit.strip().partition("/")
    return f"{CURRENCY_SYMBOLS[symbol]}{sep}{rest}" if symbol in CURRENCY_SYMBOLS else unit.strip()


def resolve_value_unit(unit: str, currency: str) -> str:
    """The template's ``{currency}`` placeholder replaced by the region's ISO code."""
    return unit.replace("{currency}", currency)


def divisor_to_kwh(unit: str) -> float:
    """What to divide a price in ``unit`` by to get currency/kWh (``EUR/MWh`` -> 1000).

    Division (not multiplication by 0.001) keeps ``45.5 / 1000`` the exact nearest double.
    Raises ``ValueError`` for a unit that is not ``<currency>/kWh|MWh``.
    """
    return float(round(1 / price_unit_factor(unit)))


def energy_scale(unit: str) -> str | None:
    """``"kWh"`` or ``"MWh"`` of a ``<currency>/<scale>`` unit, else ``None``."""
    _, _, scale = unit.partition("/")
    return scale if scale in ("kWh", "MWh") else None
