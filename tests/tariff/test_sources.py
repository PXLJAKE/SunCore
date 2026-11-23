# SPDX-License-Identifier: Apache-2.0
"""Fixed and HT/NT sources, the unit helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from suncore.core.series import PLANNING_STEP, TimeRange
from suncore.core.tariff import TouPeriod, TouTariff, TouWindow
from suncore.tariff import FixedPriceSource, TouPriceSource
from suncore.tariff.units import (
    divisor_to_kwh,
    energy_scale,
    normalise_price_unit,
    resolve_value_unit,
)
from tests.tariff.helpers import BERLIN, NOW

H = timedelta(hours=1)


def rng(start: datetime, hours: float) -> TimeRange:
    return TimeRange(start=start, end=start + timedelta(hours=hours))


async def test_fixed_price_is_constant_all_in_on_the_planning_grid() -> None:
    source = FixedPriceSource(price=0.31)
    data = await source.fetch_segments(rng(NOW, 24), NOW)
    (segment,) = data.segments
    assert segment.price_includes == "all_in"
    assert segment.step == PLANNING_STEP
    assert segment.start == datetime(2026, 10, 16, 8, 0, tzinfo=UTC)  # 10:07 local floors to 10:00
    assert len(segment.import_price) == 96 + 1  # the partial first quarter hour is included
    assert set(segment.import_price.values) == {0.31}
    assert source.kind == "fixed"
    assert not source.uses_action
    assert source.watched_entities == ()
    assert (await source.fetch(rng(NOW, 2))).import_price.values[0] == 0.31


TOU = TouTariff(
    base_price=0.35,
    periods=(TouPeriod(window=TouWindow(start="22:00", end="06:00"), price=0.25),),
)


async def test_tou_prices_follow_the_local_windows() -> None:
    source = TouPriceSource(tou=TOU, tz=BERLIN)
    start = datetime(2026, 10, 16, 21, 0, tzinfo=BERLIN)
    data = await source.fetch_segments(rng(start, 2), start)
    values = data.segments[0].import_price.values
    assert values == (0.35,) * 4 + (0.25,) * 4  # 22:00 local is the first NT slot
    assert (await source.fetch(rng(start, 2))).import_price.values == values


@pytest.mark.parametrize(
    ("day", "local_hours"), [((2026, 3, 29), 23), ((2026, 10, 25), 25), ((2026, 10, 16), 24)]
)
async def test_tou_on_dst_days_counts_real_hours(
    day: tuple[int, int, int], local_hours: int
) -> None:
    """NT 22:00-06:00 on the 25 h day: 00:00-06:00 is seven real hours, 22:00-24:00 two."""
    start = datetime(*day, tzinfo=BERLIN)
    end = datetime(*day, tzinfo=BERLIN) + timedelta(days=1)
    window = TimeRange(start=start.astimezone(UTC), end=end.astimezone(UTC))
    assert window.duration == timedelta(hours=local_hours)
    data = await TouPriceSource(tou=TOU, tz=BERLIN).fetch_segments(window, start)
    values = data.segments[0].import_price.values
    assert len(values) == local_hours * 4
    nt_hours = {23: 5 + 2, 25: 7 + 2, 24: 6 + 2}[local_hours]  # 00:00-06:00 is 5, 7 or 6 hours
    assert values.count(0.25) == nt_hours * 4


def test_unit_helpers() -> None:
    assert normalise_price_unit("€/kWh") == "EUR/kWh"
    assert normalise_price_unit("£/kWh") == "GBP/kWh"
    assert normalise_price_unit("EUR/MWh") == "EUR/MWh"
    assert normalise_price_unit(" CHF/kWh ") == "CHF/kWh"
    assert resolve_value_unit("{currency}/MWh", "SEK") == "SEK/MWh"
    assert divisor_to_kwh("EUR/kWh") == 1.0
    assert divisor_to_kwh("{currency}/MWh") == 1000.0
    assert 45.5 / divisor_to_kwh("EUR/MWh") == 0.0455
    assert energy_scale("EUR/MWh") == "MWh"
    assert energy_scale("EUR/kWh") == "kWh"
    assert energy_scale("EUR/m3") is None
    with pytest.raises(ValueError, match="not a price unit"):
        divisor_to_kwh("kWh")
