# SPDX-License-Identifier: Apache-2.0
"""Price formula on spot sources, the export side and the negative-price rule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from suncore.core.config import ExportTariff, ExportTier
from suncore.core.series import Grid, PriceSeries, SeriesUnit, TimeSeries
from suncore.core.tariff import GridFeeWindow, PriceFormula, TouWindow
from suncore.tariff import (
    ExportConfigError,
    ExportTiersError,
    apply_import_formula,
    export_base_price,
    planning_prices,
)
from suncore.templates.models import NegativePriceRule
from tests.tariff.helpers import BERLIN, prices

T0 = datetime(2026, 10, 16, 0, 0, tzinfo=BERLIN).astimezone(UTC)
Q = timedelta(minutes=15)
H = timedelta(hours=1)
RULE = NegativePriceRule.NO_REMUNERATION


def grid(count: int, start: datetime = T0) -> Grid:
    return Grid(start=start, step=Q, count=count)


def run(
    segments: list[PriceSeries],
    count: int,
    *,
    formula: PriceFormula | None = None,
    export: float = 0.08,
    rule: NegativePriceRule = RULE,
) -> tuple[PriceSeries, int]:
    return planning_prices(
        segments, grid(count), formula=formula, export_price=export, rule=rule, tz=BERLIN
    )


# --- formula ----------------------------------------------------------------------------

FORMULA = PriceFormula(grid_fee=0.10, levies=0.05, margin=0.02, vat_pct=19)


def test_a_spot_source_gets_the_formula_at_the_slot_start() -> None:
    spot = prices(T0, [0.10, 0.20, None, -0.05], includes="spot_only").import_price
    end, includes = apply_import_formula(spot, "spot_only", FORMULA, BERLIN)
    assert includes == "all_in"
    assert end.values[0] == pytest.approx((0.10 + 0.10 + 0.05 + 0.02) * 1.19)
    assert end.values[2] is None
    assert end.values[3] == pytest.approx((-0.05 + 0.17) * 1.19)


def test_an_all_in_source_never_gets_the_formula_even_if_one_is_configured() -> None:
    series = prices(T0, [0.30, 0.31], includes="all_in").import_price
    end, includes = apply_import_formula(series, "all_in", FORMULA, BERLIN)
    assert end is series
    assert includes == "all_in"


def test_a_spot_source_without_a_formula_stays_spot_only() -> None:
    series = prices(T0, [0.10], includes="spot_only").import_price
    end, includes = apply_import_formula(series, "spot_only", None, BERLIN)
    assert end is series
    assert includes == "spot_only"


def test_time_variable_grid_fee_is_judged_in_local_time() -> None:
    formula = PriceFormula(
        grid_fee=0.10,
        grid_fee_windows=(GridFeeWindow(window=TouWindow(start="22:00", end="06:00"), fee=0.03),),
    )
    # 21:45 local (CEST) = 19:45Z, 22:00 local = 20:00Z
    start = datetime(2026, 10, 16, 21, 45, tzinfo=BERLIN)
    spot = prices(start, [0.0, 0.0], includes="spot_only").import_price
    end, _ = apply_import_formula(spot, "spot_only", formula, BERLIN)
    assert end.values == pytest.approx((0.10, 0.03))


# --- export side ------------------------------------------------------------------------


def test_export_fixed_none_and_tiered() -> None:
    assert export_base_price(ExportTariff(kind="fixed", fixed_price=0.081), None) == 0.081
    assert export_base_price(ExportTariff(kind="none"), None) == 0.0
    tiered = ExportTariff(
        kind="tiered",
        tiers=(ExportTier(up_to_kwp=10, price=0.08), ExportTier(price=0.07)),
    )
    assert export_base_price(tiered, 17.0) == pytest.approx((10 * 0.08 + 7 * 0.07) / 17)
    assert export_base_price(tiered, 8.0) == pytest.approx(0.08)


def test_tiered_export_needs_the_plant_size_and_tiers_that_cover_it() -> None:
    tiered = ExportTariff(kind="tiered", tiers=(ExportTier(up_to_kwp=10, price=0.08),))
    with pytest.raises(ExportConfigError):
        export_base_price(tiered, None)
    with pytest.raises(ExportTiersError) as err:
        export_base_price(tiered, 17.0)  # the tiers end at 10 kWp: no rate is guessed
    assert err.value.key == "tariff.error.export_tiers"
    assert err.value.message_args == {"kwp": 17.0}
    assert isinstance(err.value, ExportConfigError)


# --- planning prices --------------------------------------------------------------------


def test_negative_spot_price_zeroes_export_before_the_formula_hides_it() -> None:
    """Spot -0.02 becomes a positive end price (grid fee, levies) but the grid operator pays
    nothing in that slot: the rule looks at the spot price of a spot-only source."""
    series = prices(T0, [0.10, -0.02, 0.0, -0.001], includes="spot_only")
    out, zeroed = run([series], 4, formula=FORMULA)
    assert out.price_includes == "all_in"
    assert out.import_price.values[1] == pytest.approx((-0.02 + 0.17) * 1.19)
    assert out.import_price.values[1] > 0
    assert out.export_price is not None
    assert out.export_price.values == (0.08, 0.0, 0.08, 0.0)
    assert zeroed == 2


def test_an_all_in_source_uses_the_import_price_as_the_negative_proxy() -> None:
    series = prices(T0, [0.25, -0.01, 0.0], includes="all_in")
    out, zeroed = run([series], 3)
    assert out.import_price.values == (0.25, -0.01, 0.0)
    assert out.export_price is not None
    assert out.export_price.values == (0.08, 0.0, 0.08)  # zero is not negative
    assert zeroed == 1


def test_without_the_rule_export_keeps_its_price_at_negative_prices() -> None:
    series = prices(T0, [-0.05, 0.1], includes="spot_only")
    for rule in (NegativePriceRule.NONE, NegativePriceRule.CURTAILMENT_REQUIRED):
        out, zeroed = run([series], 2, rule=rule)
        assert out.export_price is not None
        assert out.export_price.values == (0.08, 0.08)
        assert zeroed == 0


def test_unknown_prices_stay_unknown_in_both_series() -> None:
    series = prices(T0, [0.2, None, 0.2], includes="all_in")
    out, _ = run([series], 5)
    assert out.import_price.values == (0.2, None, 0.2, None, None)
    assert out.export_price is not None
    assert out.export_price.values == (0.08, None, 0.08, None, None)


def test_mixed_15_and_60_minute_segments_are_held_onto_the_grid() -> None:
    hourly = prices(T0, [0.30, -0.10], step=H, includes="all_in")
    quarter = prices(T0 + 2 * H, [0.20, 0.21], includes="all_in")
    out, zeroed = run([hourly, quarter], 10)
    assert out.import_price.values == (0.3,) * 4 + (-0.1,) * 4 + (0.2, 0.21)
    assert zeroed == 4
    assert out.source_id == "t"


def test_a_source_with_its_own_export_prices_wins() -> None:
    imp = prices(T0, [0.3, -0.2]).import_price
    own = TimeSeries(start=T0, step=Q, values=(0.05, 0.04), unit=SeriesUnit.PRICE)
    out, zeroed = run(
        [PriceSeries(source_id="s", price_includes="all_in", import_price=imp, export_price=own)], 2
    )
    assert out.export_price is not None
    assert out.export_price.values == (0.05, 0.04)  # the source decides, the rule is not applied
    assert zeroed == 0


def test_no_segments_gives_unknown_prices_not_zeros() -> None:
    out, zeroed = planning_prices(
        [], grid(3), formula=None, export_price=0.08, rule=RULE, tz=BERLIN, source_id="x"
    )
    assert out.source_id == "x"
    assert out.import_price.values == (None, None, None)
    assert out.export_price is not None
    assert out.export_price.values == (None, None, None)
    assert zeroed == 0


def test_export_none_means_zero_not_unknown() -> None:
    out, _ = run(
        [prices(T0, [0.3, 0.3])], 2, export=export_base_price(ExportTariff(kind="none"), None)
    )
    assert out.export_price is not None
    assert out.export_price.values == (0.0, 0.0)
