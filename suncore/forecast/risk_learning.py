# SPDX-License-Identifier: Apache-2.0
"""Nightly learning of the net energy error and the risk read model (Phase 4 WP7).

**Samples.** For every complete anchor day ``D`` (at most ``BACKFILL_DAYS`` back - the stored
forecasts are kept 45 days - and only once: ``learned_until``), the forecasts as they stood at
two fixed local times are compared with what was measured:

* at ``MORNING_ANCHOR`` (06:00) of ``D``: the window ``rest_of_today`` (06:00 -> sunset of ``D``),
  the situation the morning buffer is computed in;
* at ``EVENING_ANCHOR`` (18:00) of ``D``: ``night`` (sunset ``D`` -> sunrise ``D+1``),
  ``next_day`` (daytime of ``D+1``) and ``bridge`` (sunset ``D`` -> sunset ``D+1``), the
  situation of the evening target.

The forecast of a slot is the newest issue at or before the anchor (``latest_forecast_before``)
of the PV forecast the planner uses (source ``blended``) and of the load forecast
(``load_profile``); windows, sunrise/sunset and the weather class are derived from that issue
exactly as live (:func:`suncore.forecast.risk.day_windows`). The measurement is the PV power
(``site.pv_w``) and the base load (house minus devices, through the load forecast's reader); a
window counts only if both cover at least ``MIN_COVERAGE`` of its quarter hours. ``D`` is
processed once ``D+1`` ended (the night ``bridge`` window reaches the sunset of ``D+1``), i.e.
the nightly job at 03:30 learns the day before yesterday.

The samples (``error = actual net - forecast net`` and the window's scale) are kept for
``KEEP_DAYS`` days in ``learned_param`` (namespace ``net_error``, key ``samples``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any, Final, Literal, Protocol

import structlog

from suncore.core.battery import BatterySettings
from suncore.core.measurements import SITE_PV_W
from suncore.core.series import QuantileSeries, SeriesUnit, TimeRange, TimeSeries
from suncore.forecast.accuracy import LearnedParams
from suncore.forecast.load import LOAD_SOURCE_ID
from suncore.forecast.pv.clearsky import PlantGeometry
from suncore.forecast.risk import (
    DistributionSource,
    EnergyQuantiles,
    ErrorModel,
    ErrorSample,
    NetWindow,
    RiskWindow,
    coming_daytime,
    coverage,
    day_windows,
    evening_target_pct,
    measured_energy,
    merge_samples,
    morning_buffer_pct,
    net_energy_quantile,
    samples_view,
)
from suncore.forecast.views import (
    EnergyView,
    RiskTargetView,
    RiskView,
    RiskWindowView,
)

log = structlog.get_logger(__name__)

NS_NET_ERROR: Final = "net_error"
"""``learned_param`` namespace (equal to ``suncore.storage.learned.NS_NET_ERROR``)."""
SAMPLES_KEY: Final = "samples"
SCHEMA_VERSION: Final = 1
KEEP_DAYS: Final = 365
BACKFILL_DAYS: Final = 45
MORNING_ANCHOR: Final = time(6, 0)
EVENING_ANCHOR: Final = time(18, 0)
MIN_COVERAGE: Final = 0.9
PV_SOURCE_ID: Final = "blended"
"""The PV forecast the planner uses (``suncore.forecast.pv.learning.BLENDED_SOURCE_ID``)."""

BaseLoadReader = Callable[[TimeRange], Awaitable[TimeSeries]]


class RiskData(Protocol):
    """The part of ``TimeseriesStore`` the learning reads."""

    async def read_series(
        self,
        key: str,
        window: TimeRange,
        resolution: Literal["1m", "15m"] = "15m",
        unit: SeriesUnit | None = None,
        *,
        include_recent: bool = True,
    ) -> TimeSeries: ...

    async def latest_forecast_before(
        self,
        source_id: str,
        kind: Literal["pv", "load", "price"],
        before: datetime,
        window: TimeRange | None = None,
    ) -> QuantileSeries | None: ...


@dataclass(frozen=True, slots=True)
class StoredErrors:
    model: ErrorModel
    learned_until: date | None


async def load_error_model(learned: LearnedParams) -> StoredErrors:
    """The stored samples; an unreadable entry counts as none (learning starts again)."""
    for entry in await learned.in_namespace(NS_NET_ERROR):
        if entry.key != SAMPLES_KEY:
            continue
        if entry.schema_version != SCHEMA_VERSION:
            log.warning("forecast.risk.unknown_schema", version=entry.schema_version)
            break
        try:
            return _decode(entry.payload)
        except (KeyError, ValueError, TypeError):
            log.warning("forecast.risk.samples_unreadable")
            break
    return StoredErrors(ErrorModel(), None)


def _decode(payload: Mapping[str, Any]) -> StoredErrors:
    raw = payload["samples"]
    if not isinstance(raw, list):
        msg = "samples must be a list"
        raise TypeError(msg)
    until = payload.get("learned_until")
    return StoredErrors(
        ErrorModel(tuple(ErrorSample.from_json(s) for s in raw)),
        None if until is None else date.fromisoformat(str(until)),
    )


async def save_error_model(learned: LearnedParams, stored: StoredErrors) -> None:
    payload = {
        "learned_until": None if stored.learned_until is None else stored.learned_until.isoformat(),
        "samples": [s.to_json() for s in stored.model.samples],
    }
    await learned.put(NS_NET_ERROR, SAMPLES_KEY, payload, schema_version=SCHEMA_VERSION)


def _at(day: date, moment: time, tz: tzinfo) -> datetime:
    return datetime.combine(day, moment, tzinfo=tz).astimezone(UTC)


def _sample(day: date, window: NetWindow, pv: TimeSeries, base: TimeSeries) -> ErrorSample | None:
    if window.end <= window.start:
        return None
    if (
        coverage(pv, window.start, window.end) < MIN_COVERAGE
        or coverage(base, window.start, window.end) < MIN_COVERAGE
    ):
        return None
    actual = measured_energy(pv, window.start, window.end) - measured_energy(
        base, window.start, window.end
    )
    return ErrorSample(
        day=day,
        window=window.kind,
        weather=window.weather,
        error_wh=actual - window.net_p50,
        scale_wh=window.scale_wh,
    )


async def _issue(
    data: RiskData, anchor: datetime, span: TimeRange
) -> tuple[QuantileSeries | None, QuantileSeries] | None:
    """``(pv, load)`` as issued before ``anchor``; ``None`` without a load forecast (there is
    no net forecast to judge then). A missing PV forecast counts as 0 W, as live."""
    pv = await data.latest_forecast_before(PV_SOURCE_ID, "pv", anchor, span)
    load = await data.latest_forecast_before(LOAD_SOURCE_ID, "load", anchor, span)
    if load is None:
        return None
    return pv, load


async def learn_day(
    data: RiskData,
    day: date,
    *,
    tz: tzinfo,
    plant: PlantGeometry | None,
    base_reader: BaseLoadReader,
) -> list[ErrorSample]:
    """The samples of one anchor day (module doc); empty when data is missing."""
    start = _at(day, time(0), tz)
    end = _at(day + timedelta(days=2), time(0), tz)
    span = TimeRange(start=start - timedelta(hours=1), end=end)
    measured = TimeRange(start=start, end=end)
    pv_actual = await data.read_series(SITE_PV_W, measured, "15m", SeriesUnit.W)
    base = await base_reader(measured)
    out: list[ErrorSample] = []
    for anchor, kinds in (
        (MORNING_ANCHOR, (RiskWindow.REST_OF_TODAY,)),
        (EVENING_ANCHOR, (RiskWindow.NIGHT, RiskWindow.NEXT_DAY, RiskWindow.BRIDGE)),
    ):
        moment = _at(day, anchor, tz)
        issue = await _issue(data, moment, span)
        if issue is None:
            continue
        windows = day_windows(moment, issue[0], issue[1], tz, plant=plant)
        for kind in kinds:
            sample = _sample(day, windows[kind], pv_actual, base)
            if sample is not None:
                out.append(sample)
    return out


async def learn_net_errors(
    data: RiskData,
    learned: LearnedParams,
    now: datetime,
    *,
    tz: tzinfo,
    plant: PlantGeometry | None,
    base_reader: BaseLoadReader,
    backfill_days: int = BACKFILL_DAYS,
) -> ErrorModel:
    """The nightly job: learn every new complete anchor day and store the samples; a failure
    is logged and keeps what was stored."""
    stored = await load_error_model(learned)
    today = now.astimezone(tz).date()
    last = today - timedelta(days=2)
    first = today - timedelta(days=backfill_days)
    if stored.learned_until is not None:
        first = max(first, stored.learned_until + timedelta(days=1))
    new: list[ErrorSample] = []
    day = first
    try:
        while day <= last:
            new.extend(await learn_day(data, day, tz=tz, plant=plant, base_reader=base_reader))
            day += timedelta(days=1)
    except Exception:
        log.exception("forecast.risk.learning_failed", day=day.isoformat())
        last = day - timedelta(days=1)  # keep what was learnt so far
    if last < first:
        return stored.model
    model = ErrorModel(merge_samples(stored.model.samples, new, keep_days=KEEP_DAYS, today=today))
    await save_error_model(learned, StoredErrors(model, last))
    log.info("forecast.risk.learned", days=(last - first).days + 1, samples=len(new))
    return model


# --- read model ---------------------------------------------------------------------------------


def _energy(q: EnergyQuantiles) -> EnergyView:
    return EnergyView(p10_wh=round(q.p10, 1), p50_wh=round(q.p50, 1), p90_wh=round(q.p90, 1))


def risk_view(
    now: datetime,
    *,
    pv: QuantileSeries | None,
    load: QuantileSeries | None,
    settings: BatterySettings,
    user_target_pct: float | None,
    model: ErrorModel,
    min_history_days: int,
    tz: tzinfo,
    plant: PlantGeometry | None,
) -> RiskView:
    """The API read model: windows, buffer and target as the planner would get them now."""
    base: dict[str, Any] = {
        "generated_at": now,
        "risk": settings.risk,
        "quantile": settings.risk.quantile,
        "strategy": settings.strategy,
        "bridge_next_day": settings.bridge_next_day,
        "pv_forecast": pv is not None,
        "history_days": model.days,
        "min_history_days": min_history_days,
        "samples": samples_view(model.samples),
    }
    if load is None:
        return RiskView(
            status="no_forecast",
            distribution_source="source_quantiles",
            evening_at=None,
            windows=[],
            morning_buffer=None,
            evening_target=None,
            **base,
        )
    windows = day_windows(now, pv, load, tz, plant=plant)
    views: list[RiskWindowView] = []
    for window in windows.values():
        q = net_energy_quantile(window, settings.risk, model, min_history_days=min_history_days)
        views.append(
            RiskWindowView(
                window=window.kind,
                start=window.start,
                end=window.end,
                weather=window.weather,
                pv=_energy(window.pv),
                load=_energy(window.load),
                net_p50_wh=round(window.net_p50, 1),
                net_quantile_wh=round(q.value_wh, 1),
                source=q.source,
                samples=q.samples,
                pooled=q.pooled,
            )
        )
    night = next(v for v in views if v.window is RiskWindow.NIGHT)
    source: DistributionSource = night.source
    buffer = target = None
    battery = settings.model
    if battery is not None:
        evening = evening_target_pct(
            battery,
            settings,
            windows,
            user_target_pct=user_target_pct,
            model=model,
            min_history_days=min_history_days,
        )
        morning = morning_buffer_pct(
            battery,
            settings,
            coming_daytime(windows),
            evening.soc_pct,
            model=model,
            min_history_days=min_history_days,
        )
        source = evening.source
        target = RiskTargetView(
            soc_pct=round(evening.soc_pct, 1), reason=evening.reason, source=evening.source
        )
        buffer = RiskTargetView(
            soc_pct=round(morning.soc_pct, 1), reason=morning.reason, source=morning.source
        )
    return RiskView(
        status="ok" if battery is not None else "no_battery_model",
        distribution_source=source,
        evening_at=windows[RiskWindow.NIGHT].start,
        windows=views,
        morning_buffer=buffer,
        evening_target=target,
        **base,
    )


__all__ = [
    "BACKFILL_DAYS",
    "EVENING_ANCHOR",
    "KEEP_DAYS",
    "MORNING_ANCHOR",
    "NS_NET_ERROR",
    "BaseLoadReader",
    "RiskData",
    "StoredErrors",
    "learn_day",
    "learn_net_errors",
    "load_error_model",
    "risk_view",
    "save_error_model",
]
