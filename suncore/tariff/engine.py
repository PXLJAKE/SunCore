# SPDX-License-Identifier: Apache-2.0
"""Template-driven price adapter: a :class:`TariffTemplate` becomes a price source.

The template (``price_series``) says where the curve is: the response of a read-only action
(Tibber, Nord Pool) or an attribute of one or more entities (ENTSO-E, EPEX Spot, Octopus,
generic). This module knows the shapes, not the integrations: field names, units and paths
come from the template; the rules below are the contract of the schema.

* **Grid.** The native step is derived from the data: ``end - start`` per slot when the
  template names ``end_field`` (Nord Pool, EPEX Spot, Octopus), otherwise the template's
  ``resolution_min`` or the most common spacing of the timestamps (Tibber, ENTSO-E). Changes
  between 15 and 60 minutes inside a list start a new segment; missing slots are ``None``.
* **Unit.** ``/MWh`` values are divided by 1000; ``{currency}`` is the region's currency.
* **Time.** Timestamps need a UTC offset (see :mod:`suncore.tariff.parse`); request dates of
  ``date_field`` actions are local dates of the site's zone (DST: a day has 23 or 25 hours).
* **No I/O of its own.** Actions go through an injected :class:`ActionCaller`
  (``suncore.app.adapters`` is the only app module allowed to call actions), attributes
  through an injected :data:`EntityReader` of the entity cache.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any, Final, Protocol

from suncore.core.series import (
    PLANNING_STEP,
    PriceIncludes,
    PriceSeries,
    SeriesUnit,
    TimeRange,
    TimeSeries,
    floor_to_step,
)
from suncore.tariff.errors import (
    MalformedPayloadError,
    MissingEntityError,
    MissingParamError,
    SourceUnavailableError,
    UnitMismatchError,
)
from suncore.tariff.parse import (
    ParseReport,
    RawSlot,
    build_segments,
    clip_series,
    expand_path,
    normalise,
    parse_number,
    select_series,
    slots_from_mapping,
    slots_from_records,
    walk,
)
from suncore.tariff.sources import SourceData, SourceKind, finest_series
from suncore.tariff.units import (
    divisor_to_kwh,
    energy_scale,
    normalise_price_unit,
    resolve_value_unit,
)
from suncore.templates.models import ActionSeries, AttributeSeries, TariffTemplate

DEFAULT_MIN_INTERVAL_S: Final = 30.0
"""Floor between two calls of the same action (``ActionRateLimiter`` default, follow-up A3)."""


class ActionCaller(Protocol):
    """Calls a read-only action and returns its response object.

    Implemented in ``suncore.app.adapters`` on top of ``HAConnection.call_action_response``;
    it raises :class:`~suncore.tariff.errors.SourceThrottledError` for a call skipped by the
    rate limiter and :class:`~suncore.tariff.errors.SourceFailedError` for anything else.
    """

    async def __call__(
        self,
        domain: str,
        service: str,
        data: Mapping[str, Any],
        *,
        min_interval_s: float,
        max_wait_s: float,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class EntityReading:
    """State text and attributes of an entity as last seen."""

    state: str
    attributes: Mapping[str, Any]


EntityReader = Callable[[str], EntityReading | None]
"""Current reading of an entity, ``None`` while it is unknown to Home Assistant or
``unavailable``."""


@dataclass(frozen=True, slots=True)
class SourceBinding:
    """What the user's tariff configuration adds to a template (see ``ImportTariff``)."""

    tz: tzinfo
    entities: Mapping[str, str] = field(default_factory=dict)
    """Template role -> entity id (``price_entity`` is the role ``price``)."""
    action_params: Mapping[str, str | Sequence[str]] = field(default_factory=dict)
    series_key: str | None = None
    price_includes: PriceIncludes | None = None
    """The user's statement overriding the template's classification."""
    currency: str = "EUR"
    min_interval_s: float = DEFAULT_MIN_INTERVAL_S


def _local_midnight(day: date, tz: tzinfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz)


