# SPDX-License-Identifier: Apache-2.0
"""Template-driven PV forecast source (Phase 4 WP5).

One :class:`TemplateForecastSource` per configured forecast template. It reads the points the
template names - an attribute of the day sensors, the response of a read-only action, or the
Energy dashboard's ``energy/solar_forecast`` - through an injected :class:`PvDataReader` and
returns a :class:`~suncore.core.series.QuantileSeries` in W on the source's native grid
(Solcast 30 min, Open-Meteo Solar Forecast 15 min, Forecast.Solar as spaced by its keys). The
source does no I/O of its own, never writes, and never raises for missing data: it reports what
it found in :attr:`TemplateForecastSource.last_report`.

Joining the day sensors: the points of ``today``, ``tomorrow``, ``d3`` ... (in the order of the
template's ``series.roles``) are one series; a day whose sensor is unavailable or empty leaves
a gap of ``None`` slots, never a copy of another day. A sensor whose timestamps are irregular
is dropped as a whole (and reported); the others are kept.

Terms of use: the sources are Home Assistant integrations the user runs; SunCore calls neither
the Solcast API nor Open-Meteo (the built-in fallback is not shipped in Phase 4).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum, unique
from typing import Any, Final, Protocol, runtime_checkable

import structlog

from suncore.core.series import QuantileSeries, TimeRange
from suncore.core.units import Unit, parse_unit
from suncore.forecast.pv.parsing import (
    ParsedPoints,
    RawPoint,
    build_quantiles,
    crop,
    dig,
    dig_response,
    empty_series,
    has_known,
    infer_step,
    list_points,
    mapping_points,
)
from suncore.templates.models import (
    ActionSeries,
    AttributeSeries,
    EnergyDashboardSeries,
    ForecastTemplate,
)

log = structlog.get_logger(__name__)

ACTION_MIN_INTERVAL_S: Final = 600.0
"""Rate limit of the fallback action (it uses no Solcast API calls, but stay polite)."""


@unique
class FetchStatus(StrEnum):
    """What a fetch found (rendered as ``forecast.status.<value>`` in the UI)."""

    OK = "ok"
    PARTIAL = "partial"
    """Data, but a configured sensor was missing, unavailable or irregular."""
    NO_DATA = "no_data"
    NOT_CONFIGURED = "not_configured"
    """No entity of the template's roles is configured."""
    NOT_LINKED = "not_linked"
    """``energy/solar_forecast`` lists no forecast (not linked in the Energy dashboard)."""
    AMBIGUOUS_ENTRY = "ambiguous_entry"
    """Several entries and none chosen (``config_entry_id``): SunCore does not guess."""
    ENTRY_NOT_FOUND = "entry_not_found"
    """The chosen ``config_entry_id`` is not in the command's result."""
    ERROR = "error"
    """Set by the service when a fetch raised."""


@dataclass(frozen=True, slots=True)
class FetchReport:
    """Diagnostics of the latest fetch of one source."""

    status: FetchStatus
    route: str = "attribute"
    """``attribute``, ``action`` (fallback) or ``energy``."""
    entities_used: int = 0
    entities_missing: tuple[str, ...] = ()
    skipped_points: int = 0
    dropped_entities: tuple[str, ...] = ()
    """Sensors whose timestamps were irregular."""


@runtime_checkable
class PvDataReader(Protocol):
    """What a forecast source may read (implemented in ``suncore.app.adapters``)."""

    def attributes(self, entity_id: str) -> Mapping[str, Any] | None:
        """Attributes of an entity's current state; ``None`` if unknown or unavailable."""
        ...

    async def solar_forecast(self) -> Mapping[str, Mapping[str, float]]:
        """``energy/solar_forecast``: ``{config_entry_id: {iso timestamp: Wh}}``."""
        ...

    async def action_response(
        self, domain: str, service: str, data: Mapping[str, Any], *, min_interval_s: float
    ) -> Mapping[str, Any]:
        """A read-only action with ``return_response`` (rate limited by the connection)."""
        ...


@dataclass(slots=True)
class _Collected:
    points: list[RawPoint] = field(default_factory=list)
    skipped: int = 0
    used: int = 0
    missing: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def _try_build(
    points: tuple[RawPoint, ...], unit: Unit, step: timedelta, *, missing_is_zero: bool = False
) -> QuantileSeries | None:
    try:
        return build_quantiles(points, unit, step, missing_is_zero=missing_is_zero)
    except ValueError:
        return None


