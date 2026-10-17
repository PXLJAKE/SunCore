# SPDX-License-Identifier: Apache-2.0
"""The template-driven adapters, one per integration, from documented-shape fixtures."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from suncore.core.series import TimeRange, floor_to_step
from suncore.tariff import (
    CurrentPriceSource,
    EntityReading,
    MissingEntityError,
    MissingParamError,
    SourceBinding,
    SourceUnavailableError,
    TemplatePriceSource,
    UnitMismatchError,
)
from suncore.tariff.engine import request_dates
from suncore.tariff.errors import AmbiguousSeriesError, MalformedPayloadError
from suncore.tariff.sources import finest_series
from tests.tariff.helpers import (
    BERLIN,
    LONDON,
    NOW,
    StubActions,
    action_source,
    attribute_source,
    fixture,
    reader_of,
    readings,
    template,
)

H = timedelta(hours=1)
Q = timedelta(minutes=15)
MIDNIGHT = datetime(2026, 10, 16, 0, 0, tzinfo=BERLIN)


def window(hours: int = 48, start: datetime = NOW) -> TimeRange:
    begin = floor_to_step(start)
    return TimeRange(start=begin, end=begin + timedelta(hours=hours))


def iso(moment: datetime) -> str:
    return moment.isoformat()


# --- Tibber (action, 15 minutes, per home) ----------------------------------------------


def tibber_response(start: datetime, slots: int, *, home: str = "Home") -> dict[str, Any]:
    return {
        "prices": {
            home: [
                {"start_time": iso(start + Q * i), "price": round(0.30 + 0.001 * i, 4)}
                for i in range(slots)
            ]
        }
    }


async def test_tibber_fixture_is_parsed_on_the_native_15_minute_grid() -> None:
    actions = StubActions(lambda _data: fixture("tibber_get_prices")["response"])
    source = action_source("tibber.price", actions)
    data = await source.fetch_segments(window(2, datetime(2026, 10, 16, 0, 0, tzinfo=BERLIN)), NOW)
    (segment,) = data.segments
    assert segment.source_id == "tibber.price"
    assert segment.price_includes == "all_in"
    assert segment.step == Q
    assert segment.import_price.values[:3] == (0.2812, 0.279, 0.2755)
    assert segment.start == datetime(2026, 10, 15, 22, 0, tzinfo=UTC)
    assert data.known_end == datetime(2026, 10, 15, 22, 0, tzinfo=UTC) + 6 * Q


async def test_tibber_request_asks_for_the_local_days_of_the_window() -> None:
    actions = StubActions(lambda _data: {"prices": {}})
    source = action_source("tibber.price", actions)
    await source.fetch_segments(window(48), NOW)
    ((domain, service, data, interval, wait),) = actions.calls
    assert (domain, service) == ("tibber", "get_prices")
    assert data == {
        "start": "2026-10-16T00:00:00+02:00",
        "end": "2026-10-19T00:00:00+02:00",  # NOW + 48 h is 18 Oct 10:00: the day after is the 19th
    }
    assert interval == 30.0
    assert wait == 0.0


async def test_tibber_empty_response_is_no_data_not_an_error() -> None:
    actions = StubActions(lambda _data: {"prices": {}})
    data = await action_source("tibber.price", actions).fetch_segments(window(), NOW)
    assert data.segments == ()
    assert data.known_end is None


async def test_tibber_two_homes_need_a_series_key() -> None:
    payload = {
        "prices": {
            "Home": tibber_response(datetime(2026, 10, 16, 10, tzinfo=BERLIN), 4)["prices"]["Home"],
            "Cabin": tibber_response(datetime(2026, 10, 16, 10, tzinfo=BERLIN), 4, home="x")[
                "prices"
            ]["x"],
        }
    }
    actions = StubActions(lambda _data: payload)
    with pytest.raises(AmbiguousSeriesError):
        await action_source("tibber.price", actions).fetch_segments(window(), NOW)
    data = await action_source("tibber.price", actions, series_key="Cabin").fetch_segments(
        window(), NOW
    )
    assert len(data.segments[0].import_price) == 4


async def test_tibber_gaps_and_unreadable_prices_are_none() -> None:
    start = datetime(2026, 10, 16, 10, 0, tzinfo=BERLIN)
    rows = tibber_response(start, 6)["prices"]["Home"]
    del rows[2]  # a missing slot
    rows[4] = {"start_time": rows[4]["start_time"], "price": None}  # an unreadable price
    actions = StubActions(lambda _data: {"prices": {"Home": rows}})
    data = await action_source("tibber.price", actions).fetch_segments(window(), NOW)
    (segment,) = data.segments
    values = segment.import_price.values
    assert len(values) == 6
    assert values[2] is None  # the gap
    assert values[3] is not None
    assert values[4] is not None
    assert values[5] is None  # a null price is a gap too
    assert data.report.unreadable_values == 1


async def test_tibber_mixed_15_and_60_minutes_gives_two_segments_and_a_15_minute_series() -> None:
    day = datetime(2026, 10, 16, 12, 0, tzinfo=BERLIN)
    quarter = [
        {"start_time": iso(day + Q * i), "price": 0.30 + 0.01 * i}
        for i in range(8)  # 12:00-14:00
    ]
    hourly = [
        {"start_time": iso(day + 2 * H + H * i), "price": 0.50 + 0.1 * i} for i in range(3)
    ]  # 14:00-17:00
    actions = StubActions(lambda _data: {"prices": {"Home": quarter + hourly}})
    source = action_source("tibber.price", actions)
    data = await source.fetch_segments(window(), NOW)
    first, second = data.segments
    assert (first.step, len(first)) == (Q, 8)
    assert (second.step, len(second)) == (H, 3)
    merged = await source.fetch(window())
    assert merged.step == Q
    assert len(merged) == 8 + 12  # hourly values held for four quarters each
    assert merged.import_price.values[8:12] == (0.5, 0.5, 0.5, 0.5)


async def test_tibber_dst_end_day_has_25_hours_of_quarters() -> None:
    start = datetime(2026, 10, 25, 0, 0, tzinfo=BERLIN).astimezone(UTC)  # 22:00 UTC the day before
    rows = [
        {"start_time": iso((start + Q * i).astimezone(BERLIN)), "price": 0.2 + 0.0001 * i}
        for i in range(25 * 4)
    ]
    actions = StubActions(lambda _data: {"prices": {"Home": rows}})
    now = datetime(2026, 10, 25, 0, 30, tzinfo=BERLIN)
    data = await action_source("tibber.price", actions, now=now).fetch_segments(
        TimeRange(start=start, end=start + timedelta(hours=25)), now
    )
    (segment,) = data.segments
    assert len(segment.import_price) == 100
    assert segment.import_price.end - segment.start == timedelta(hours=25)
    # the repeated local hour 02:00-03:00 exists twice, with different offsets, both kept
    stamps = [iso((start + Q * i).astimezone(BERLIN)) for i in range(8, 16)]
    assert stamps[0].endswith("+02:00")
    assert stamps[-1].endswith("+01:00")


async def test_tibber_dst_start_day_has_23_hours() -> None:
    start = datetime(2026, 3, 29, 0, 0, tzinfo=BERLIN).astimezone(UTC)
    rows = [
        {"start_time": iso((start + Q * i).astimezone(BERLIN)), "price": 0.2} for i in range(23 * 4)
    ]
    now = datetime(2026, 3, 29, 0, 30, tzinfo=BERLIN)
    actions = StubActions(lambda _data: {"prices": {"Home": rows}})
    data = await action_source("tibber.price", actions, now=now).fetch_segments(
        TimeRange(start=start, end=start + timedelta(hours=23)), now
    )
    assert data.segments[0].import_price.end - start == timedelta(hours=23)


async def test_tibber_malformed_payloads_are_rejected() -> None:
    for payload in (
        {"prices": {"Home": "nope"}},
        {"prices": {"Home": [{"price": 1}]}},
        {"prices": []},
    ):
        actions = StubActions(lambda _data, p=payload: p)
        with pytest.raises(MalformedPayloadError):
            await action_source("tibber.price", actions).fetch_segments(window(), NOW)


async def test_a_window_clips_the_series_to_whole_overlapping_slots() -> None:
    actions = StubActions(
        lambda _data: tibber_response(datetime(2026, 10, 16, 8, tzinfo=BERLIN), 40)
    )
    narrow = TimeRange(
        start=datetime(2026, 10, 16, 10, 0, tzinfo=BERLIN),
        end=datetime(2026, 10, 16, 11, 0, tzinfo=BERLIN),
    )
    data = await action_source("tibber.price", actions).fetch_segments(narrow, NOW)
    (segment,) = data.segments
    assert len(segment.import_price) == 4
    assert segment.start == narrow.start.astimezone(UTC)


# --- Nord Pool (action per date, UTC, per MWh, slot end) -------------------------------

NORDPOOL_PARAMS: Mapping[str, str | list[str]] = {"config_entry": "01HXENTRY", "areas": ["SE3"]}


def nordpool_by_date(day17: list[dict[str, Any]] | None = None) -> StubActions:
    first = fixture("nordpool_get_prices_for_date")["response"]

    def respond(data: Mapping[str, Any]) -> Mapping[str, Any]:
        return first if data["date"] == "2026-10-16" else {"SE3": day17 or []}

    return StubActions(respond)


async def test_nordpool_calls_once_per_local_date_and_never_beyond_tomorrow() -> None:
    actions = nordpool_by_date()
    source = action_source("nordpool.price", actions, action_params=NORDPOOL_PARAMS)
    await source.fetch_segments(window(72), NOW)  # a long window must not ask for the 18th/19th
    assert [(c[2]["date"], c[4]) for c in actions.calls] == [
        ("2026-10-16", 0.0),
        ("2026-10-17", 31.0),  # the limiter's next slot is awaited for the second date
    ]
    assert all(c[2]["config_entry"] == "01HXENTRY" for c in actions.calls)
    assert all(c[2]["areas"] == ["SE3"] for c in actions.calls)
    assert "currency" not in actions.calls[0][2]  # optional and not configured


async def test_nordpool_prices_are_divided_by_1000_and_the_slot_length_is_end_minus_start() -> None:
    actions = nordpool_by_date()
    source = action_source("nordpool.price", actions, action_params=NORDPOOL_PARAMS)
    data = await source.fetch_segments(window(24, datetime(2026, 10, 16, 0, 0, tzinfo=BERLIN)), NOW)
    (segment,) = data.segments
    assert segment.price_includes == "spot_only"
    assert segment.step == H
    assert segment.start == datetime(2026, 10, 15, 22, tzinfo=UTC)
    assert segment.import_price.values == pytest.approx((0.0455, 0.04125, -0.0035))


async def test_nordpool_15_minute_day_after_the_transition_is_mixed_with_hourly() -> None:
    t = datetime(2026, 10, 16, 22, 0, tzinfo=UTC)  # local 17 Oct 00:00
    quarter = [
        {"start": iso(t + Q * i), "end": iso(t + Q * (i + 1)), "price": 100.0 + i} for i in range(8)
    ]
    actions = nordpool_by_date(quarter)
    source = action_source("nordpool.price", actions, action_params=NORDPOOL_PARAMS)
    data = await source.fetch_segments(window(48, MIDNIGHT), NOW)
    steps = [s.step for s in data.segments]
    assert steps == [H, Q]
    assert data.segments[1].import_price.values[:2] == pytest.approx((0.1, 0.101))
    merged = await source.fetch(window(48, MIDNIGHT))
    assert merged.step == Q


async def test_nordpool_unpublished_tomorrow_returns_only_today() -> None:
    source = action_source("nordpool.price", nordpool_by_date(), action_params=NORDPOOL_PARAMS)
    data = await source.fetch_segments(window(48, MIDNIGHT), NOW)
    assert data.known_end == datetime(2026, 10, 16, 1, 0, tzinfo=UTC)


async def test_nordpool_needs_the_config_entry_and_makes_no_call_without_it() -> None:
    actions = nordpool_by_date()
    source = action_source("nordpool.price", actions, action_params={"areas": ["SE3"]})
    with pytest.raises(MissingParamError) as err:
        await source.fetch_segments(window(), NOW)
    assert err.value.message_args == {"field": "config_entry"}
    assert actions.calls == []


async def test_nordpool_several_areas_need_a_series_key() -> None:
    both = {"SE3": [], "SE4": []}
    actions = StubActions(lambda _data: both)
    source = action_source("nordpool.price", actions, action_params={"config_entry": "x"})
    with pytest.raises(AmbiguousSeriesError):
        await source.fetch_segments(window(), NOW)
    chosen = action_source(
        "nordpool.price", actions, action_params={"config_entry": "x"}, series_key="SE4"
    )
    assert (await chosen.fetch_segments(window(), NOW)).segments == ()


async def test_nordpool_optional_fields_are_passed_when_configured() -> None:
    actions = nordpool_by_date()
    source = action_source(
        "nordpool.price",
        actions,
        action_params={"config_entry": "x", "areas": "SE3", "currency": "SEK"},
    )
    await source.fetch_segments(window(2), NOW)
    assert actions.calls[0][2] == {
        "config_entry": "x",
        "areas": "SE3",
        "currency": "SEK",
        "date": "2026-10-16",
    }


def test_request_dates_follow_the_local_calendar_across_the_dst_change() -> None:
    now = datetime(2026, 10, 24, 23, 30, tzinfo=BERLIN)
    win = TimeRange(start=floor_to_step(now), end=floor_to_step(now) + timedelta(hours=30))
    assert request_dates(win, now, BERLIN, 48) == [
        date(2026, 10, 24),
        date(2026, 10, 25),
        date(2026, 10, 26),
    ]
    assert request_dates(win, now, BERLIN, 24) == [date(2026, 10, 24), date(2026, 10, 25)]
    # a window that starts after the limit asks for nothing
    late = TimeRange(start=now + timedelta(days=5), end=now + timedelta(days=6))
    assert request_dates(late, now, BERLIN, 24) == []


# --- ENTSO-E (attribute, local str(datetime), spacing) ---------------------------------

ENTSOE_ENTITY = "sensor.entsoe_average_electricity_price"


async def test_entsoe_fixture_hourly_spot_only() -> None:
    source = attribute_source(
        "entsoe.price", {"price": ENTSOE_ENTITY}, readings("entsoe_avg_price")
    )
    data = await source.fetch_segments(window(24, datetime(2026, 10, 16, 0, 0, tzinfo=BERLIN)), NOW)
    (segment,) = data.segments
    assert segment.step == H
    assert segment.price_includes == "spot_only"
    assert segment.import_price.values == (0.0712, 0.069, 0.0668)
    assert segment.start == datetime(2026, 10, 15, 22, tzinfo=UTC)


async def test_entsoe_user_statement_overrides_the_template_classification() -> None:
    source = attribute_source(
        "entsoe.price",
        {"price": ENTSOE_ENTITY},
        readings("entsoe_avg_price"),
        price_includes="all_in",
    )
    assert source.price_includes == "all_in"
    data = await source.fetch_segments(window(24, datetime(2026, 10, 16, 0, 0, tzinfo=BERLIN)), NOW)
    assert data.segments[0].price_includes == "all_in"


async def test_entsoe_fifteen_minute_period_is_inferred_from_the_spacing() -> None:
    base = datetime(2026, 10, 16, 0, 0, tzinfo=BERLIN)
    rows = [{"time": str(base + Q * i), "price": 0.05 + 0.001 * i} for i in range(96)]
    entity = EntityReading("0.05", {"unit_of_measurement": "EUR/kWh", "prices": rows})
    source = attribute_source("entsoe.price", {"price": ENTSOE_ENTITY}, {ENTSOE_ENTITY: entity})
    assert rows[0]["time"] == "2026-10-16 00:00:00+02:00"  # the documented str(datetime) form
    data = await source.fetch_segments(window(24, base), NOW)
    assert data.segments[0].step == Q
    assert len(data.segments[0].import_price) == 96


async def test_entsoe_dst_end_day_with_str_datetime_offsets() -> None:
    base = datetime(2026, 10, 25, 0, 0, tzinfo=BERLIN).astimezone(UTC)
    rows = [
        {"time": str((base + H * i).astimezone(BERLIN)), "price": 0.05 + 0.001 * i}
        for i in range(25)
    ]
    assert rows[2]["time"].endswith("+02:00")
    assert rows[3]["time"] == "2026-10-25 02:00:00+01:00"
    entity = EntityReading("0.05", {"prices": rows})
    now = datetime(2026, 10, 25, 0, 30, tzinfo=BERLIN)
    source = attribute_source(
        "entsoe.price", {"price": ENTSOE_ENTITY}, {ENTSOE_ENTITY: entity}, now=now
    )
    data = await source.fetch_segments(window(25, base), now)
    assert len(data.segments[0].import_price) == 25
    assert data.report.duplicates == 0


async def test_entsoe_mwh_template_divides_by_1000_and_refuses_a_kwh_entity() -> None:
    rows = [
        {"time": "2026-10-16 00:00:00+02:00", "price": 85.5},
        {"time": "2026-10-16 01:00:00+02:00", "price": 80.0},
    ]
    mwh = EntityReading("85.5", {"unit_of_measurement": "EUR/MWh", "prices": rows})
    source = attribute_source("entsoe.price_mwh", {"price": ENTSOE_ENTITY}, {ENTSOE_ENTITY: mwh})
    data = await source.fetch_segments(window(24, datetime(2026, 10, 16, 0, 0, tzinfo=BERLIN)), NOW)
    assert data.segments[0].import_price.values == pytest.approx((0.0855, 0.08))
    # the kWh template on an entity that says MWh would be a silent factor 1000: refused
    wrong = attribute_source("entsoe.price", {"price": ENTSOE_ENTITY}, {ENTSOE_ENTITY: mwh})
    with pytest.raises(UnitMismatchError) as err:
        await wrong.fetch_segments(window(24), NOW)
    assert err.value.message_args["sensor_unit"] == "EUR/MWh"


async def test_entsoe_unavailable_entity_missing_attribute_and_empty_list() -> None:
    entities = {"price": ENTSOE_ENTITY}
    with pytest.raises(SourceUnavailableError):
        await attribute_source("entsoe.price", entities, {}).fetch_segments(window(), NOW)
    bare = {ENTSOE_ENTITY: EntityReading("unknown", {})}
    with pytest.raises(MalformedPayloadError):
        await attribute_source("entsoe.price", entities, bare).fetch_segments(window(), NOW)
    empty = {ENTSOE_ENTITY: EntityReading("0.1", {"prices": []})}
    data = await attribute_source("entsoe.price", entities, empty).fetch_segments(window(), NOW)
    assert data.segments == ()  # published, but nothing yet: no error


async def test_a_required_role_without_an_entity_is_reported() -> None:
    source = attribute_source("entsoe.price", {}, readings("entsoe_avg_price"))
    with pytest.raises(MissingEntityError):
        await source.fetch_segments(window(), NOW)


# --- EPEX Spot (attribute, local ISO, end_time, unit literal) --------------------------


async def test_epex_total_price_mixed_hourly_and_quarter_hour_slots() -> None:
    entity = "sensor.epex_spot_total_price"
    source = attribute_source(
        "epex_spot.total_price", {"price": entity}, readings("epex_spot_total_price")
    )
    data = await source.fetch_segments(window(24, datetime(2026, 10, 16, 0, 0, tzinfo=BERLIN)), NOW)
    hourly, quarter = data.segments
    assert (hourly.step, hourly.import_price.values) == (H, (0.2631, 0.2587))
    assert (quarter.step, quarter.import_price.values) == (Q, (0.2561, 0.2549))
    assert source.price_includes == "all_in"
    assert hourly.price_includes == "all_in"


async def test_epex_market_price_template_is_spot_only() -> None:
    entity = "sensor.epex_spot_market_price"
    source = attribute_source(
        "epex_spot.market_price",
        {"price": entity},
        readings("epex_spot_total_price")
        | {entity: readings("epex_spot_total_price")["sensor.epex_spot_total_price"]},
    )
    assert source.price_includes == "spot_only"
    data = await source.fetch_segments(window(24, datetime(2026, 10, 16, 0, 0, tzinfo=BERLIN)), NOW)
    assert data.segments[0].price_includes == "spot_only"


async def test_epex_gbp_symbol_is_accepted_by_the_unit_check() -> None:
    attrs = dict(readings("epex_spot_total_price")["sensor.epex_spot_total_price"].attributes)
    attrs["unit_of_measurement"] = "£/kWh"
    source = attribute_source(
        "epex_spot.total_price",
        {"price": "sensor.x"},
        {"sensor.x": EntityReading("0.2", attrs)},
        currency="GBP",
    )
    data = await source.fetch_segments(window(24, datetime(2026, 10, 16, 0, 0, tzinfo=BERLIN)), NOW)
    assert data.segments


# --- Octopus (two event entities merged, 30 minutes, GBP) -------------------------------

CURRENT = "event.octopus_energy_electricity_1234_5678_current_day_rates"
NEXT = "event.octopus_energy_electricity_1234_5678_next_day_rates"


async def test_octopus_merges_current_and_next_day_rates() -> None:
    source = attribute_source(
        "octopus_energy.rates",
        {"current_day_rates": CURRENT, "next_day_rates": NEXT},
        readings("octopus_rates"),
        tz=LONDON,
        currency="GBP",
    )
    win = TimeRange(
        start=datetime(2026, 10, 15, 23, 0, tzinfo=UTC),
        end=datetime(2026, 10, 17, 23, 0, tzinfo=UTC),
    )
    data = await source.fetch_segments(win, NOW)
    today, tomorrow = data.segments  # 00:00-01:30 today, 00:00-01:00 tomorrow: two runs
    assert today.step == tomorrow.step == timedelta(minutes=30)
    assert today.price_includes == "all_in"
    assert today.import_price.values == (0.1245, 0.1302, 0.1519)
    assert tomorrow.import_price.values == (0.101, 0.0955)
    assert data.known_end == datetime(2026, 10, 17, 0, 0, tzinfo=UTC)


async def test_octopus_without_the_next_day_entity_still_reads_the_current_day() -> None:
    source = attribute_source(
        "octopus_energy.rates", {"current_day_rates": CURRENT}, readings("octopus_rates"), tz=LONDON
    )
    win = TimeRange(
        start=datetime(2026, 10, 15, 23, 0, tzinfo=UTC),
        end=datetime(2026, 10, 17, 23, 0, tzinfo=UTC),
    )
    data = await source.fetch_segments(win, NOW)
    assert len(data.segments) == 1


async def test_octopus_event_entity_before_its_first_event_has_no_rates() -> None:
    r = readings("octopus_rates")
    r[NEXT] = EntityReading("unknown", {"event_types": ["x"]})
    source = attribute_source(
        "octopus_energy.rates", {"current_day_rates": CURRENT, "next_day_rates": NEXT}, r, tz=LONDON
    )
    win = TimeRange(
        start=datetime(2026, 10, 15, 23, 0, tzinfo=UTC),
        end=datetime(2026, 10, 17, 23, 0, tzinfo=UTC),
    )
    data = await source.fetch_segments(win, NOW)
    assert data.known_end == datetime(2026, 10, 16, 0, 30, tzinfo=UTC)


async def test_octopus_next_day_published_in_a_different_slot_length_makes_a_new_segment() -> None:
    r = readings("octopus_rates")
    r[NEXT] = EntityReading(
        "x",
        {
            "rates": [
                {
                    "start": "2026-10-17T00:00:00+01:00",
                    "end": "2026-10-17T01:00:00+01:00",
                    "value_inc_vat": 0.1,
                }
            ]
        },
    )
    source = attribute_source(
        "octopus_energy.rates", {"current_day_rates": CURRENT, "next_day_rates": NEXT}, r, tz=LONDON
    )
    win = TimeRange(
        start=datetime(2026, 10, 15, 23, 0, tzinfo=UTC),
        end=datetime(2026, 10, 17, 23, 0, tzinfo=UTC),
    )
    data = await source.fetch_segments(win, NOW)
    assert [s.step for s in data.segments] == [timedelta(minutes=30), H]


# --- generic ----------------------------------------------------------------------------


async def test_generic_price_list_quarter_hours() -> None:
    entity = "sensor.my_price_list"
    source = attribute_source(
        "generic.price_list", {"price": entity}, readings("generic_price_list")
    )
    data = await source.fetch_segments(window(24, datetime(2026, 10, 16, 0, 0, tzinfo=BERLIN)), NOW)
    (segment,) = data.segments
    assert (segment.step, segment.import_price.values) == (Q, (0.31, 0.3, 0.29))


# --- the PriceSource protocol -----------------------------------------------------------


async def test_fetch_without_data_is_an_empty_series_at_the_window_start() -> None:
    actions = StubActions(lambda _data: {"prices": {}})
    out = await action_source("tibber.price", actions).fetch(window())
    assert len(out) == 0
    assert out.start == window().start


async def test_finest_series_holds_coarse_prices_over_finer_slots() -> None:
    entity = "sensor.epex_spot_total_price"
    source = attribute_source(
        "epex_spot.total_price", {"price": entity}, readings("epex_spot_total_price")
    )
    series = await source.fetch(window(24, datetime(2026, 10, 16, 0, 0, tzinfo=BERLIN)))
    assert series.step == Q
    assert series.import_price.values[:4] == (0.2631,) * 4
    assert series.import_price.values[8:10] == (0.2561, 0.2549)
    again = finest_series("x", "all_in", [], window())
    assert len(again) == 0


# --- construction guards ----------------------------------------------------------------


def test_the_engine_refuses_templates_it_cannot_serve() -> None:
    binding = SourceBinding(tz=BERLIN)
    with pytest.raises(ValueError, match="ActionCaller"):
        TemplatePriceSource(template("tibber.price"), binding, clock=lambda: NOW)
    with pytest.raises(ValueError, match="EntityReader"):
        TemplatePriceSource(template("entsoe.price"), binding, clock=lambda: NOW)


# --- price entity only (no template) ----------------------------------------------------


async def test_current_price_source_is_one_slot_from_the_entity_state() -> None:
    entity = {"sensor.p": EntityReading("0.25", {"unit_of_measurement": "EUR/kWh"})}
    source = CurrentPriceSource(
        "sensor.p", reader_of(entity), price_includes="all_in", clock=lambda: NOW
    )
    data = await source.fetch_segments(window(), NOW)
    (segment,) = data.segments
    assert segment.import_price.values == (0.25,)
    assert segment.start == floor_to_step(NOW)
    assert source.watched_entities == ("sensor.p",)
    assert (await source.fetch(window())).import_price.values == (0.25,)


async def test_current_price_source_converts_mwh_and_rejects_bad_states() -> None:
    mwh = {"sensor.p": EntityReading("85.0", {"unit_of_measurement": "EUR/MWh"})}
    data = await CurrentPriceSource(
        "sensor.p", reader_of(mwh), price_includes="spot_only", clock=lambda: NOW
    ).fetch_segments(window(), NOW)
    assert data.segments[0].import_price.values == (0.085,)
    for reading in (None, EntityReading("unknown", {})):
        source = CurrentPriceSource(
            "sensor.p", reader_of({"sensor.p": reading}), price_includes="all_in", clock=lambda: NOW
        )
        with pytest.raises(SourceUnavailableError):
            await source.fetch_segments(window(), NOW)