def request_dates(window: TimeRange, now: datetime, tz: tzinfo, max_horizon_h: int) -> list[date]:
    """Local delivery dates to ask a per-date action for.

    From the window's first local day to the last, but not beyond today plus
    ``ceil(max_horizon_h / 24)`` days (Nord Pool: "one day in the future" -> 24 h).
    """
    first = window.start.astimezone(tz).date()
    last = (window.end - timedelta(microseconds=1)).astimezone(tz).date()
    limit = now.astimezone(tz).date() + timedelta(days=-(-max_horizon_h // 24))
    last = min(last, limit)
    days = (last - first).days
    return [first + timedelta(days=i) for i in range(max(0, days + 1))]


class TemplatePriceSource:
    """A :class:`SegmentSource` (and ``PriceSource``) built from a dynamic tariff template."""

    def __init__(
        self,
        template: TariffTemplate,
        binding: SourceBinding,
        *,
        action: ActionCaller | None = None,
        attributes: EntityReader | None = None,
        clock: Callable[[], datetime],
    ) -> None:
        series = template.price_series
        if template.tariff_type != "dynamic" or series is None:
            msg = f"template {template.id} is not a dynamic tariff with a price_series"
            raise ValueError(msg)
        if isinstance(series, ActionSeries) and action is None:
            msg = f"template {template.id} reads an action: an ActionCaller is required"
            raise ValueError(msg)
        if isinstance(series, AttributeSeries) and attributes is None:
            msg = f"template {template.id} reads attributes: an EntityReader is required"
            raise ValueError(msg)
        self._template = template
        self._series = series
        self._binding = binding
        self._action = action
        self._attributes = attributes
        self._clock = clock
        self._unit = resolve_value_unit(series.fields.value_unit, binding.currency)
        self._divisor = divisor_to_kwh(self._unit)
        self._resolution = (
            None if template.resolution_min is None else timedelta(minutes=template.resolution_min)
        )
        assert template.price_includes is not None  # validated: required for dynamic
        self._includes: PriceIncludes = binding.price_includes or template.price_includes

    # --- SegmentSource ---------------------------------------------------------------

    @property
    def source_id(self) -> str:
        return self._template.id

    @property
    def kind(self) -> SourceKind:
        return "dynamic"

    @property
    def price_includes(self) -> PriceIncludes:
        return self._includes

    @property
    def uses_action(self) -> bool:
        return isinstance(self._series, ActionSeries)

    @property
    def watched_entities(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self._binding.entities.values()))

    async def fetch_segments(self, window: TimeRange, now: datetime) -> SourceData:
        if isinstance(self._series, ActionSeries):
            slots, bad = await self._from_action(self._series, window, now)
        else:
            slots, bad = self._from_attributes(self._series)
        normalised, report = normalise(slots, unreadable=bad)
        series = build_segments(normalised, divisor=self._divisor, resolution=self._resolution)
        clipped = [c for s in series if (c := clip_series(s, window.start, window.end))]
        return SourceData(
            segments=tuple(self._price_series(s) for s in clipped),
            report=ParseReport(
                points=report.points,
                unreadable_values=report.unreadable_values,
                duplicates=report.duplicates,
                overlaps=report.overlaps,
            ),
        )

    async def fetch(self, window: TimeRange) -> PriceSeries:
        """``PriceSource`` protocol: one series on the finest native step."""
        data = await self.fetch_segments(window, self._clock())
        return finest_series(self.source_id, self._includes, data.segments, window)

    def _price_series(self, series: TimeSeries) -> PriceSeries:
        return PriceSeries(
            source_id=self.source_id, price_includes=self._includes, import_price=series
        )

    # --- action ----------------------------------------------------------------------

    def _request_base(self, series: ActionSeries) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for param in series.params:
            value = self._binding.action_params.get(param.name)
            if value is None:
                if param.required:
                    raise MissingParamError(
                        f"action field `{param.name}` is not configured", field=param.name
                    )
                continue
            data[param.name] = value if isinstance(value, str) else list(value)
        return data

    async def _from_action(
        self, series: ActionSeries, window: TimeRange, now: datetime
    ) -> tuple[list[RawSlot], int]:
        assert self._action is not None
        tz = self._binding.tz
        base = self._request_base(series)
        requests: list[dict[str, Any]]
        if series.date_field is not None:
            dates = request_dates(window, now, tz, series.max_horizon_h)
            requests = [{**base, series.date_field: d.isoformat()} for d in dates]
        else:
            data = dict(base)
            if series.start_field is not None:
                first = window.start.astimezone(tz).date()
                data[series.start_field] = _local_midnight(first, tz).isoformat()
            if series.end_field is not None:
                last = (window.end - timedelta(microseconds=1)).astimezone(tz).date()
                data[series.end_field] = _local_midnight(last + timedelta(days=1), tz).isoformat()
            requests = [data]
        slots: list[RawSlot] = []
        bad = 0
        for index, data in enumerate(requests):
            response = await self._action(
                series.domain,
                series.service,
                data,
                min_interval_s=self._binding.min_interval_s,
                # the second and later dates wait for the limiter's next slot
                max_wait_s=0.0 if index == 0 else self._binding.min_interval_s + 1.0,
            )
            candidates = expand_path(response, series.response_path)
            node = select_series(candidates, self._binding.series_key)
            if node is None:
                continue
            part, unreadable = slots_from_records(node, series.fields)
            slots.extend(part)
            bad += unreadable
        return slots, bad

    # --- attributes ------------------------------------------------------------------

    def _check_unit(self, attrs: Mapping[str, Any]) -> None:
        """Refuse an entity whose unit says per MWh where the template says per kWh (or back).

        A silent factor 1000 would poison every plan; an entity without a price unit
        (event entities, template sensors) is not checked.
        """
        raw = attrs.get("unit_of_measurement")
        if not isinstance(raw, str):
            return
        seen = energy_scale(normalise_price_unit(raw))
        expected = energy_scale(self._unit)
        if seen is not None and expected is not None and seen != expected:
            raise UnitMismatchError(
                f"sensor unit {raw!r} vs template unit {self._unit!r}",
                sensor_unit=raw,
                template_unit=self._unit,
            )

    def _from_attributes(self, series: AttributeSeries) -> tuple[list[RawSlot], int]:
        assert self._attributes is not None
        slots: list[RawSlot] = []
        bad = 0
        seen_entity = False
        found_attribute = False
        for role in series.roles:
            entity_id = self._binding.entities.get(role)
            if entity_id is None:
                if self._template.roles[role].required:
                    raise MissingEntityError(f"no entity for role `{role}`", role=role)
                continue
            reading = self._attributes(entity_id)
            if reading is None:
                continue
            attrs = reading.attributes
            seen_entity = True
            self._check_unit(attrs)
            value = attrs.get(series.attribute)
            if value is None:
                continue
            found_attribute = True
            if series.list_path is not None:
                value = walk(value, series.list_path)
            if series.shape == "mapping":
                part, unreadable = slots_from_mapping(value)
            else:
                part, unreadable = slots_from_records(value, series.fields)
            slots.extend(part)
            bad += unreadable
        if not slots and not seen_entity:
            raise SourceUnavailableError("no price entity is available")
        if not slots and not found_attribute:
            raise MalformedPayloadError(
                f"attribute `{series.attribute}` is missing on the price entity",
                attribute=series.attribute,
            )
        return slots, bad


class CurrentPriceSource:
    """A dynamic tariff with only a price sensor (no template): the current slot's price.

    The state is the price of the running 15-minute slot (units per kWh, as the wizard asks
    for); nothing is known about later slots, so the series is one slot long. Better than no
    price, much worse than a curve - the wizard recommends a template.
    """

    def __init__(
        self,
        entity_id: str,
        reader: EntityReader,
        *,
        price_includes: PriceIncludes,
        clock: Callable[[], datetime],
    ) -> None:
        self._entity_id = entity_id
        self._reader = reader
        self._includes = price_includes
        self._clock = clock

    @property
    def source_id(self) -> str:
        return "entity"

    @property
    def kind(self) -> SourceKind:
        return "dynamic"

    @property
    def price_includes(self) -> PriceIncludes:
        return self._includes

    @property
    def uses_action(self) -> bool:
        return False

    @property
    def watched_entities(self) -> tuple[str, ...]:
        return (self._entity_id,)

    async def fetch_segments(self, window: TimeRange, now: datetime) -> SourceData:  # noqa: ARG002
        reading = self._reader(self._entity_id)
        if reading is None:
            raise SourceUnavailableError("the price sensor is not available")
        value = parse_number(reading.state)
        if value is None:
            raise SourceUnavailableError("the price sensor has no numeric state")
        divisor = 1.0
        unit = reading.attributes.get("unit_of_measurement")
        if isinstance(unit, str) and energy_scale(normalise_price_unit(unit)) == "MWh":
            divisor = 1000.0
        series = TimeSeries(
            start=floor_to_step(now),
            step=PLANNING_STEP,
            values=(value / divisor,),
            unit=SeriesUnit.PRICE,
        )
        price = PriceSeries(
            source_id=self.source_id, price_includes=self._includes, import_price=series
        )
        return SourceData(segments=(price,), report=ParseReport(points=1))

    async def fetch(self, window: TimeRange) -> PriceSeries:
        data = await self.fetch_segments(window, self._clock())
        return data.segments[0]
