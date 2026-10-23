# SPDX-License-Identifier: Apache-2.0
"""Shared test doubles and builders for the tariff tests (no network, no wall clock)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from suncore.core.series import PriceSeries, TimeSeries
from suncore.tariff import EntityReader, EntityReading, SourceBinding, TemplatePriceSource
from suncore.templates import TariffTemplate, load_templates

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "tariff"
CATALOG = load_templates(ROOT / "templates")
BERLIN = ZoneInfo("Europe/Berlin")
LONDON = ZoneInfo("Europe/London")
NOW = datetime(2026, 10, 16, 10, 7, tzinfo=BERLIN)
"""A Friday morning, CEST."""


def fixture(name: str) -> dict[str, Any]:
    """A documented-shape fixture (``tests/fixtures/tariff/<name>.yaml``)."""
    data = yaml.safe_load((FIXTURES / f"{name}.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def template(template_id: str) -> TariffTemplate:
    found = CATALOG.get(template_id)
    assert isinstance(found, TariffTemplate), template_id
    return found


class StubActions:
    """An ``ActionCaller`` answering from a function, recording every call."""

    def __init__(self, respond: Callable[[Mapping[str, Any]], Mapping[str, Any]]) -> None:
        self._respond = respond
        self.calls: list[tuple[str, str, dict[str, Any], float, float]] = []
        self.error: Exception | None = None

    async def __call__(
        self,
        domain: str,
        service: str,
        data: Mapping[str, Any],
        *,
        min_interval_s: float,
        max_wait_s: float,
    ) -> Mapping[str, Any]:
        self.calls.append((domain, service, dict(data), min_interval_s, max_wait_s))
        if self.error is not None:
            raise self.error
        return self._respond(data)


def reader_of(entities: Mapping[str, EntityReading | None]) -> EntityReader:
    def read(entity_id: str) -> EntityReading | None:
        return entities.get(entity_id)

    return read


def readings(name: str) -> dict[str, EntityReading]:
    """Entity readings of a fixture file."""
    return {
        e["entity_id"]: EntityReading(str(e["state"]), e["attributes"])
        for e in fixture(name)["entities"]
    }


def attribute_source(
    template_id: str,
    entities: Mapping[str, str],
    reading: Mapping[str, EntityReading | None],
    *,
    now: datetime = NOW,
    **binding: Any,
) -> TemplatePriceSource:
    tz = binding.pop("tz", BERLIN)
    return TemplatePriceSource(
        template(template_id),
        SourceBinding(tz=tz, entities=entities, **binding),
        attributes=reader_of(reading),
        clock=lambda: now,
    )


def action_source(
    template_id: str,
    actions: StubActions,
    *,
    now: datetime = NOW,
    **binding: Any,
) -> TemplatePriceSource:
    tz = binding.pop("tz", BERLIN)
    return TemplatePriceSource(
        template(template_id),
        SourceBinding(tz=tz, **binding),
        action=actions,
        clock=lambda: now,
    )


def quarter_hours(
    start: datetime, count: int, first: float = 0.2, step: float = 0.001
) -> list[tuple[datetime, float]]:
    return [(start + timedelta(minutes=15 * i), round(first + step * i, 6)) for i in range(count)]


def prices(
    start: datetime,
    values: list[float | None],
    *,
    step: timedelta = timedelta(minutes=15),
    includes: str = "all_in",
    source_id: str = "t",
) -> PriceSeries:
    series = TimeSeries(start=start, step=step, values=tuple(values), unit="currency/kWh")  # type: ignore[arg-type]
    return PriceSeries(source_id=source_id, price_includes=includes, import_price=series)  # type: ignore[arg-type]
