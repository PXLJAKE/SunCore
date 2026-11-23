# SPDX-License-Identifier: Apache-2.0
"""Fixtures of the PV forecast tests: documented point shapes and a scripted reader.

The shapes are the ones ``docs/research/integrations.md`` "PV forecasts" documents:

* Solcast ``detailedForecast`` (research lines 36-47): entries ``{"period_start": <tz-aware
  datetime>, "pv_estimate": f, "pv_estimate10": f, "pv_estimate90": f}``, kW average power over
  30 minutes, one sensor per local day (``today``, ``tomorrow``, ``total_kwh_forecast_d3`` ...),
  ``period_start`` in Home Assistant's time zone with the local offset;
* Solcast action ``query_forecast_data`` (lines 67-74): ``{"data": [same entries]}``;
* Forecast.Solar via ``energy/solar_forecast`` (lines 92-109): ``{"wh_hours": {iso: Wh}}``,
  hourly, only a zero value at exactly 00:00:00 dropped;
* Open-Meteo Solar Forecast ``wh_period_15m`` (lines 133-144): ``{iso interval start: Wh}``,
  15 minutes, fixed UTC offset of the location.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from suncore.templates import ForecastTemplate, TemplateCatalog, load_templates

ROOT = Path(__file__).resolve().parents[2]
BERLIN = ZoneInfo("Europe/Berlin")


@pytest.fixture(scope="session")
def catalog() -> TemplateCatalog:
    return load_templates(ROOT / "templates")


def forecast_template(catalog: TemplateCatalog, template_id: str) -> ForecastTemplate:
    template = catalog.get(template_id)
    assert isinstance(template, ForecastTemplate)
    return template


def local_day_slots(day: date, step_min: int, tz: ZoneInfo = BERLIN) -> list[datetime]:
    """Start of every slot of one local day, as aware datetimes in ``tz``.

    Walked in absolute time, so a DST day has 23 or 25 hours: 46 / 50 half-hour slots.
    """
    begin = datetime.combine(day, datetime.min.time(), tzinfo=tz).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(UTC)
    slots = []
    moment = begin
    while moment < end:
        slots.append(moment.astimezone(tz))
        moment += timedelta(minutes=step_min)
    return slots


def solcast_entries(
    day: date, kw: Callable[[datetime], float] | None = None, tz: ZoneInfo = BERLIN
) -> list[dict[str, Any]]:
    """``detailedForecast`` of one local day: P10 = 0.6 x P50, P90 = 1.2 x P50 (kW)."""
    value = kw or (lambda s: 2.0 if 8 <= s.hour < 18 else 0.0)
    return [
        {
            "period_start": slot.isoformat(),
            "pv_estimate": value(slot),
            "pv_estimate10": round(value(slot) * 0.6, 6),
            "pv_estimate90": round(value(slot) * 1.2, 6),
        }
        for slot in local_day_slots(day, 30, tz)
    ]


class FakeReader:
    """A scripted :class:`~suncore.forecast.pv.adapter.PvDataReader`."""

    def __init__(self) -> None:
        self.states: dict[str, Mapping[str, Any] | None] = {}
        self.entries: dict[str, dict[str, float]] = {}
        self.action_data: Any = {}
        self.action_calls: list[tuple[str, str, dict[str, Any], float]] = []
        self.action_error: Exception | None = None
        self.solar_calls = 0

    def attributes(self, entity_id: str) -> Mapping[str, Any] | None:
        return self.states.get(entity_id)

    async def solar_forecast(self) -> Mapping[str, Mapping[str, float]]:
        self.solar_calls += 1
        return self.entries

    async def action_response(
        self, domain: str, service: str, data: Mapping[str, Any], *, min_interval_s: float
    ) -> Mapping[str, Any]:
        self.action_calls.append((domain, service, dict(data), min_interval_s))
        if self.action_error is not None:
            raise self.action_error
        return self.action_data
