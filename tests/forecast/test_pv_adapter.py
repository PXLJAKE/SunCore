# SPDX-License-Identifier: Apache-2.0
"""The template-driven PV forecast sources, from the documented fixtures (Phase 4 WP5)."""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from suncore.core.forecast import ForecastSource
from suncore.core.series import SeriesUnit, TimeRange
from suncore.forecast.pv.adapter import FetchStatus, TemplateForecastSource
from suncore.templates import TemplateCatalog
from tests.forecast.conftest import (
    BERLIN,
    FakeReader,
    forecast_template,
    local_day_slots,
    solcast_entries,
)

DAY = date(2026, 6, 21)
WINDOW = TimeRange(
    start=datetime(2026, 6, 20, 22, 0, tzinfo=UTC), end=datetime(2026, 6, 24, 22, 0, tzinfo=UTC)
)
SOLCAST_ENTITIES = {
    "forecast_today": "sensor.solcast_pv_forecast_forecast_today",
    "forecast_tomorrow": "sensor.solcast_pv_forecast_forecast_tomorrow",
    "forecast_d3": "sensor.solcast_pv_forecast_forecast_day_3",
}


def solcast(
    catalog: TemplateCatalog, reader: FakeReader, entities: dict[str, str] | None = None
) -> TemplateForecastSource:
    return TemplateForecastSource(
        forecast_template(catalog, "solcast.pv"),
        SOLCAST_ENTITIES if entities is None else entities,
        reader,
    )


def day_state(day: date, kw: Any = None) -> dict[str, Any]:
    return {"detailedForecast": solcast_entries(day, kw), "dataCorrect": True, "dayname": "x"}


async def test_the_source_satisfies_the_protocol(catalog: TemplateCatalog) -> None:
    source = solcast(catalog, FakeReader())
    assert isinstance(source, ForecastSource)
    assert source.source_id == "solcast.pv"
    assert source.watched_entities == frozenset(SOLCAST_ENTITIES.values())


async def test_solcast_joins_today_tomorrow_and_d3(catalog: TemplateCatalog) -> None:
    reader = FakeReader()
    reader.states = {
        SOLCAST_ENTITIES["forecast_today"]: day_state(DAY),
        SOLCAST_ENTITIES["forecast_tomorrow"]: day_state(DAY + timedelta(days=1)),
        SOLCAST_ENTITIES["forecast_d3"]: day_state(DAY + timedelta(days=2)),
    }
    source = solcast(catalog, reader)
    series = await source.fetch(WINDOW)
    assert series.unit is SeriesUnit.W
    assert series.step == timedelta(minutes=30)
    assert len(series) == 3 * 48
    assert series.start == datetime(2026, 6, 20, 22, tzinfo=UTC)
    assert series.p50.values[24] == 2000.0
    assert series.p10.values[24] <= 2000.0
    assert source.last_report.status is FetchStatus.OK
    assert source.last_report.entities_used == 3
    assert source.last_report.route == "attribute"
    assert reader.action_calls == []  # the attributes were enough


async def test_a_missing_day_sensor_leaves_a_gap_and_is_reported(catalog: TemplateCatalog) -> None:
    reader = FakeReader()
    reader.states = {
        SOLCAST_ENTITIES["forecast_today"]: day_state(DAY),
        SOLCAST_ENTITIES["forecast_tomorrow"]: None,  # unavailable
        SOLCAST_ENTITIES["forecast_d3"]: day_state(DAY + timedelta(days=2)),
    }
    source = solcast(catalog, reader)
    series = await source.fetch(WINDOW)
    assert len(series) == 3 * 48
    assert series.p50.values[48:96] == (None,) * 48
    assert source.last_report.status is FetchStatus.PARTIAL
    assert source.last_report.entities_missing == (SOLCAST_ENTITIES["forecast_tomorrow"],)


