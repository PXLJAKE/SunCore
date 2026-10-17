# SPDX-License-Identifier: Apache-2.0
"""Weekly windows, price formula, HT/NT and blended export tiers (Phase 4 WP2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from suncore.core.config import ExportTier
from suncore.core.forecast import PriceSource
from suncore.core.tariff import (
    NIGHT_WINDOW,
    GridFeeWindow,
    PriceFormula,
    TouPeriod,
    TouTariff,
    TouWindow,
    WeeklySchedule,
    blended_export_price,
    hhmm,
    window_duration,
)
from suncore.core.tariff import PriceSource as ReExported

BERLIN = ZoneInfo("Europe/Berlin")
FRI = datetime(2026, 10, 16, tzinfo=BERLIN)  # a Friday


def at(day: datetime, hh: int, mm: int = 0) -> datetime:
    return day.replace(hour=hh, minute=mm)


def test_price_source_is_re_exported() -> None:
    assert ReExported is PriceSource


def test_window_same_day() -> None:
    w = TouWindow(days=frozenset({4}), start="08:00", end="20:00")
    assert not w.crosses_midnight
    assert w.contains(at(FRI, 8), BERLIN)
    assert w.contains(at(FRI, 19, 59), BERLIN)
    assert not w.contains(at(FRI, 20), BERLIN)
    assert not w.contains(at(FRI, 7, 59), BERLIN)
    assert not w.contains(at(FRI + timedelta(days=1), 12), BERLIN)


def test_window_crossing_midnight_belongs_to_its_start_day() -> None:
    w = TouWindow(days=frozenset({4}), start="22:00", end="06:00")  # Friday night
    assert w.crosses_midnight
    assert w.contains(at(FRI, 22), BERLIN)
    assert w.contains(at(FRI + timedelta(days=1), 5, 59), BERLIN)  # Saturday morning
    assert not w.contains(at(FRI, 5), BERLIN)  # Friday morning belongs to Thursday's window
    assert not w.contains(at(FRI + timedelta(days=1), 22), BERLIN)


def test_whole_day_window_and_time_zone_conversion() -> None:
    w = TouWindow(days=frozenset({5, 6}), start="00:00", end="00:00")
    sat = FRI + timedelta(days=1)
    assert w.contains(at(sat, 0), BERLIN)
    assert w.contains(at(sat, 23, 59), BERLIN)
    assert not w.contains(at(FRI, 23, 59), BERLIN)
    # 21:30 UTC on Friday is 23:30 in Berlin (summer time): still Friday.
    assert not w.contains(datetime(2026, 10, 16, 21, 30, tzinfo=UTC), BERLIN)
    assert w.contains(datetime(2026, 10, 16, 22, 30, tzinfo=UTC), BERLIN)
    with pytest.raises(ValueError, match="aware"):
        w.contains(datetime(2026, 1, 1), BERLIN)  # noqa: DTZ001


@pytest.mark.parametrize(
    "bad",
    [
        {"start": "24:00", "end": "01:00"},
        {"start": "6:00", "end": "07:00"},
        {"start": "06:00", "end": "07:00", "days": []},
        {"start": "06:00", "end": "07:00", "days": [7]},
    ],
)
def test_window_rejects(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TouWindow.model_validate(bad)


def test_night_window_and_durations() -> None:
    assert NIGHT_WINDOW.contains(at(FRI, 23), BERLIN)
    assert NIGHT_WINDOW.contains(at(FRI, 3), BERLIN)
    assert not NIGHT_WINDOW.contains(at(FRI, 12), BERLIN)
    assert window_duration(NIGHT_WINDOW.windows[0]) == timedelta(hours=8)
    assert window_duration(TouWindow(start="05:00", end="05:00")) == timedelta(hours=24)
    assert hhmm(time(6, 5, 59)) == "06:05"
    with pytest.raises(ValidationError):
        WeeklySchedule(windows=())


def test_dst_night_is_judged_in_local_time() -> None:
    # 2026-03-29: clocks jump 02:00 -> 03:00; 01:30 UTC is 03:30 local, inside 22-06.
    assert NIGHT_WINDOW.contains(datetime(2026, 3, 29, 1, 30, tzinfo=UTC), BERLIN)
    assert not NIGHT_WINDOW.contains(datetime(2026, 3, 29, 4, 30, tzinfo=UTC), BERLIN)


def test_price_formula() -> None:
    f = PriceFormula(grid_fee=0.08, levies=0.03, margin=0.02, vat_pct=19)
    assert f.end_price(0.10, None, BERLIN) == pytest.approx((0.10 + 0.13) * 1.19)
    assert f.end_price(-0.20, None, BERLIN) == pytest.approx(
        (-0.20 + 0.13) * 1.19
    )  # negative end price
    assert PriceFormula().end_price(0.1, None, BERLIN) == pytest.approx(0.1)
    assert PriceFormula(spot_multiplier=1.1).end_price(0.1, None, BERLIN) == pytest.approx(0.11)


def test_price_formula_time_variable_grid_fee() -> None:
    low = GridFeeWindow(window=TouWindow(start="00:00", end="06:00"), fee=0.02)
    high = GridFeeWindow(window=TouWindow(start="17:00", end="20:00"), fee=0.15)
    f = PriceFormula(grid_fee=0.08, grid_fee_windows=(low, high))
    assert f.grid_fee_at(None, BERLIN) == 0.08
    assert f.end_price(0.1, at(FRI, 3), BERLIN) == pytest.approx(0.12)
    assert f.end_price(0.1, at(FRI, 18), BERLIN) == pytest.approx(0.25)
    assert f.end_price(0.1, at(FRI, 12), BERLIN) == pytest.approx(0.18)
    assert f.end_price(0.1, datetime(2026, 10, 16, 1, tzinfo=UTC), BERLIN) == pytest.approx(0.12)
    with pytest.raises(ValidationError):
        PriceFormula(spot_multiplier=0)
    with pytest.raises(ValidationError):
        PriceFormula(vat_pct=-1)


def test_tou_tariff_first_period_wins_and_seasons() -> None:
    nt = TouPeriod(window=TouWindow(start="22:00", end="06:00"), price=0.22)
    winter_peak = TouPeriod(
        window=TouWindow(days=frozenset(range(5)), start="17:00", end="20:00"),
        price=0.40,
        months=frozenset({11, 12, 1, 2}),
    )
    tariff = TouTariff(base_price=0.30, periods=(nt, winter_peak))
    assert tariff.price_at(at(FRI, 23), BERLIN) == 0.22
    assert tariff.price_at(at(FRI, 18), BERLIN) == 0.30  # October: not winter
    assert tariff.price_at(datetime(2026, 12, 4, 18, tzinfo=BERLIN), BERLIN) == 0.40
    assert tariff.price_at(datetime(2026, 12, 5, 18, tzinfo=BERLIN), BERLIN) == 0.30  # Saturday
    with pytest.raises(ValidationError):
        TouTariff(base_price=0.3, periods=())
    with pytest.raises(ValidationError):
        TouPeriod(window=nt.window, price=0.2, months=frozenset({13}))


@dataclass(frozen=True)
class Tier:
    up_to_kwp: float | None
    price: float


def test_blended_export_price_eeg_example() -> None:
    tiers = [Tier(10, 0.0786), Tier(None, 0.0680)]
    assert blended_export_price(tiers, 17) == pytest.approx((10 * 0.0786 + 7 * 0.0680) / 17)
    assert blended_export_price(tiers, 8) == pytest.approx(0.0786)
    config_tiers = (ExportTier(up_to_kwp=10, price=0.08), ExportTier(price=0.07))
    assert blended_export_price(config_tiers, 20) == pytest.approx(0.075)


def test_blended_export_price_refuses_to_guess() -> None:
    with pytest.raises(ValueError, match="below the plant"):
        blended_export_price([Tier(10, 0.08)], 17)
    with pytest.raises(ValueError, match="positive"):
        blended_export_price([Tier(None, 0.08)], 0)
    with pytest.raises(ValueError, match="no tiers"):
        blended_export_price([], 5)
    with pytest.raises(ValueError, match="ordered"):
        blended_export_price([Tier(None, 0.08), Tier(10, 0.07)], 5)
    with pytest.raises(ValueError, match="ordered"):
        blended_export_price([Tier(10, 0.08), Tier(5, 0.07), Tier(None, 0.06)], 5)


@given(
    bounds=st.lists(st.floats(0.5, 100), min_size=1, max_size=4, unique=True).map(sorted),
    rates=st.lists(st.floats(0, 0.5), min_size=5, max_size=5),
    total=st.floats(0.1, 200),
)
def test_blended_rate_lies_between_the_tier_rates(
    bounds: list[float], rates: list[float], total: float
) -> None:
    tiers = [Tier(b, r) for b, r in zip(bounds, rates, strict=False)]
    tiers.append(Tier(None, rates[len(bounds)]))
    used = [t.price for t in tiers]
    blended = blended_export_price(tiers, total)
    assert min(used) - 1e-12 <= blended <= max(used) + 1e-12


def test_season_of_a_window_crossing_midnight_is_its_start_day() -> None:
    """m4: 31 Oct 22:00 - 1 Nov 06:00 is an October night, also after midnight."""
    october_nt = TouPeriod(
        window=TouWindow(start="22:00", end="06:00"), price=0.20, months=frozenset({10})
    )
    tariff = TouTariff(base_price=0.30, periods=(october_nt,))
    assert tariff.price_at(datetime(2026, 10, 31, 23, tzinfo=BERLIN), BERLIN) == 0.20
    assert tariff.price_at(datetime(2026, 11, 1, 3, tzinfo=BERLIN), BERLIN) == 0.20
    assert tariff.price_at(datetime(2026, 11, 1, 23, tzinfo=BERLIN), BERLIN) == 0.30
    assert tariff.price_at(datetime(2026, 10, 1, 3, tzinfo=BERLIN), BERLIN) == 0.30  # Sep night
    w = october_nt.window
    assert w.occurrence_date(datetime(2026, 11, 1, 3, tzinfo=BERLIN), BERLIN) == date(2026, 10, 31)
    assert w.occurrence_date(datetime(2026, 11, 1, 12, tzinfo=BERLIN), BERLIN) is None


def test_grid_fee_window_months_for_para14a_module3_quarters() -> None:
    """m3: a module 3 low fee only in Q1 and Q4; the month is the start day's."""
    q1_q4 = frozenset({1, 2, 3, 10, 11, 12})
    low = GridFeeWindow(window=TouWindow(start="22:00", end="06:00"), fee=0.02, months=q1_q4)
    f = PriceFormula(grid_fee=0.08, grid_fee_windows=(low,))
    assert f.grid_fee_at(datetime(2026, 1, 10, 23, tzinfo=BERLIN), BERLIN) == 0.02
    assert f.grid_fee_at(datetime(2026, 7, 10, 23, tzinfo=BERLIN), BERLIN) == 0.08
    assert f.grid_fee_at(datetime(2026, 4, 1, 2, tzinfo=BERLIN), BERLIN) == 0.02  # 31 Mar night
    assert f.grid_fee_at(datetime(2026, 10, 1, 2, tzinfo=BERLIN), BERLIN) == 0.08  # 30 Sep night
    assert GridFeeWindow(window=low.window, fee=0.02).months is None  # additive: all year
    with pytest.raises(ValidationError):
        GridFeeWindow(window=low.window, fee=0.02, months=frozenset({0}))


def test_utc_instant_is_judged_in_the_given_zone_not_its_own() -> None:
    """m5: ``tz`` is required; a UTC timestamp is converted, never judged in UTC."""
    utc_23 = datetime(2026, 10, 16, 21, 30, tzinfo=UTC)  # 23:30 in Berlin
    assert NIGHT_WINDOW.contains(utc_23, BERLIN)
    assert not NIGHT_WINDOW.contains(utc_23, UTC)


def test_apply_is_a_deprecated_alias_of_end_price() -> None:
    f = PriceFormula(grid_fee=0.1, vat_pct=19)
    assert f.apply(0.2, None, BERLIN) == f.end_price(0.2, None, BERLIN)
