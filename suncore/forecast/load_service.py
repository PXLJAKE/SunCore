# SPDX-License-Identifier: Apache-2.0
"""The load forecast service: history, day types, temperature, refresh, store (Phase 4 WP6).

* **Fit** (on start, then at most hourly and after the nightly job): read the last
  ``HISTORY_DAYS`` of ``site.house_w`` and every configured device's ``device.<id>.power_w``
  (15-minute means), form the base load (:func:`suncore.forecast.load.base_load`), resolve the
  day types and fit :class:`~suncore.forecast.load.LoadModel`. The device histories are kept
  for the planner (4-F) - controlled devices are never part of the base load.
* **Day types** with a mapped ``workday`` binary sensor: the date ``today + days_offset`` from
  its state, every other date ``d`` from the read-only action ``workday.check_date`` asked for
  ``d - days_offset`` (Home Assistant evaluates both for the date plus the offset; rate limited
  per date, at most ``MAX_CHECKS_PER_RUN`` calls per refresh, forecast days first, then the
  history newest first). The answers are cached in ``learned_param`` (namespace
  ``load_profile``, key ``day_types``) with a fingerprint of the sensor's entity id,
  ``workdays``, ``excludes`` and ``days_offset``; a changed fingerprint drops the cache (a changed
  country or holiday list is not visible in the attributes - remap the sensor to reset). A date
  without an answer (no sensor, its state unavailable, the action failed or is rate limited)
  uses the weekday rule and is asked again later.
* **Temperature** with a mapped ``weather`` entity: its ``temperature`` attribute is recorded
  with every refresh (key ``weather.temperature_c``, °C) - Home Assistant keeps no long-term
  statistics of a weather entity, so the term learns from SunCore's own recording and becomes
  active after 14 days; the hourly forecast (``weather.get_forecasts``) gives the forecast
  days' mean temperatures (today combines the recorded hours with the forecast ones).
* **Refresh** every 15 minutes: the forecast from the current slot for 48 h; it is stored in
  ``timeseries.db`` (kind ``load``, source ``load_profile``) at most hourly.
* **Nightly**: accuracy (MAE and bias = actual - forecast per horizon bucket, all quarter
  hours) of ``load_profile`` against the measured base load, then a refit.
* **ML load model** (WP12, :mod:`suncore.forecast.load_ml`, ``forecast.learning.load_ml_mode``):
  unless ``off``, the KNN model is fitted with the profiles (from a window of
  ``max(ML_HISTORY_DAYS, min_history_days)`` days; the profiles still see only the last
  ``history_days``), its forecast is stored hourly as source ``load_ml`` and scored nightly
  exactly like ``load_profile``. :meth:`LoadForecastService.forecast` - what the planner and the
  risk model get - is the profile forecast unless the mode is ``auto`` and
  :func:`~suncore.forecast.load_ml.choose_load_model` selects the KNN model;
  :meth:`LoadForecastService.choice` says which one and why.

Storage and Home Assistant are reached only through protocols (the composition root passes
the time-series store, the learned-parameter store and an adapter); time and sleeping are
injected. Failures are logged and never raised out of the loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any, Final, Literal, Protocol

import structlog

from suncore.core.decisions import Reason
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
from suncore.forecast.accuracy import (
    AccuracyData,
    LearnedParams,
    compute_paired_accuracy,
    load_accuracy,
    update_accuracy,
)
from suncore.forecast.days import local_midnight, slice_series
from suncore.forecast.load import (
    FORECAST_TEMPERATURE_HOURS,
    HISTORY_DAYS,
    LOAD_SOURCE_ID,
    BaseLoad,
    DayInfo,
    DayType,
    DayTypeSource,
    LoadModel,
    LoadQuality,
    WorkdayState,
    base_load,
    daily_mean_temperatures,
    day_type_from_workday,
    fit_load_model,
    forecast_load,
    weekday_rule,
)
from suncore.forecast.load_ml import (
    LOAD_ML_SOURCE_ID,
    ML_HISTORY_DAYS,
    ML_MAX_AGE_DAYS,
    KnnLoadModel,
    LoadMlMode,
    LoadModelChoice,
    PairedScore,
    choose_load_model,
    fit_knn_model,
    forecast_knn,
)
from suncore.forecast.views import (
    MAX_VIEW_HOURS,
    LoadDayView,
    LoadForecastView,
    LoadModelView,
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
NS_ML_STATE: Final = "ml_state"
"""``learned_param`` namespace of the KNN model (= ``suncore.storage.learned.NS_ML_STATE``)."""
COMPARISON_KEY: Final = "load_comparison"
COMPARISON_SCHEMA: Final = 1
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

    def workday_state(self, entity_id: str) -> WorkdayState | None:
        """The ``workday`` sensor's state, offset and configuration; ``None`` if unknown."""
        ...

    async def check_workday(self, entity_id: str, day: date, *, min_interval_s: float) -> bool:
        """``workday.check_date`` for ``day`` (Home Assistant answers for ``day + offset``);
        raises on any failure (rate limit included)."""
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
    ml_mode: LoadMlMode = "off"
    """``forecast.learning.load_ml_mode`` (the composition root passes the configured value,
    default ``shadow``; ``off`` here keeps a bare service free of the KNN model)."""
    min_history_days: int = 30
    """``forecast.learning.min_history_days``: history ``auto`` needs before the KNN model is
    selectable."""


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
        self._fingerprint: str | None = None
        """The workday configuration the cached answers belong to (m6)."""
        self._cache_loaded = False
        self._weather_points: list[tuple[datetime, float]] = []
        self._weather_at: datetime | None = None
        self._observed: list[tuple[datetime, float]] = []
        """Today's recorded outside temperatures (from the fit's read plus every refresh)."""
        self._last_stored_at: dict[str, datetime] = {}
        self._ml: KnnLoadModel | None = None
        self._scores: dict[str, tuple[AccuracyRecord, ...]] = {}
        """Latest rolling 14-day accuracy of ``load_profile`` and ``load_ml``."""
        self._scores_loaded = False
        self._comparison: PairedScore | None = None
        """Latest paired score of both models (decides ``auto``)."""
        self._ml_in_use = False
        """The last selection used the KNN model (hysteresis)."""
        self._ml_refit = False
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []

    # --- inputs ---------------------------------------------------------------------

    @property
    def inputs(self) -> LoadInputs:
        return self._inputs

    @property
    def model(self) -> LoadModel | None:
        return self._model

    @property
    def ml_model(self) -> KnnLoadModel | None:
        """The KNN load model (``None``: mode ``off`` or not fitted yet)."""
        return self._ml

    @property
    def ml_enabled(self) -> bool:
        return self._inputs.ml_mode != "off"

    @property
    def source_ids(self) -> tuple[str, ...]:
        """The load forecast sources that are stored and scored."""
        return (LOAD_SOURCE_ID, LOAD_ML_SOURCE_ID) if self.ml_enabled else (LOAD_SOURCE_ID,)

    def _window_days(self) -> int:
        """Days of history read for a fit (the KNN model may look further back)."""
        if not self.ml_enabled:
            return self._history_days
        return max(self._history_days, ML_HISTORY_DAYS, self._inputs.min_history_days)

    @property
    def device_histories(self) -> Mapping[str, TimeSeries]:
        """The controlled devices' measured power (15 minutes, W) of the fit window."""
        return dict(self._devices)

    # --- day types ------------------------------------------------------------------

    def day_info(self, day: date, today: date | None = None) -> DayInfo:
        """The day type of ``day`` and where it came from (module doc)."""
        entity = self._inputs.workday_entity
        if entity is not None and self._reader is not None:
            live = self._reader.workday_state(entity)
            if (
                live is not None
                and today is not None
                and day == today + timedelta(days=live.offset)
            ):
                return DayInfo(
                    day_type_from_workday(day, live.workday), DayTypeSource.WORKDAY_SENSOR
                )
            if live is None or live.fingerprint == self._fingerprint:
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
                    fingerprint = entry.payload.get("fingerprint")
                    self._fingerprint = fingerprint if isinstance(fingerprint, str) else None
        except Exception:
            log.exception("forecast.load.day_types_unreadable")

    async def _save_cache(self, today: date) -> None:
        if self._learned is None:
            return
        oldest = today - timedelta(days=self._window_days() + 7)
        self._cache = {d: v for d, v in self._cache.items() if d >= oldest}
        payload = {
            "fingerprint": self._fingerprint,
            "days": {
                d.isoformat(): [v.day_type.value, v.source.value]
                for d, v in sorted(self._cache.items())
            },
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
        state = self._reader.workday_state(entity)
        if state is None:
            return  # without the state the offset is unknown: no answer can be placed
        changed = False
        if state.fingerprint != self._fingerprint:  # another sensor or configuration
            if self._cache:
                log.info("forecast.load.day_types_reset")
            self._cache.clear()
            self._fingerprint = state.fingerprint
            changed = True
        observed = today + timedelta(days=state.offset)
        info = DayInfo(day_type_from_workday(observed, state.workday), DayTypeSource.WORKDAY_SENSOR)
        if self._cache.get(observed) != info:
            self._cache[observed] = info
            changed = True
        asked = 0
        for day in wanted:
            if day in self._cache or asked >= MAX_CHECKS_PER_RUN:
                continue
            asked += 1
            try:
                # Home Assistant answers for the date plus the sensor's offset: ask d - offset
                workday = await self._reader.check_workday(
                    entity, day - timedelta(days=state.offset), min_interval_s=CHECK_DATE_INTERVAL_S
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
        house, devices = await self._read_inputs(window)
        return base_load(house, devices), devices

    async def _read_inputs(self, window: TimeRange) -> tuple[TimeSeries, dict[str, TimeSeries]]:
        assert self._data is not None
        house = await self._data.read_series(SITE_HOUSE_W, window, "15m", SeriesUnit.W)
        devices = {
            device_id: await self._data.read_series(
                device_power_key(device_id), window, "15m", SeriesUnit.W
            )
            for device_id in self._inputs.device_ids
        }
        return house, devices

    async def refit(self, now: datetime | None = None) -> LoadModel | None:
        """Read the history and fit the model (kept on failure)."""
        if self._data is None:
            return None
        moment = self._clock() if now is None else now
        tz = self._tz()
        await self._load_cache()
        await self._load_scores()
        today = moment.astimezone(tz).date()
        end = floor_to_step(moment, PLANNING_STEP)
        ml_due = self.ml_enabled and (
            self._ml_refit or self._ml is None or self._ml.fitted_for != today
        )
        # the KNN model is fitted once per local day (its samples and level end at midnight);
        # the hourly profile refits read only the profiles' own window
        window_days = self._window_days() if ml_due else self._history_days
        # local midnights (a DST change inside the window must not cut off its first day)
        window = TimeRange(start=_local_start(today, window_days, tz), end=end)
        profile_start = _local_start(today, self._history_days, tz)
        history_days = [today - timedelta(days=i) for i in range(1, window_days + 1)]
        await self._resolve([*self._forecast_days(moment, tz), *history_days], today)
        try:
            house, devices = await self._read_inputs(window)
            full = base_load(house, devices)
            base = full
            if window_days > self._history_days:  # the profiles see only their own window
                devices = {k: slice_series(v, profile_start, v.end) for k, v in devices.items()}
                base = base_load(slice_series(house, profile_start, house.end), devices)
            temperature = None
            if self._inputs.weather_entity is not None:
                temperature = await self._data.read_series(
                    WEATHER_TEMPERATURE_C, window, "15m", SeriesUnit.CELSIUS
                )
            day_types = self.day_types(history_days, today)
            model = fit_load_model(
                base.series,
                now=moment,
                tz=tz,
                day_types=day_types,
                temperature=temperature,
                temperature_configured=self._inputs.weather_entity is not None,
                unmeasured=base.unmeasured,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("forecast.load.fit_failed")
            return self._model
        self._model, self._fitted_at = model, moment
        self._base, self._devices = base, devices
        if ml_due:
            await self._fit_ml(
                full,
                moment=moment,
                tz=tz,
                day_types=day_types,
                temperature=temperature,
                window_days=window_days,
            )
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

    async def _fit_ml(
        self,
        full: BaseLoad,
        *,
        moment: datetime,
        tz: tzinfo,
        day_types: Mapping[date, DayType],
        temperature: TimeSeries | None,
        window_days: int,
    ) -> None:
        """Fit the KNN model in a worker thread (pure NumPy/Python on immutable inputs, so the
        event loop keeps running); a failure keeps the previous model (it turns stale after a
        day) and leaves the profiles unaffected."""
        try:
            ml = await asyncio.to_thread(
                fit_knn_model,
                full.series,
                now=moment,
                tz=tz,
                day_types=day_types,
                temperature=temperature,
                unmeasured=full.unmeasured,
                history_days=window_days,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("forecast.load.ml_fit_failed")
            return
        self._ml, self._ml_refit = ml, False
        log.info("forecast.load.ml_fitted", days=ml.days, samples=ml.samples)

    async def _load_scores(self) -> None:
        """The stored accuracy of both models and their paired comparison (once; the nightly
        job keeps them current). A stored comparison older than ``SCORE_MAX_AGE`` is ignored by
        the model choice."""
        if self._scores_loaded or self._learned is None or not self.ml_enabled:
            self._scores_loaded = True
            return
        self._scores_loaded = True
        try:
            stored = await load_accuracy(self._learned, self.source_ids)
            comparison = None
            for entry in await self._learned.in_namespace(NS_ML_STATE):
                if entry.key == COMPARISON_KEY and entry.schema_version == COMPARISON_SCHEMA:
                    comparison = _decode_comparison(entry.payload)
        except Exception:
            log.exception("forecast.load.scores_unreadable")
            return
        for source_id, item in stored.items():
            self._scores.setdefault(source_id, item.records)
        if self._comparison is None:
            self._comparison = comparison

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
        hours, later dates from the forecast alone; only dates with at least
        ``FORECAST_TEMPERATURE_HOURS`` hours (others get no temperature offset)."""
        tz = self._tz()
        today = now.astimezone(tz).date()
        observed = [(t, v) for t, v in self._observed if t.astimezone(tz).date() == today]
        upcoming = [(t, v) for t, v in self._weather_points if t >= floor_to_step(now)]
        return daily_mean_temperatures(
            [*observed, *upcoming], tz, min_hours=FORECAST_TEMPERATURE_HOURS
        )

    # --- refresh ----------------------------------------------------------------------

    def request_refit(self) -> None:
        """Refit at the next refresh (e.g. after the history was rebuilt), the KNN model too."""
        self._fitted_at = None
        self._ml_refit = True

    def _forecast_days(self, now: datetime, tz: tzinfo) -> list[date]:
        first = now.astimezone(tz).date()
        last = (now + self._horizon).astimezone(tz).date()
        return [first + timedelta(days=i) for i in range((last - first).days + 1)]

    def profile_forecast(self, now: datetime | None = None) -> QuantileSeries | None:
        """The profile forecast from the current slot for ``horizon`` (``None``: no model)."""
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

    def ml_forecast(self, now: datetime | None = None) -> QuantileSeries | None:
        """The KNN forecast on the same grid (``None``: mode ``off`` or too little history)."""
        moment = self._clock() if now is None else now
        if not self._ml_current(moment):
            return None
        ml = self._ml
        assert ml is not None
        tz = self._tz()
        grid = planning_grid(moment, self._horizon)
        today = moment.astimezone(tz).date()
        types = self.day_types(self._forecast_days(moment, tz), today)
        temps = self.daily_temperatures(moment) if self._inputs.weather_entity else {}
        try:
            return forecast_knn(ml, grid, tz, types, temps)
        except Exception:
            log.exception("forecast.load.ml_forecast_failed")
            return None

    def _ml_current(self, now: datetime) -> bool:
        """The KNN model is on, ready and not stale (fitted today or yesterday)."""
        ml = self._ml
        if ml is None or not ml.ready or not self.ml_enabled:
            return False
        today = now.astimezone(self._tz()).date()
        return ml.fitted_for >= today - timedelta(days=ML_MAX_AGE_DAYS)

    def set_scores(self, scores: Mapping[str, Sequence[AccuracyRecord]]) -> None:
        """Replace the per-source accuracy shown next to the comparison (display only)."""
        for source_id, records in scores.items():
            self._scores[source_id] = tuple(records)

    def set_comparison(self, comparison: PairedScore | None) -> None:
        """The paired comparison the model choice uses (the nightly job's result)."""
        self._comparison = comparison

    def choice(
        self, now: datetime | None = None, *, ml_available: bool | None = None
    ) -> LoadModelChoice:
        """Which model :meth:`forecast` returns and why (:func:`choose_load_model`).

        ``ml_available`` defaults to "the KNN model is on, ready and not stale"."""
        moment = self._clock() if now is None else now
        ml = self._ml
        if ml_available is None:
            ml_available = self._ml_current(moment)
        return choose_load_model(
            self._inputs.ml_mode,
            ml_days=0 if ml is None else ml.days,
            ml_available=ml_available,
            min_history_days=self._inputs.min_history_days,
            score=self._comparison,
            now=moment,
            ml_in_use=self._ml_in_use,
        )

    def selected(
        self, now: datetime | None = None
    ) -> tuple[QuantileSeries | None, LoadModelChoice]:
        """The forecast the planner uses and the choice behind it: the KNN forecast only when
        the choice selects it and it exists, else the profile forecast. Remembers the outcome
        for the hysteresis of the next choice."""
        moment = self._clock() if now is None else now
        choice = self.choice(moment)
        forecast = None
        if choice.in_use == "ml":
            forecast = self.ml_forecast(moment)
            if forecast is None:
                choice = self.choice(moment, ml_available=False)
        if forecast is None:
            forecast = self.profile_forecast(moment)
        self._ml_in_use = choice.in_use == "ml"
        return forecast, choice

    def forecast(self, now: datetime | None = None) -> QuantileSeries | None:
        """The base load forecast the planner and the risk model use, from the current slot for
        ``horizon`` (``None``: no model): the profiles unless ``auto`` selected the KNN model."""
        return self.selected(now)[0]

    def planning_reasons(self, choice: LoadModelChoice | None = None) -> tuple[Reason, ...]:
        """Plan-level reasons of the load model: in ``auto`` which model the plan uses and why
        (in ``off``/``shadow`` the plan always uses the profiles, nothing to explain).
        ``choice``: the one :meth:`selected` returned with the forecast (default: now)."""
        choice = self.choice() if choice is None else choice
        return (choice.reason,) if choice.mode == "auto" else ()

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
                forecast, choice = self.selected(now)
                profile = forecast if choice.in_use == "profile" else self.profile_forecast(now)
                if profile is not None and await self._maybe_store(LOAD_SOURCE_ID, profile, now):
                    # the KNN forecast is stored with the same issue time as the profiles, so the
                    # nightly comparison pairs every issue; computed only when due or in use
                    ml = forecast if choice.in_use == "ml" else self.ml_forecast(now)
                    if ml is not None:
                        await self._maybe_store(LOAD_ML_SOURCE_ID, ml, now, force=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("forecast.load.refresh_failed")
                return None
        if forecast is not None:
            self._notify()
        return forecast

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` after every successful refresh (the forecast was recomputed)."""
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                log.exception("forecast.load.listener_failed")

    async def _maybe_store(
        self, source_id: str, forecast: QuantileSeries, now: datetime, *, force: bool = False
    ) -> bool:
        """Store one issue unless one was stored within the store interval; ``True`` if stored."""
        if self._data is None:
            return False
        last = self._last_stored_at.get(source_id)
        if not force and last is not None and now - last < self._store_interval:
            return False
        try:
            await self._data.store_forecast(source_id, "load", now, forecast)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("forecast.load.store_failed", source=source_id)
            return False
        self._last_stored_at[source_id] = now
        return True

    async def run(self) -> None:
        """Refresh now, then every ``refresh_interval_s``."""
        while True:
            await self.refresh()
            await self._sleep(self._refresh_interval_s)

    # --- nightly ------------------------------------------------------------------------

    async def nightly(self, now: datetime | None = None) -> list[AccuracyRecord]:
        """Accuracy of ``load_profile`` (and ``load_ml`` unless the mode is ``off``; stored in
        ``forecast_accuracy``, kept for the model choice) and a refit. Returns the profile's
        records."""
        moment = self._clock() if now is None else now
        records: list[AccuracyRecord] = []
        if self._data is not None and self._learned is not None:
            results = await update_accuracy(
                self._data,
                self._learned,
                list(self.source_ids),
                moment,
                kind="load",
                actual_reader=self.base_load_series,
            )
            self.set_scores(results)
            if LOAD_ML_SOURCE_ID not in results:  # failed or off: no stale row is shown
                self._scores.pop(LOAD_ML_SOURCE_ID, None)
            records = results.get(LOAD_SOURCE_ID, [])
            if self.ml_enabled:
                await self._update_comparison(moment)
        async with self._lock:
            await self.refit(moment)
        return records

    async def _update_comparison(self, now: datetime) -> None:
        """The paired score of both models (same issues and quarter hours); on failure the old
        one is dropped, so ``auto`` never decides on a stale or partial comparison."""
        assert self._data is not None
        assert self._learned is not None
        try:
            paired = await compute_paired_accuracy(
                self._data,
                LOAD_SOURCE_ID,
                LOAD_ML_SOURCE_ID,
                now,
                kind="load",
                actual_reader=self.base_load_series,
            )
            score = PairedScore(
                profile_mae_w=paired.mae_a_w,
                ml_mae_w=paired.mae_b_w,
                n=paired.n,
                computed_at=paired.computed_at,
            )
            await self._learned.put(
                NS_ML_STATE,
                COMPARISON_KEY,
                _encode_comparison(score),
                schema_version=COMPARISON_SCHEMA,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("forecast.load.comparison_failed")
            self._comparison = None
            return
        self._comparison = score
        log.info(
            "forecast.load.comparison",
            profile_mae_w=round(score.profile_mae_w, 1),
            ml_mae_w=round(score.ml_mae_w, 1),
            n=score.n,
        )

    # --- view ----------------------------------------------------------------------------

    def view(self, hours: int = 48) -> LoadForecastView:
        now = self._clock()
        hours = min(max(hours, 1), MAX_VIEW_HOURS)
        tz = self._tz()
        model = self._model
        forecast, choice = self.selected(now)
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
            source_id=LOAD_ML_SOURCE_ID if choice.in_use == "ml" else LOAD_SOURCE_ID,
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
            unmeasured_in_profile=(
                [] if model is None else sorted(model.unmeasured_kept, key=lambda t: t.value)
            ),
            model=self.model_view(choice),
        )

    def model_view(self, choice: LoadModelChoice | None = None) -> LoadModelView:
        """ "ML vs profile": the model in use, why, and the paired comparison it rests on."""
        choice = self.choice() if choice is None else choice
        score = choice.score
        return LoadModelView(
            mode=choice.mode,
            in_use=choice.in_use,
            reason=choice.reason,
            ml_ready=self._ml_current(self._clock()),
            ml_days=choice.ml_days,
            min_history_days=choice.min_history_days,
            profile_mae_w=None if score is None else round(score.profile_mae_w, 1),
            ml_mae_w=None if score is None else round(score.ml_mae_w, 1),
            compared_n=0 if score is None else score.n,
            compared_at=None if score is None else score.computed_at,
        )


def _encode_comparison(score: PairedScore) -> dict[str, Any]:
    return {
        "profile_mae_w": score.profile_mae_w,
        "ml_mae_w": score.ml_mae_w,
        "n": score.n,
        "computed_at": score.computed_at.isoformat(),
    }


def _decode_comparison(payload: Mapping[str, Any]) -> PairedScore | None:
    try:
        computed = datetime.fromisoformat(str(payload["computed_at"]))
        if computed.tzinfo is None:
            return None
        return PairedScore(
            profile_mae_w=float(payload["profile_mae_w"]),
            ml_mae_w=float(payload["ml_mae_w"]),
            n=int(payload["n"]),
            computed_at=computed,
        )
    except (KeyError, ValueError, TypeError):
        log.warning("forecast.load.comparison_unreadable")
        return None


def _local_start(today: date, days: int, tz: tzinfo) -> datetime:
    """Local midnight ``days`` before ``today``, in UTC."""
    return datetime.combine(today - timedelta(days=days), time(), tzinfo=tz).astimezone(UTC)


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