async def test_one_irregular_sensor_is_dropped_the_others_kept(catalog: TemplateCatalog) -> None:
    broken = day_state(DAY + timedelta(days=1))
    broken["detailedForecast"][3]["period_start"] = "2026-06-22T01:40:00+02:00"
    reader = FakeReader()
    reader.states = {
        SOLCAST_ENTITIES["forecast_today"]: day_state(DAY),
        SOLCAST_ENTITIES["forecast_tomorrow"]: broken,
    }
    source = solcast(catalog, reader)
    series = await source.fetch(WINDOW)
    assert len(series) == 48
    assert source.last_report.status is FetchStatus.PARTIAL
    assert source.last_report.dropped_entities == (SOLCAST_ENTITIES["forecast_tomorrow"],)


async def test_window_crops_the_series(catalog: TemplateCatalog) -> None:
    reader = FakeReader()
    reader.states = {SOLCAST_ENTITIES["forecast_today"]: day_state(DAY)}
    source = solcast(catalog, reader)
    noon = datetime(2026, 6, 21, 10, 0, tzinfo=UTC)  # 12:00 local
    series = await source.fetch(TimeRange(start=noon, end=noon + timedelta(hours=2)))
    assert series.start == noon
    assert len(series) == 4
    assert series.p50.values == (2000.0,) * 4


async def test_attribute_off_falls_back_to_the_query_action(catalog: TemplateCatalog) -> None:
    reader = FakeReader()
    reader.states = {SOLCAST_ENTITIES["forecast_today"]: {"dayname": "Sunday"}}  # no attribute
    reader.action_data = {"data": solcast_entries(DAY) + solcast_entries(DAY + timedelta(days=1))}
    source = solcast(catalog, reader)
    series = await source.fetch(WINDOW)
    assert len(series) == 2 * 48
    assert series.p50.values[24] == 2000.0
    assert source.last_report.route == "action"
    domain, service, data, interval = reader.action_calls[0]
    assert (domain, service) == ("solcast_solar", "query_forecast_data")
    assert data["start_date_time"] == WINDOW.start.isoformat()
    assert datetime.fromisoformat(data["end_date_time"]) == WINDOW.start + timedelta(hours=96)
    assert interval >= 60


async def test_without_any_configured_entity_nothing_is_read(catalog: TemplateCatalog) -> None:
    reader = FakeReader()
    source = solcast(catalog, reader, entities={})
    series = await source.fetch(WINDOW)
    assert len(series) == 0
    assert source.last_report.status is FetchStatus.NOT_CONFIGURED
    assert reader.action_calls == []  # no opt-in, no action


async def test_action_errors_propagate_to_the_service(catalog: TemplateCatalog) -> None:
    reader = FakeReader()
    reader.states = {SOLCAST_ENTITIES["forecast_today"]: None}
    reader.action_error = RuntimeError("rate limited")
    with pytest.raises(RuntimeError):
        await solcast(catalog, reader).fetch(WINDOW)


async def test_all_unknown_data_is_no_data_not_a_series(catalog: TemplateCatalog) -> None:
    reader = FakeReader()
    entries = solcast_entries(DAY)
    for entry in entries:
        entry["pv_estimate"] = None
    reader.states = {SOLCAST_ENTITIES["forecast_today"]: {"detailedForecast": entries}}
    reader.action_data = {"data": []}
    source = solcast(catalog, reader)
    series = await source.fetch(WINDOW)
    assert len(series) == 0
    assert source.last_report.status is FetchStatus.NO_DATA


# --- Open-Meteo Solar Forecast -----------------------------------------------------


def open_meteo_day(day: date) -> dict[str, float]:
    """``wh_period_15m`` of one local day, fixed offset of the location."""
    return {
        slot.isoformat(): (125.0 if 8 <= slot.hour < 18 else 0.0)
        for slot in local_day_slots(day, 15)
    }


async def test_open_meteo_15_minute_wh_become_w(catalog: TemplateCatalog) -> None:
    ids = {"energy_today": "sensor.today", "energy_tomorrow": "sensor.tomorrow"}
    reader = FakeReader()
    reader.states = {
        "sensor.today": {"wh_period_15m": open_meteo_day(DAY), "watts": {}},
        "sensor.tomorrow": {"wh_period_15m": open_meteo_day(DAY + timedelta(days=1))},
    }
    source = TemplateForecastSource(
        forecast_template(catalog, "open_meteo_solar_forecast.pv"), ids, reader
    )
    series = await source.fetch(WINDOW)
    assert series.step == timedelta(minutes=15)
    assert len(series) == 2 * 96
    assert series.p50.values[4 * 12] == 500.0  # 125 Wh in 15 min = 500 W
    assert series.p10 == series.p50 == series.p90  # a single deterministic estimate
    assert series.p50.total() == pytest.approx(2 * 40 * 125.0)  # energy preserved
    assert source.last_report.status is FetchStatus.OK


