# SPDX-License-Identifier: Apache-2.0
"""Plural categories and number formatting per locale (the subset the catalogue needs).

``en`` and ``de`` follow CLDR (``one`` = the number 1, else ``other``). Every other language
falls back to ``other`` only until its rules are added by hand (ADR-0016 option 4C). Decimal and
grouping separators come from CLDR for ``en`` and ``de``; unknown languages use ``en``.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

_SEPARATORS: Final[dict[str, tuple[str, str]]] = {
    "en": (".", ","),
    "de": (",", "."),
}
"""language -> (decimal separator, grouping separator)"""

_ONE_OTHER: Final = frozenset({"en", "de"})


def plural_category(language: str, value: float) -> str:
    """CLDR cardinal plural category of ``value`` (``one`` or ``other``)."""
    if language in _ONE_OTHER and float(value) == 1.0:
        return "one"
    return "other"


def format_number(
    language: str,
    value: float,
    *,
    min_fraction: int = 0,
    max_fraction: int = 3,
    min_integer: int = 1,
) -> str:
    """Format like ``Intl.NumberFormat``: grouped, half away from zero on the exact value."""
    decimal_sep, group_sep = _SEPARATORS.get(language, _SEPARATORS["en"])
    number = float(value)
    if math.isnan(number):
        return "NaN"
    if number in (float("inf"), float("-inf")):
        return "-∞" if number < 0 else "∞"
    negative = number < 0 or str(number).startswith("-")
    quantum = Decimal(1).scaleb(-max_fraction)
    rounded = abs(Decimal(number)).quantize(quantum, rounding=ROUND_HALF_UP)
    integer, _, fraction = format(rounded, "f").partition(".")
    fraction = fraction.rstrip("0").ljust(min_fraction, "0")
    integer = integer.rjust(min_integer, "0")
    if len(integer) > 3:
        head = len(integer) % 3 or 3
        parts = [integer[:head], *(integer[i : i + 3] for i in range(head, len(integer), 3))]
        integer = group_sep.join(parts)
    out = integer + (decimal_sep + fraction if fraction else "")
    return "-" + out if negative else out