class TemplateForecastSource:
    """A :class:`~suncore.core.forecast.ForecastSource` built from a forecast template."""

    def __init__(
        self,
        template: ForecastTemplate,
        entities: Mapping[str, str],
        reader: PvDataReader,
        *,
        config_entry_id: str | None = None,
    ) -> None:
        """``entities`` maps the template's roles to entity ids; ``config_entry_id`` binds a
        ``energy_solar_forecast`` template to its entry in ``energy/solar_forecast``."""
        self._template = template
        self._entities = dict(entities)
        self._reader = reader
        self._config_entry_id = config_entry_id
        self._step = timedelta(minutes=template.resolution_min)
        self.last_report = FetchReport(FetchStatus.NO_DATA)

    @property
    def source_id(self) -> str:
        return self._template.id

    @property
    def template(self) -> ForecastTemplate:
        return self._template

    @property
    def watched_entities(self) -> frozenset[str]:
        """Entities whose change should trigger a refresh."""
        return frozenset(self._entities.values())

    async def fetch(self, window: TimeRange) -> QuantileSeries:
        """The forecast for ``window``: what the source has, ``None`` slots where it has not.

        Shorter than the window if the source ends earlier; an empty series (and a report with
        the reason) if it has nothing for the window.
        """
        series = self._template.series
        if isinstance(series, EnergyDashboardSeries):
            result, report = await self._from_energy(series)
        else:
            result, report = await self._from_series(series, window)
            fallback = self._template.fallback_series
            if (
                fallback is not None
                and report.status is not FetchStatus.NOT_CONFIGURED
                and (result is None or not has_known(result))
            ):
                action, action_report = await self._from_series(fallback, window)
                if action is not None and has_known(action):
                    result, report = action, action_report
        cropped = None if result is None else crop(result, window.start, window.end)
        if cropped is None or not has_known(cropped):
            keep = (
                FetchStatus.NOT_CONFIGURED,
                FetchStatus.NOT_LINKED,
                FetchStatus.AMBIGUOUS_ENTRY,
                FetchStatus.ENTRY_NOT_FOUND,
            )
            status = report.status if report.status in keep else FetchStatus.NO_DATA
            self.last_report = FetchReport(
                status,
                report.route,
                report.entities_used,
                report.entities_missing,
                report.skipped_points,
                report.dropped_entities,
            )
            return empty_series(window.start, self._step if result is None else result.step)
        self.last_report = report
        return cropped

    # --- routes ---------------------------------------------------------------------

    async def _from_series(
        self, series: AttributeSeries | ActionSeries, window: TimeRange
    ) -> tuple[QuantileSeries | None, FetchReport]:
        unit = parse_unit(series.fields.value_unit)
        assert unit is not None  # validated by the template
        if isinstance(series, AttributeSeries):
            return self._from_attributes(series, unit)
        return await self._from_action(series, unit, window)

    def _from_attributes(
        self, series: AttributeSeries, unit: Unit
    ) -> tuple[QuantileSeries | None, FetchReport]:
        collected = _Collected()
        configured = [role for role in series.roles if role in self._entities]
        if not configured:
            return None, FetchReport(FetchStatus.NOT_CONFIGURED)
        for role in configured:
            entity_id = self._entities[role]
            attrs = self._reader.attributes(entity_id)
            raw = None if attrs is None else attrs.get(series.attribute)
            parsed = self._parse(series, dig(raw, series.list_path))
            if not parsed.points:
                collected.missing.append(entity_id)
                collected.skipped += parsed.skipped
                continue
            if _try_build(parsed.points, unit, self._step) is None:
                collected.dropped.append(entity_id)  # irregular timestamps: refuse this sensor
                continue
            collected.points.extend(parsed.points)
            collected.skipped += parsed.skipped
            collected.used += 1
        return self._join(collected, unit, "attribute", self._step)

    async def _from_action(
        self, series: ActionSeries, unit: Unit, window: TimeRange
    ) -> tuple[QuantileSeries | None, FetchReport]:
        end = min(window.end, window.start + timedelta(hours=series.max_horizon_h))
        data: dict[str, Any] = {}
        if series.start_field is not None:
            data[series.start_field] = window.start.isoformat()
        if series.end_field is not None:
            data[series.end_field] = end.isoformat()
        response = await self._reader.action_response(
            series.domain, series.service, data, min_interval_s=ACTION_MIN_INTERVAL_S
        )
        parsed = list_points(dig_response(response, series.response_path), series.fields)
        collected = _Collected(points=list(parsed.points), skipped=parsed.skipped, used=1)
        if not parsed.points:
            collected.used = 0
        return self._join(collected, unit, "action", self._step)

    async def _from_energy(
        self, series: EnergyDashboardSeries
    ) -> tuple[QuantileSeries | None, FetchReport]:
        unit = parse_unit(series.fields.value_unit)
        assert unit is not None
        entries = await self._reader.solar_forecast()
        if not entries:
            return None, FetchReport(FetchStatus.NOT_LINKED, "energy")
        if self._config_entry_id is not None:
            chosen = entries.get(self._config_entry_id)
            if chosen is None:
                return None, FetchReport(FetchStatus.ENTRY_NOT_FOUND, "energy")
        elif len(entries) == 1:
            chosen = next(iter(entries.values()))
        else:
            return None, FetchReport(FetchStatus.AMBIGUOUS_ENTRY, "energy")
        parsed = mapping_points(chosen)
        step = infer_step(parsed.points, self._step)
        collected = _Collected(
            points=list(parsed.points), skipped=parsed.skipped, used=int(bool(parsed.points))
        )
        return self._join(collected, unit, "energy", step, missing_is_zero=series.missing_is_zero)

    # --- helpers --------------------------------------------------------------------

    @staticmethod
    def _parse(series: AttributeSeries, raw: object) -> ParsedPoints:
        if series.shape == "mapping":
            return mapping_points(raw)
        return list_points(raw, series.fields)

    @staticmethod
    def _join(
        collected: _Collected,
        unit: Unit,
        route: str,
        step: timedelta,
        *,
        missing_is_zero: bool = False,
    ) -> tuple[QuantileSeries | None, FetchReport]:
        built = (
            _try_build(tuple(collected.points), unit, step, missing_is_zero=missing_is_zero)
            if collected.points
            else None
        )
        if built is None and collected.points:
            # Sensors that are regular alone but not together (offset grids): refuse the result.
            log.warning("forecast.pv.irregular_join", route=route)
        incomplete = bool(collected.missing or collected.dropped)
        status = FetchStatus.NO_DATA
        if built is not None and has_known(built):
            status = FetchStatus.PARTIAL if incomplete else FetchStatus.OK
        report = FetchReport(
            status,
            route,
            collected.used,
            tuple(collected.missing),
            collected.skipped,
            tuple(collected.dropped),
        )
        return built, report


__all__ = [
    "ACTION_MIN_INTERVAL_S",
    "FetchReport",
    "FetchStatus",
    "PvDataReader",
    "TemplateForecastSource",
]
