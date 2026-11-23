# SPDX-License-Identifier: Apache-2.0
"""From source prices to the planning prices: formula, export side, negative-price rule.

Pure functions on :class:`~suncore.core.series.PriceSeries` and the contracts of
``core/tariff.py``; the service only feeds them.

**Import.** A ``spot_only`` source goes through :meth:`PriceFormula.end_price` at the slot's
start instant, in the site's zone (``end_price`` is the 4-A ``apply``: the boundary test
reserves the call ``.apply(`` for the actuator write path). An ``all_in`` source is never
touched, even if a formula is configured (the API refuses that combination, this is the second
line of defence).

**Export.** ``fixed``: the fixed price; ``tiered``: :func:`blended_export_price` with the
site's total kWp (the tiers must cover it, else :class:`ExportConfigError`); ``none``: 0.
A source that brings its own export prices (none of the known integrations does) wins over the
configuration.

**Negative-price rule** (region template ``negative_price_rule``):

* ``no_remuneration`` (``de``): in every slot whose *spot* price is negative, the export price
  is 0 - the grid operator pays nothing then, so the planner must see that exporting earns
  nothing and avoid it (curtail, store, consume). The spot price is the source price of a
  ``spot_only`` source (before the formula). For an ``all_in`` source (Tibber) the spot price
  is not known; the **import end price** is used as the proxy, which only marks slots where
  the end price itself is negative, i.e. fewer slots than the true negative-spot slots
  (UNVERIFIED approximation: the planner may see a remuneration in a slot with a slightly
  negative spot price). A slot whose price is unknown gets an unknown export price.
* ``curtailment_required`` and ``none``: prices are not changed here (curtailment is a
  planner/controller matter, not a price).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import tzinfo
from typing import Final

from suncore.core.config import ExportTariff
from suncore.core.series import (
    Grid,
    PriceIncludes,
    PriceSeries,
    SeriesUnit,
    TimeSeries,
    resample_prices,
)
from suncore.core.tariff import PriceFormula, blended_export_price
from suncore.tariff.errors import ExportConfigError, ExportTiersError
from suncore.templates.models import NegativePriceRule

NEGATIVE_EXPORT_PRICE: Final = 0.0
"""Export price (currency/kWh) in a slot without remuneration."""


def export_base_price(tariff: ExportTariff, total_kwp: float | None) -> float:
    """The export price per kWh the configuration gives (``none`` -> 0).

    Raises :class:`ExportConfigError` for ``tiered`` without a known plant size or with tiers
    that do not cover it (SunCore never guesses a rate).
    """
    if tariff.kind == "none":
        return 0.0
    if tariff.kind == "fixed":
        assert tariff.fixed_price is not None
        return tariff.fixed_price
    if total_kwp is None:
        raise ExportConfigError("tiered export needs `kwp` on every PV array")
    try:
        return blended_export_price(tariff.tiers, total_kwp)
    except ValueError as exc:
        raise ExportTiersError(str(exc), kwp=total_kwp) from exc


def apply_import_formula(
    spot: TimeSeries,
    includes: PriceIncludes,
    formula: PriceFormula | None,
    tz: tzinfo,
) -> tuple[TimeSeries, PriceIncludes]:
    """End prices for a resampled source series, and the ``price_includes`` of the result.

    ``spot_only`` + formula -> ``all_in``; ``spot_only`` without formula stays ``spot_only``
    (the caller reports it); ``all_in`` is returned unchanged.
    """
    if includes == "all_in" or formula is None:
        return spot, includes
    values = tuple(
        None if v is None else formula.end_price(v, spot.slot_start(i), tz)
        for i, v in enumerate(spot.values)
    )
    return spot.model_copy(update={"values": values}), "all_in"


def planning_prices(
    segments: Sequence[PriceSeries],
    grid: Grid,
    *,
    formula: PriceFormula | None,
    export_price: float,
    rule: NegativePriceRule,
    tz: tzinfo,
    source_id: str | None = None,
) -> tuple[PriceSeries, int]:
    """Import and export prices on ``grid`` and how many slots the negative-price rule zeroed.

    ``segments`` are one source's segments (any mix of 15/60 minutes). Slots no source value
    covers are ``None`` in both series.
    """
    if not segments:
        empty = TimeSeries(
            start=grid.start,
            step=grid.step,
            values=(None,) * grid.count,
            unit=SeriesUnit.PRICE,
        )
        return (
            PriceSeries(
                source_id=source_id or "none",
                price_includes="all_in",
                import_price=empty,
                export_price=empty,
            ),
            0,
        )
    resampled = resample_prices(list(segments), grid)
    spot = resampled.import_price
    end, includes = apply_import_formula(spot, resampled.price_includes, formula, tz)
    zeroed = 0
    export_values: list[float | None] = []
    for i, end_price in enumerate(end.values):
        if resampled.export_price is not None:
            export_values.append(resampled.export_price.values[i])
            continue
        reference = spot.values[i] if resampled.price_includes == "spot_only" else end_price
        if end_price is None or reference is None:
            export_values.append(None)
        elif rule is NegativePriceRule.NO_REMUNERATION and reference < 0:
            export_values.append(NEGATIVE_EXPORT_PRICE)
            zeroed += 1
        else:
            export_values.append(export_price)
    export = TimeSeries(
        start=end.start, step=end.step, values=tuple(export_values), unit=SeriesUnit.PRICE
    )
    return (
        PriceSeries(
            source_id=source_id or resampled.source_id,
            price_includes=includes,
            import_price=end,
            export_price=export,
        ),
        zeroed,
    )