# --- Forecast.Solar through energy/solar_forecast -----------------------------------


def forecast_solar_hours(day: date) -> dict[str, float]:
    """Hourly Wh; only a zero hour at exactly 00:00:00 is dropped (``forecast_solar/energy.py``
    keeps ``val != 0 or (h, m, s) != (0, 0, 0)``)."""
    hours = {}
    for slot in local_day_slots(day, 60):
        wh = 1000.0 if 8 <= slot.hour < 18 else 0.0
        if wh != 0 or (slot.hour, slot.minute, slot.second) != (0, 0, 0):
            hours[slot.isoformat()] = wh
    return hours


async def test_forecast_solar_reads_the_only_entry_and_fills_the_dropped_midnight(
    catalog: TemplateCatalog,
) -> None:
    reader = FakeReader()
    reader.entries = {
        "entry1": {**forecast_solar_hours(DAY), **forecast_solar_hours(DAY + timedelta(days=1))}
    }
    source = TemplateForecastSource(forecast_template(catalog, "forecast_solar.pv"), {}, reader)
    series = await source.fetch(WINDOW)
    assert series.step == timedelta(hours=1)
    assert series.p10 == series.p90 == series.p50
    # day 1 starts at 01:00 (its 0 Wh midnight was dropped) and day 2 runs to its end
    assert len(series) == 23 + 24
    assert series.p50.values[0] == 0.0  # 01:00, a listed zero hour
    assert series.p50.values[22] == 0.0  # 23:00
    assert series.p50.values[23] == 0.0  # day 2's dropped midnight, filled between known hours
    assert series.p50.values[9] == 1000.0  # 10:00: 1000 Wh in an hour = 1000 W
    assert None not in series.p50.values
    assert source.last_report.route == "energy"


async def test_forecast_solar_continuous_hours_with_only_midnight_missing(
    catalog: TemplateCatalog,
) -> None:
    """The shape ``forecast_solar/energy.py`` really produces: every hour, zeros included, only
    the 0 Wh hour at 00:00:00 is absent."""
    hours = forecast_solar_hours(DAY + timedelta(days=1))
    stamps = sorted(datetime.fromisoformat(k) for k in hours)
    assert len(stamps) == 23
    assert stamps[0].hour == 1
    assert all(b - a == timedelta(hours=1) for a, b in itertools.pairwise(stamps))
    previous = forecast_solar_hours(DAY)
    reader = FakeReader()
    reader.entries = {"entry1": {**previous, **hours}}
    source = TemplateForecastSource(forecast_template(catalog, "forecast_solar.pv"), {}, reader)
    series = await source.fetch(WINDOW)
    midnight = datetime.combine(DAY + timedelta(days=1), datetime.min.time(), tzinfo=BERLIN)
    index = (midnight - series.start) // timedelta(hours=1)
    assert series.p50.values[index] == 0.0
    assert series.p50.total() == pytest.approx(2 * 10 * 1000.0)


async def test_forecast_solar_with_an_off_grid_key_still_delivers(
    catalog: TemplateCatalog,
) -> None:
    hours = forecast_solar_hours(DAY)
    sunrise = datetime.combine(DAY, datetime.min.time(), tzinfo=BERLIN) + timedelta(
        hours=7, minutes=41, seconds=13
    )
    reader = FakeReader()
    reader.entries = {"entry1": {**hours, sunrise.isoformat(): 250.0}}
    source = TemplateForecastSource(forecast_template(catalog, "forecast_solar.pv"), {}, reader)
    series = await source.fetch(WINDOW)
    assert series.step == timedelta(hours=1)
    assert source.last_report.status is FetchStatus.OK
    assert source.last_report.merged_points == 1
    assert series.p50.total() == pytest.approx(10 * 1000.0 + 250.0)
    seven = (sunrise.replace(minute=0, second=0) - series.start) // timedelta(hours=1)
    assert series.p50.values[seven] == 250.0  # the 07:00 hour was 0 Wh before


