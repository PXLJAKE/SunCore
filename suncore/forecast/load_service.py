# SPDX-License-Identifier: Apache-2.0
"""The load forecast service: history, day types, temperature, refresh, store (Phase 4 WP6).

* **Fit** (on start, then at most hourly and after the nightly job): read the last
  ``HISTORY_DAYS`` of ``site.house_w`` and every configured device's ``device.<id>.power_w``
  (15-minute means), form the base load (:func:`suncore.forecast.load.base_load`), resolve the
  day types and fit :class:`~suncore.forecast.load.LoadModel`. The device histories are kept
  for the planner (4-F) - controlled devices are never part of the base load.
* **Day types** with a mapped ``workday`` binary sensor: today from its state, every other
  date from the read-only action ``workday.check_date`` (rate limited per date, at most
  ``MAX_CHECKS_PER_RUN`` calls per refresh, forecast days first, then the history newest
  first); the answers are cached in ``learned_param`` (namespace ``load_profile``, key
  ``day_types``) so each date is asked once. A date without an answer (no sensor, the action
  failed or is rate limited) uses the weekday rule and is asked again later.
* **Temperature** with a mapped ``weather`` entity: its ``temperature`` attribute is recorded
  with every refresh (key ``weather.temperature_c``, °C) - Home Assistant keeps no long-term
  statistics of a weather entity, so the term learns from SunCore's own recording and becomes
  active after 14 days; the hourly forecast (``weather.get_forecasts``) gives the forecast
  days' mean temperatures (today combines the recorded hours with the forecast ones).
* **Refresh** every 15 minutes: the forecast from the current slot for 48 h; it is stored in
  ``timeseries.db`` (kind ``load``, source ``load_profile``) at most hourly.
* **Nightly**: accuracy (MAE and bias = actual - forecast per horizon bucket, all quarter
  hours) of ``load_profile`` against the measured base load, then a refit.

Storage and Home Assistant are reached only through protocols (the composition root passes
the time-series store, the learned-parameter store and an adapter); time and sleeping are
injected. Failures are logged and never raised out of the loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any, Final, Literal, Protocol

import structlog

from suncore.core.forecast import AccuracyRecord
from suncore.core.measurements import SITE_HOUSE_W, WEATHER_TEMPERATURE_C, device_power_key
from suncore.core.series import (
    PLANNING_STEP,
    QuantileSeries,
    SeriesUnit,
    TimeRange,
    TimeSeries,
    floor_to_step,
    planning_grid,
)
from suncore.forecast.accuracy import AccuracyData, LearnedParams, update_accuracy
from suncore.forecast.days import local_midnight
from suncore.forecast.load import (
    HISTORY_DAYS,
    LOAD_SOURCE_ID,
    BaseLoad,
    DayInfo,
    DayType,
    DayTypeSource,
    LoadModel,
    LoadQuality,
    base_load,
    daily_mean_temperatures,
    day_type_from_workday,
    fit_load_model,
    forecast_load,
    weekday_rule,
)
from suncore.forecast.views import (
    MAX_VIEW_HOURS,
    LoadDayView,
    LoadForecastView,
    LoadTemperatureView,
)

log = structlog.get_logger(__name__)

REFRESH_INTERVAL_S: Final = 900.0
STORE_INTERVAL_S: Final = 3600.0
REFIT_INTERVAL_S: Final = 3600.0
WEATHER_INTERVAL_S: Final = 3600.0
HORIZON: Final = timedelta(hours=48)
MAX_CHECKS_PER_RUN: Final = 12
CHECK_DATE_INTERVAL_S: Final = 6 * 3600.0
"""Rate-limit interval of ``workday.check_date`` per date (each date has its own slot)."""
NS_LOAD_PROFILE: Final = "load_profile"
"""``learned_param`` namespace (equal to ``suncore.storage.learned.NS_LOAD_PROFILE``)."""
DAY_TYPES_KEY: Final = "day_types"
DAY_TYPES_SCHEMA: Final = 1


class LoadData(AccuracyData, Protocol):
    """The part of ``TimeseriesStore`` the load forecast uses."""

    async def store_forecast(
        self,
        source_id: str,
        kind: Literal["pv", "load", "price"],
        issued_at: datetime,
        forecast: QuantileSeries | TimeSeries,
    ) -> int: ...

    async def append_measurements(
        self, rows: Iterable[tuple[str, datetime, float | None]]
    ) -> int: ...


class LoadHaReader(Protocol):
    """What the load forecast reads from Home Assistant (implemented in ``suncore.app``)."""

    def workday_today(self, entity_id: str) -> bool | None:
        """The ``workday`` sensor's state (``on`` = workday); ``None`` if unknown/shifted."""
        ...

    async def check_workday(self, entity_id: str, day: date, *, min_interval_s: float) -> bool:
        """``workday.check_date`` for ``day``; raises on any failure (rate limit included)."""
        ...

    def outside_temperature(self, entity_id: str) -> float | None:
        """The weather entity's current temperature in °C (``None``: unknown)."""
        ...

    async def hourly_temperatures(
        self, entity_id: str, *, min_interval_s: float
    ) -> list[tuple[datetime, float]]:
        """The hourly forecast temperatures (°C); raises on failure."""
        ...


