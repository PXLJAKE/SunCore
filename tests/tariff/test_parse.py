# SPDX-License-Identifier: Apache-2.0
"""Parsing of price lists: timestamps, numbers, paths, normalising and segmenting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from suncore.core.series import SeriesUnit, TimeSeries
from suncore.tariff.errors import AmbiguousSeriesError, MalformedPayloadError
from suncore.tariff.parse import (
    RawSlot,
    build_segments,
    clip_series,
    expand_path,
    infer_step,
    normalise,
    parse_number,
    parse_timestamp,
    select_series,
    slots_from_mapping,
    slots_from_records,
    walk,
)
from suncore.templates.models import SeriesFields
from tests.tariff.helpers import BERLIN

H = timedelta(hours=1)
Q = timedelta(minutes=15)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


# --- timestamps -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ENTSO-E: str(datetime) with a space (research: coordinator.py:250-256)
        ("2026-10-16 00:00:00+02:00", utc(2026, 10, 15, 22)),
        # EPEX Spot / Octopus: local ISO 8601
        ("2026-10-16T00:00:00+02:00", utc(2026, 10, 15, 22)),
        # Nord Pool: UTC +00:00
        ("2026-10-15T22:00:00+00:00", utc(2026, 10, 15, 22)),
        # Tibber: milliseconds and Z
        ("2026-10-16T00:00:00.000+02:00", utc(2026, 10, 15, 22)),
        ("2026-10-15T22:00:00Z", utc(2026, 10, 15, 22)),
        ("  2026-10-16 00:00:00+02:00 ", utc(2026, 10, 15, 22)),
    ],
)
def test_timestamps_are_parsed_to_utc(text: str, expected: datetime) -> None:
    assert parse_timestamp(text) == expected
    assert parse_timestamp(text).tzinfo is UTC


def test_datetime_objects_are_accepted() -> None:
    assert parse_timestamp(datetime(2026, 10, 16, tzinfo=BERLIN)) == utc(2026, 10, 15, 22)


@pytest.mark.parametrize(
    "bad",
    [
        "2026-10-16 00:00:00",
        "yesterday",
        "",
        None,
        5,
        datetime.fromisoformat("2026-01-01T00:00:00"),
    ],
)
def test_a_timestamp_without_offset_or_text_is_malformed(bad: object) -> None:
    with pytest.raises(MalformedPayloadError):
        parse_timestamp(bad)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(1, 1.0), (0.25, 0.25), ("0.5", 0.5), (" -3.5 ", -3.5), ("1e-3", 0.001)],
)
def test_numbers(raw: object, expected: float) -> None:
    assert parse_number(raw) == expected


@pytest.mark.parametrize("raw", [None, True, "n/a", "", float("nan"), float("inf"), "nan", [], {}])
def test_unreadable_numbers_are_none(raw: object) -> None:
    assert parse_number(raw) is None


# --- paths ------------------------------------------------------------------------------


def test_walk_follows_dotted_keys_and_rejects_a_missing_one() -> None:
    assert walk({"a": {"b": [1]}}, "a.b") == [1]
    with pytest.raises(MalformedPayloadError):
        walk({"a": {}}, "a.b")
    with pytest.raises(MalformedPayloadError):
        walk({"a": 1}, "a.b")


def test_star_fans_out_over_a_mapping() -> None:
    payload = {"prices": {"Home": [1], "Cabin": [2]}}
    assert expand_path(payload, "prices.*") == [("Home", [1]), ("Cabin", [2])]
    assert expand_path({"SE3": [3]}, "*") == [("SE3", [3])]
    assert expand_path({"other": 1}, "prices.*") == []  # nothing there: not an error


def test_a_path_through_a_non_object_is_malformed() -> None:
    with pytest.raises(MalformedPayloadError):
        expand_path({"prices": [1]}, "prices.*")


def test_select_series_the_only_one_or_the_named_one() -> None:
    assert select_series([("Home", [1])], None) == [1]
    assert select_series([], None) is None
    two = [("Home", [1]), ("Cabin", [2])]
    assert select_series(two, "Cabin") == [2]
    assert select_series(two, "cabin") == [2]  # area codes are case-insensitive
    assert select_series([], "Home") is None


def test_several_series_without_a_key_are_ambiguous() -> None:
    with pytest.raises(AmbiguousSeriesError) as err:
        select_series([("Home", [1]), ("Cabin", [2])], None)
    assert err.value.message_args["available"] == ["Cabin", "Home"]


def test_an_unknown_key_is_malformed() -> None:
    with pytest.raises(MalformedPayloadError):
        select_series([("Home", [1])], "Cabin")


# --- records and mappings ---------------------------------------------------------------

TIBBER = SeriesFields(timestamp_field="start_time", value_field="price", value_unit="EUR/kWh")
NORDPOOL = SeriesFields(
    timestamp_field="start", end_field="end", value_field="price", value_unit="EUR/MWh"
)


def test_records_with_a_missing_or_text_value_become_gaps() -> None:
    slots, bad = slots_from_records(
        [
            {"start_time": "2026-10-16T00:00:00+02:00", "price": 0.1},
            {"start_time": "2026-10-16T00:15:00+02:00", "price": None},
            {"start_time": "2026-10-16T00:30:00+02:00", "price": "x"},
            {"start_time": "2026-10-16T00:45:00+02:00"},
        ],
        TIBBER,
    )
    assert [s.value for s in slots] == [0.1, None, None, None]
    assert bad == 3


def test_records_need_a_timestamp_and_a_list() -> None:
    with pytest.raises(MalformedPayloadError):
        slots_from_records([{"price": 1}], TIBBER)
    with pytest.raises(MalformedPayloadError):
        slots_from_records("nope", TIBBER)
    with pytest.raises(MalformedPayloadError):
        slots_from_records(["nope"], TIBBER)


def test_records_with_an_end_field() -> None:
    slots, _ = slots_from_records(
        [{"start": "2026-10-15T22:00:00+00:00", "end": "2026-10-15T23:00:00+00:00", "price": 45.5}],
        NORDPOOL,
    )
    assert slots == [RawSlot(utc(2026, 10, 15, 22), utc(2026, 10, 15, 23), 45.5)]


def test_mapping_shape() -> None:
    slots, bad = slots_from_mapping(
        {"2026-10-16T00:00:00+02:00": 0.1, "2026-10-16T01:00:00+02:00": "?"}
    )
    assert [s.value for s in slots] == [0.1, None]
    assert bad == 1
    with pytest.raises(MalformedPayloadError):
        slots_from_mapping([1])


# --- normalise --------------------------------------------------------------------------


def test_normalise_sorts_and_the_later_duplicate_wins() -> None:
    slots, report = normalise(
        [
            RawSlot(utc(2026, 1, 1, 1), None, 2.0),
            RawSlot(utc(2026, 1, 1, 0), None, 1.0),
            RawSlot(utc(2026, 1, 1, 1), None, 3.0),
        ]
    )
    assert [(s.start.hour, s.value) for s in slots] == [(0, 1.0), (1, 3.0)]
    assert report.duplicates == 1
    assert report.points == 2


def test_normalise_drops_overlapping_and_inverted_slots() -> None:
    a = RawSlot(utc(2026, 1, 1, 0), utc(2026, 1, 1, 1), 1.0)
    overlap = RawSlot(utc(2026, 1, 1, 0, 30), utc(2026, 1, 1, 1, 30), 2.0)
    inverted = RawSlot(utc(2026, 1, 1, 5), utc(2026, 1, 1, 4), 3.0)
    slots, report = normalise([a, overlap, inverted], unreadable=2)
    assert slots == [a]
    assert report.overlaps == 2
    assert report.unreadable_values == 2


# --- segments ---------------------------------------------------------------------------


def slot(start: datetime, minutes: int | None, value: float | None) -> RawSlot:
    end = None if minutes is None else start + timedelta(minutes=minutes)
    return RawSlot(start, end, value)


def test_infer_step_uses_the_most_common_distance_and_prefers_the_finer_on_a_tie() -> None:
    t0 = utc(2026, 1, 1)
    assert infer_step([t0, t0 + Q, t0 + 2 * Q], None) == Q
    assert infer_step([t0, t0 + H], None) == H
    assert infer_step([t0, t0 + Q, t0 + Q + H], None) == Q  # tie: finer
    assert infer_step([t0], Q) == Q
    assert infer_step([t0], None) == H


def test_explicit_slots_hourly_become_one_segment_with_the_mwh_conversion() -> None:
    t0 = utc(2026, 10, 15, 22)
    slots = [slot(t0 + H * i, 60, 45.0 * (i + 1)) for i in range(3)]
    (seg,) = build_segments(slots, divisor=1000.0, resolution=None)
    assert seg.step == H
    assert seg.unit is SeriesUnit.PRICE
    assert seg.values == (0.045, 0.09, 0.135)


def test_explicit_slots_mixed_15_and_60_minutes_make_two_segments() -> None:
    t0 = utc(2026, 10, 15, 22)
    slots = [
        slot(t0, 60, 1.0),
        slot(t0 + H, 60, 2.0),
        slot(t0 + 2 * H, 15, 3.0),
        slot(t0 + 2 * H + Q, 15, 4.0),
    ]
    hourly, quarter = build_segments(slots, divisor=1.0, resolution=None)
    assert (hourly.step, hourly.values) == (H, (1.0, 2.0))
    assert (quarter.step, quarter.start, quarter.values) == (Q, t0 + 2 * H, (3.0, 4.0))


def test_explicit_slots_with_a_hole_start_a_new_segment_and_no_value_is_invented() -> None:
    t0 = utc(2026, 10, 15, 22)
    slots = [slot(t0, 60, 1.0), slot(t0 + 2 * H, 60, 3.0)]
    first, second = build_segments(slots, divisor=1.0, resolution=None)
    assert first.end == t0 + H
    assert second.start == t0 + 2 * H


def test_inferred_hourly_with_a_gap_has_none_in_the_gap() -> None:
    t0 = utc(2026, 10, 15, 22)
    slots = [slot(t0, None, 1.0), slot(t0 + H, None, 2.0), slot(t0 + 3 * H, None, 4.0)]
    (seg,) = build_segments(slots, divisor=1.0, resolution=None)
    assert seg.values == (1.0, 2.0, None, 4.0)


def test_inferred_quarter_hours_then_hourly_tomorrow() -> None:
    t0 = utc(2026, 10, 15, 22)
    slots = [slot(t0 + Q * i, None, float(i)) for i in range(8)]
    slots += [slot(t0 + 2 * H + H * i, None, 10.0 + i) for i in range(3)]
    quarter, hourly = build_segments(slots, divisor=1.0, resolution=None)
    assert quarter.step == Q
    assert len(quarter) == 8
    assert hourly.step == H
    assert hourly.values == (10.0, 11.0, 12.0)


def test_inferred_template_resolution_wins_for_a_single_point() -> None:
    (seg,) = build_segments([slot(utc(2026, 1, 1), None, 1.0)], divisor=1.0, resolution=Q)
    assert seg.step == Q


def test_irregular_timestamps_are_malformed_never_guessed() -> None:
    t0 = utc(2026, 1, 1)
    slots = [
        slot(t0, None, 1.0),
        slot(t0 + H, None, 2.0),
        slot(t0 + 2 * H, None, 3.0),
        slot(t0 + 2 * H + timedelta(minutes=20), None, 4.0),
    ]
    with pytest.raises(MalformedPayloadError):
        build_segments(slots, divisor=1.0, resolution=None)


def test_no_slots_no_segments() -> None:
    assert build_segments([], divisor=1.0, resolution=None) == ()


def test_dst_days_have_23_and_25_hours() -> None:
    """Europe/Berlin: 2026-03-29 has 23 h, 2026-10-25 has 25 h (hourly local slots)."""
    for day, hours in (
        (datetime(2026, 3, 29, tzinfo=UTC), 23),
        (datetime(2026, 10, 25, tzinfo=UTC), 25),
    ):
        start = datetime.combine(day.date(), datetime.min.time(), tzinfo=BERLIN)
        end = datetime.combine(day.date() + timedelta(days=1), datetime.min.time(), tzinfo=BERLIN)
        assert end.astimezone(UTC) - start.astimezone(UTC) == timedelta(hours=hours)
        slots = []
        t = start.astimezone(UTC)
        while t < end.astimezone(UTC):
            slots.append(slot(t, None, 0.1))
            t += H
        (seg,) = build_segments(slots, divisor=1.0, resolution=None)
        assert len(seg) == hours
        assert seg.end == end.astimezone(UTC)


def test_the_repeated_local_hour_of_the_25_hour_day_keeps_both_slots() -> None:
    """ENTSO-E prints `str(datetime)` with the offset: 02:00+02:00 and 02:00+01:00 differ."""
    slots = [
        slot(parse_timestamp("2026-10-25 01:00:00+02:00"), None, 1.0),
        slot(parse_timestamp("2026-10-25 02:00:00+02:00"), None, 2.0),
        slot(parse_timestamp("2026-10-25 02:00:00+01:00"), None, 3.0),
        slot(parse_timestamp("2026-10-25 03:00:00+01:00"), None, 4.0),
    ]
    normalised, report = normalise(slots)
    (seg,) = build_segments(normalised, divisor=1.0, resolution=None)
    assert seg.values == (1.0, 2.0, 3.0, 4.0)
    assert report.duplicates == 0


# --- clip -------------------------------------------------------------------------------


def series(start: datetime, n: int, step: timedelta = H) -> TimeSeries:
    return TimeSeries(
        start=start, step=step, values=tuple(float(i) for i in range(n)), unit=SeriesUnit.PRICE
    )


def test_clip_keeps_whole_slots_that_overlap_the_window() -> None:
    t0 = utc(2026, 1, 1)
    s = series(t0, 6)
    clipped = clip_series(s, t0 + H + timedelta(minutes=30), t0 + 4 * H - timedelta(minutes=30))
    assert clipped is not None
    assert (clipped.start, clipped.values) == (t0 + H, (1.0, 2.0, 3.0))
    assert clip_series(s, t0 + 7 * H, t0 + 8 * H) is None
    assert clip_series(s, t0 - 2 * H, t0) is None
    whole = clip_series(s, t0 - H, t0 + 10 * H)
    assert whole is not None
    assert whole.values == s.values