async def test_forecast_solar_with_several_entries_needs_a_choice(catalog: TemplateCatalog) -> None:
    reader = FakeReader()
    reader.entries = {
        "entry_a": forecast_solar_hours(DAY),
        "entry_b": {k: 2 * v for k, v in forecast_solar_hours(DAY).items()},
    }
    template = forecast_template(catalog, "forecast_solar.pv")
    ambiguous = TemplateForecastSource(template, {}, reader)
    assert len(await ambiguous.fetch(WINDOW)) == 0
    assert ambiguous.last_report.status is FetchStatus.AMBIGUOUS_ENTRY
    chosen = TemplateForecastSource(template, {}, reader, config_entry_id="entry_b")
    series = await chosen.fetch(WINDOW)
    assert series.p50.values[10] == 2000.0
    assert chosen.last_report.status is FetchStatus.OK
    missing = TemplateForecastSource(template, {}, reader, config_entry_id="entry_c")
    assert len(await missing.fetch(WINDOW)) == 0
    assert missing.last_report.status is FetchStatus.ENTRY_NOT_FOUND


async def test_forecast_solar_not_linked_in_the_energy_dashboard(catalog: TemplateCatalog) -> None:
    reader = FakeReader()  # energy/solar_forecast answers {}
    source = TemplateForecastSource(forecast_template(catalog, "forecast_solar.pv"), {}, reader)
    assert len(await source.fetch(WINDOW)) == 0
    assert source.last_report.status is FetchStatus.NOT_LINKED


async def test_half_hour_entries_are_recognised_by_their_spacing(catalog: TemplateCatalog) -> None:
    # Solcast's energy platform: half-hour keys, Wh per 30 min; the period is inferred
    reader = FakeReader()
    reader.entries = {"e": {slot.isoformat(): 500.0 for slot in local_day_slots(DAY, 30)[16:24]}}
    source = TemplateForecastSource(forecast_template(catalog, "forecast_solar.pv"), {}, reader)
    series = await source.fetch(WINDOW)
    assert series.step == timedelta(minutes=30)
    assert series.p50.values == (1000.0,) * 8  # 500 Wh per 30 min = 1000 W


# --- generic attribute: list and mapping -----------------------------------------------


async def test_generic_list_template(catalog: TemplateCatalog) -> None:
    reader = FakeReader()
    points = [
        {"start": slot.isoformat(), "power_w": 1500.0} for slot in local_day_slots(DAY, 30)[20:30]
    ]
    reader.states = {"sensor.my_forecast": {"forecast": points}}
    source = TemplateForecastSource(
        forecast_template(catalog, "generic.pv_forecast"),
        {"forecast": "sensor.my_forecast"},
        reader,
    )
    series = await source.fetch(WINDOW)
    assert len(series) == 10
    assert series.p50.values == (1500.0,) * 10


async def test_generic_mapping_template_with_a_list_path(catalog: TemplateCatalog) -> None:
    base = forecast_template(catalog, "generic.pv_forecast")
    series_spec = base.series.model_copy(
        update={
            "shape": "mapping",
            "list_path": "plan.watts",
            "fields": base.series.fields.model_copy(
                update={"timestamp_field": None, "value_field": None}
            ),
        }
    )
    template = base.model_copy(update={"series": series_spec})
    reader = FakeReader()
    stamps = local_day_slots(DAY, 30)[20:26]
    reader.states = {
        "sensor.x": {"forecast": {"plan": {"watts": {s.isoformat(): 800.0 for s in stamps}}}}
    }
    source = TemplateForecastSource(template, {"forecast": "sensor.x"}, reader)
    series = await source.fetch(WINDOW)
    assert series.p50.values == (800.0,) * 6
    assert stamps[0].tzinfo == BERLIN