@dataclass(frozen=True, slots=True)
class LoadInputs:
    """Which entities and devices the service uses (from ``forecast.load`` and ``devices``)."""

    device_ids: tuple[str, ...] = ()
    workday_entity: str | None = None
    weather_entity: str | None = None


class LoadForecastService:
    """Fits, refreshes, stores and serves the base load forecast."""

    def __init__(
        self,
        data: LoadData | None,
        learned: LearnedParams | None,
        *,
        clock: Callable[[], datetime],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        tz: Callable[[], tzinfo] = lambda: UTC,
        inputs: LoadInputs | None = None,
        reader: LoadHaReader | None = None,
        refresh_interval_s: float = REFRESH_INTERVAL_S,
        store_interval_s: float = STORE_INTERVAL_S,
        refit_interval_s: float = REFIT_INTERVAL_S,
        weather_interval_s: float = WEATHER_INTERVAL_S,
        horizon: timedelta = HORIZON,
        history_days: int = HISTORY_DAYS,
    ) -> None:
        self._data = data
        self._learned = learned
        self._clock = clock
        self._sleep = sleep
        self._tz = tz
        self._inputs = inputs or LoadInputs()
        self._reader = reader
        self._refresh_interval_s = refresh_interval_s
        self._store_interval = timedelta(seconds=store_interval_s)
        self._refit_interval = timedelta(seconds=refit_interval_s)
        self._weather_interval = timedelta(seconds=weather_interval_s)
        self._horizon = horizon
        self._history_days = history_days
        self._model: LoadModel | None = None
        self._fitted_at: datetime | None = None
        self._base: BaseLoad | None = None
        self._devices: dict[str, TimeSeries] = {}
        self._cache: dict[date, DayInfo] = {}
        self._cache_loaded = False
        self._weather_points: list[tuple[datetime, float]] = []
        self._weather_at: datetime | None = None
        self._observed: list[tuple[datetime, float]] = []
        """Today's recorded outside temperatures (from the fit's read plus every refresh)."""
        self._last_stored_at: datetime | None = None
        self._lock = asyncio.Lock()

    # --- inputs ---------------------------------------------------------------------

    @property
    def inputs(self) -> LoadInputs:
        return self._inputs

    @property
    def model(self) -> LoadModel | None:
        return self._model

    @property
    def device_histories(self) -> Mapping[str, TimeSeries]:
        """The controlled devices' measured power (15 minutes, W) of the fit window."""
        return dict(self._devices)

    # --- day types ------------------------------------------------------------------

    def day_info(self, day: date, today: date | None = None) -> DayInfo:
        """The day type of ``day`` and where it came from (module doc)."""
        entity = self._inputs.workday_entity
        if entity is not None and self._reader is not None:
            if day == today:
                state = self._reader.workday_today(entity)
                if state is not None:
                    return DayInfo(day_type_from_workday(day, state), DayTypeSource.WORKDAY_SENSOR)
            cached = self._cache.get(day)
            if cached is not None:
                return cached
        return DayInfo(weekday_rule(day), DayTypeSource.WEEKDAY_RULE)

    def day_types(self, days: Iterable[date], today: date) -> dict[date, DayType]:
        return {d: self.day_info(d, today).day_type for d in days}

    async def _load_cache(self) -> None:
        if self._cache_loaded or self._learned is None:
            self._cache_loaded = True
            return
        self._cache_loaded = True
        try:
            for entry in await self._learned.in_namespace(NS_LOAD_PROFILE):
                if entry.key == DAY_TYPES_KEY and entry.schema_version == DAY_TYPES_SCHEMA:
                    self._cache.update(_decode_days(entry.payload))
        except Exception:
            log.exception("forecast.load.day_types_unreadable")

    async def _save_cache(self, today: date) -> None:
        if self._learned is None:
            return
        oldest = today - timedelta(days=self._history_days + 7)
        self._cache = {d: v for d, v in self._cache.items() if d >= oldest}
        payload = {
            "days": {
                d.isoformat(): [v.day_type.value, v.source.value]
                for d, v in sorted(self._cache.items())
            }
        }
        try:
            await self._learned.put(
                NS_LOAD_PROFILE, DAY_TYPES_KEY, payload, schema_version=DAY_TYPES_SCHEMA
            )
        except Exception:
            log.exception("forecast.load.day_types_store_failed")

    async def _resolve(self, wanted: Sequence[date], today: date) -> None:
        """Ask ``workday.check_date`` for dates without a cached answer (bounded per run)."""
        entity = self._inputs.workday_entity
        if entity is None or self._reader is None:
            return
        changed = False
        state = self._reader.workday_today(entity)
        if state is not None:
            info = DayInfo(day_type_from_workday(today, state), DayTypeSource.WORKDAY_SENSOR)
            if self._cache.get(today) != info:
                self._cache[today] = info
                changed = True
        asked = 0
        for day in wanted:
            if day in self._cache or asked >= MAX_CHECKS_PER_RUN:
                continue
            asked += 1
            try:
                workday = await self._reader.check_workday(
                    entity, day, min_interval_s=CHECK_DATE_INTERVAL_S
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.info("forecast.load.check_date_failed", day=day.isoformat(), error=repr(exc))
                break  # rate limited or unsupported: the weekday rule until the next run
            self._cache[day] = DayInfo(
                day_type_from_workday(day, workday), DayTypeSource.CHECK_DATE
            )
            changed = True
        if changed:
            await self._save_cache(today)

    # --- history ----------------------------------------------------------------------

    async def base_load_series(self, window: TimeRange) -> TimeSeries:
        """The measured base load (W, 15 minutes) in ``window`` (house minus devices)."""
        result, _ = await self._read_base(window)
        return result.series

    async def _read_base(self, window: TimeRange) -> tuple[BaseLoad, dict[str, TimeSeries]]:
        assert self._data is not None
        house = await self._data.read_series(SITE_HOUSE_W, window, "15m", SeriesUnit.W)
        devices = {
            device_id: await self._data.read_series(
                device_power_key(device_id), window, "15m", SeriesUnit.W
            )
            for device_id in self._inputs.device_ids
        }
        return base_load(house, devices), devices

    async def refit(self, now: datetime | None = None) -> LoadModel | None:
        """Read the history and fit the model (kept on failure)."""
        if self._data is None:
            return None
        moment = self._clock() if now is None else now
        tz = self._tz()
        await self._load_cache()
        today = moment.astimezone(tz).date()
        end = floor_to_step(moment, PLANNING_STEP)
        start = local_midnight(moment, tz).astimezone(UTC) - timedelta(days=self._history_days)
        window = TimeRange(start=start, end=end)
        history_days = [today - timedelta(days=i) for i in range(1, self._history_days + 1)]
        await self._resolve([*self._forecast_days(moment, tz)[1:], *history_days], today)
        try:
            base, devices = await self._read_base(window)
            temperature = None
            if self._inputs.weather_entity is not None:
                temperature = await self._data.read_series(
                    WEATHER_TEMPERATURE_C, window, "15m", SeriesUnit.CELSIUS
                )
            model = fit_load_model(
                base.series,
                now=moment,
                tz=tz,
                day_types=self.day_types(history_days, today),
                temperature=temperature,
                temperature_configured=self._inputs.weather_entity is not None,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("forecast.load.fit_failed")
            return self._model
        self._model, self._fitted_at = model, moment
        self._base, self._devices = base, devices
        if temperature is not None:
            midnight = local_midnight(moment, tz)
            live = [(t, v) for t, v in self._observed if t >= end]  # not aggregated yet
            self._observed = [
                (begin, v)
                for begin, _, v in temperature.slots()
                if v is not None and begin >= midnight
            ] + live
        log.info(
            "forecast.load.fitted",
            quality=model.quality.value,
            days=model.days,
            temperature=model.temperature.reason,
            unmeasured=sum(base.unmeasured_slots.values()),
        )
        return model

    # --- temperature ------------------------------------------------------------------

    async def _record_temperature(self, now: datetime) -> None:
        entity = self._inputs.weather_entity
        if entity is None or self._reader is None or self._data is None:
            return
        value = self._reader.outside_temperature(entity)
        if value is None:
            return
        self._observed.append((now, value))
        try:
            await self._data.append_measurements([(WEATHER_TEMPERATURE_C, now, value)])
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("forecast.load.temperature_store_failed")

    async def _update_weather(self, now: datetime) -> None:
        entity = self._inputs.weather_entity
        if entity is None or self._reader is None:
            return
        if self._weather_at is not None and now - self._weather_at < self._weather_interval:
            return
        try:
            points = await self._reader.hourly_temperatures(
                entity, min_interval_s=self._weather_interval.total_seconds()
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.info("forecast.load.weather_failed", error=repr(exc))
            return
        self._weather_at = now
        if points:
            self._weather_points = points

    def daily_temperatures(self, now: datetime) -> dict[date, float]:
        """Mean temperature per forecast date: today from the recorded hours plus the forecast
        hours, later dates from the forecast alone."""
        tz = self._tz()
        today = now.astimezone(tz).date()
        observed = [(t, v) for t, v in self._observed if t.astimezone(tz).date() == today]
        upcoming = [(t, v) for t, v in self._weather_points if t >= floor_to_step(now)]
        return daily_mean_temperatures([*observed, *upcoming], tz)

    # --- refresh ----------------------------------------------------------------------

    def request_refit(self) -> None:
        """Refit at the next refresh (e.g. after the history was rebuilt)."""
        self._fitted_at = None

    def _forecast_days(self, now: datetime, tz: tzinfo) -> list[date]:
        first = now.astimezone(tz).date()
        last = (now + self._horizon).astimezone(tz).date()
        return [first + timedelta(days=i) for i in range((last - first).days + 1)]

    def forecast(self, now: datetime | None = None) -> QuantileSeries | None:
        """The base load forecast from the current slot for ``horizon`` (``None``: no model)."""
        model = self._model
        if model is None:
            return None
        moment = self._clock() if now is None else now
        tz = self._tz()
        grid = planning_grid(moment, self._horizon)
        today = moment.astimezone(tz).date()
        types = self.day_types(self._forecast_days(moment, tz), today)
        temps = self.daily_temperatures(moment) if model.temperature.active else {}
        return forecast_load(model, grid, tz, types, temps)

    async def refresh(self) -> QuantileSeries | None:
        """One refresh (record, refit if due, weather, forecast, store); never raises."""
        async with self._lock:
            now = self._clock()
            try:
                await self._record_temperature(now)
                tz = self._tz()
                if (
                    self._fitted_at is None
                    or self._model is None
                    or self._model.quality is not LoadQuality.OK  # little data: refit cheaply
                    or now - self._fitted_at >= self._refit_interval
                    or self._fitted_at.astimezone(tz).date() != now.astimezone(tz).date()
                ):
                    await self.refit(now)
                else:
                    await self._resolve(self._forecast_days(now, tz), now.astimezone(tz).date())
                await self._update_weather(now)
                forecast = self.forecast(now)
                if forecast is not None:
                    await self._maybe_store(forecast, now)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("forecast.load.refresh_failed")
                return None
            return forecast

    async def _maybe_store(self, forecast: QuantileSeries, now: datetime) -> None:
        if self._data is None:
            return
        if self._last_stored_at is not None and now - self._last_stored_at < self._store_interval:
            return
        try:
            await self._data.store_forecast(LOAD_SOURCE_ID, "load", now, forecast)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("forecast.load.store_failed")
            return
        self._last_stored_at = now

    async def run(self) -> None:
        """Refresh now, then every ``refresh_interval_s``."""
        while True:
            await self.refresh()
            await self._sleep(self._refresh_interval_s)

    # --- nightly ------------------------------------------------------------------------

    async def nightly(self, now: datetime | None = None) -> list[AccuracyRecord]:
        """Accuracy of ``load_profile`` (stored in ``forecast_accuracy``) and a refit."""
        moment = self._clock() if now is None else now
        records: list[AccuracyRecord] = []
        if self._data is not None and self._learned is not None:
            results = await update_accuracy(
                self._data,
                self._learned,
                [LOAD_SOURCE_ID],
                moment,
                kind="load",
                actual_reader=self.base_load_series,
            )
            records = results.get(LOAD_SOURCE_ID, [])
        async with self._lock:
            await self.refit(moment)
        return records

    # --- view ----------------------------------------------------------------------------

    def view(self, hours: int = 48) -> LoadForecastView:
        now = self._clock()
        hours = min(max(hours, 1), MAX_VIEW_HOURS)
        tz = self._tz()
        model = self._model
        forecast = self.forecast(now)
        count = hours * 4
        p10: list[float | None] = []
        p50: list[float | None] = []
        p90: list[float | None] = []
        start = None
        if forecast is not None:
            start = forecast.start
            p10 = list(forecast.p10.values[:count])
            p50 = list(forecast.p50.values[:count])
            p90 = list(forecast.p90.values[:count])
        today = now.astimezone(tz).date()
        temps = self.daily_temperatures(now)
        last = (now + timedelta(hours=hours)).astimezone(tz).date()
        days = [
            LoadDayView(
                date=d,
                day_type=(info := self.day_info(d, today)).day_type,
                source=info.source,
                temperature_c=None if d not in temps else round(temps[d], 2),
            )
            for d in self._forecast_days(now, tz)
            if d <= last
        ]
        term = model.temperature if model is not None else None
        base = self._base
        return LoadForecastView(
            generated_at=now,
            hours=hours,
            source_id=LOAD_SOURCE_ID,
            quality=LoadQuality.NO_HISTORY if model is None else model.quality,
            days_of_history=0 if model is None else model.days,
            fitted_at=self._fitted_at,
            start=start,
            step_s=int(PLANNING_STEP.total_seconds()),
            p10_w=p10,
            p50_w=p50,
            p90_w=p90,
            days=days,
            temperature=LoadTemperatureView(
                active=term is not None and term.active,
                reason=(
                    "not_configured"
                    if self._inputs.weather_entity is None
                    else (
                        term.reason
                        if term is not None and term.reason != "not_configured"
                        else "too_few_days"
                    )
                ),
                days=0 if term is None else term.days,
                heating_w_per_k=0.0 if term is None else round(term.heating_w_per_k, 3),
                cooling_w_per_k=0.0 if term is None else round(term.cooling_w_per_k, 3),
            ),
            controlled_devices=list(self._inputs.device_ids),
            devices_unmeasured={} if base is None else dict(base.unmeasured_slots),
            clamped_slots=0 if base is None else base.clamped_slots,
        )


def _decode_days(payload: Mapping[str, Any]) -> dict[date, DayInfo]:
    out: dict[date, DayInfo] = {}
    raw = payload.get("days")
    if not isinstance(raw, Mapping):
        return out
    for key, value in raw.items():
        try:
            day = date.fromisoformat(str(key))
            kind, source = value
            out[day] = DayInfo(DayType(kind), DayTypeSource(source))
        except (ValueError, TypeError):
            log.warning("forecast.load.day_type_unreadable", day=str(key))
    return out


__all__ = [
    "CHECK_DATE_INTERVAL_S",
    "DAY_TYPES_KEY",
    "HORIZON",
    "MAX_CHECKS_PER_RUN",
    "NS_LOAD_PROFILE",
    "REFRESH_INTERVAL_S",
    "LoadData",
    "LoadForecastService",
    "LoadHaReader",
    "LoadInputs",
]
