# SPDX-License-Identifier: Apache-2.0
"""Learning the RC model of a heat pump's store from history (plan WP11, ADR-0017 decision 6).

The model (``core.thermal.ThermalModel``) is ``C dT/dt = COP * P_el + G - UA (T - T_out)``.
Its exact discretisation over one 15-minute step is linear in the parameters::

    T1 - T0 = k (T_out - T0) + b (COP(T_out) * P_el) + d

with ``k = 1 - exp(-UA h / C)``, ``b = k / UA`` (K per thermal W over the step) and ``d = b G``.
:func:`fit_rc` solves this by least squares on the 15-minute means of the probe temperature, the
device's electric power and the outdoor temperature, drops outliers (residuals beyond four robust
standard deviations - hot-water draws, defrost cycles, gaps around a restart) and fits again, then
maps ``k, b, d`` back to ``C, UA, G``. Physically impossible results (``k`` outside ``[0, 1)``,
``b <= 0``, parameters outside the model's bounds) give no model.

Quality (plan §0 decision 10) is the normalised RMSE of a **24 h replay**: the model runs freely
from the first measured temperature of the last day with the measured power and outdoor
temperature, and the RMSE against the probe is divided by the probe's range over that day (at
least 1 K). The planner uses the model only when ``core.thermal.model_usable`` (>= 14 days of
history, NRMSE <= 0.2); otherwise the device stays energy-only. Fits are stored in
``learned_param`` (namespace ``thermal_model``, key = device id) and survive a restart.

Pure computations plus a small service (:class:`ThermalLearner`) that reads ``timeseries.db``
through the :class:`~suncore.forecast.accuracy.AccuracyData` protocol; no Home Assistant access.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

import numpy as np
import structlog
from pydantic import ValidationError

from suncore.core.control import TEMPERATURE_ROLE
from suncore.core.devices import DeviceConfig
from suncore.core.measurements import (
    WEATHER_TEMPERATURE_C,
    device_power_key,
    device_temperature_key,
)
from suncore.core.series import SeriesUnit, TimeRange, TimeSeries, floor_to_step
from suncore.core.thermal import (
    GENERIC_AIR_WATER_COP,
    MODEL_MIN_HISTORY_DAYS,
    CopCurve,
    LearnedThermal,
    ThermalModel,
)
from suncore.forecast.accuracy import AccuracyData, LearnedParams

log = structlog.get_logger(__name__)

NS_THERMAL_MODEL: Final = "thermal_model"
"""``learned_param`` namespace of the fitted RC models (key = device id)."""
SCHEMA_VERSION: Final = 1
STEP: Final = timedelta(minutes=15)
SLOTS_PER_DAY: Final = 96
HISTORY_WINDOW_DAYS: Final = 28
"""How much history a refit reads."""
MIN_ROWS: Final = 2 * SLOTS_PER_DAY
"""At least two days of complete transitions before anything is fitted."""
OUTLIER_SIGMAS: Final = 4.0
MIN_OUTLIER_K: Final = 0.05
"""Residuals below this (K) are never outliers (a very quiet store)."""
REPLAY_MIN_COVERAGE: Final = 0.75
"""The 24 h replay needs at least this share of measured temperatures."""
MIN_RANGE_K: Final = 1.0
"""The NRMSE divides by the probe's range over the replay day, at least this."""
REFIT_INTERVAL: Final = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class Rows:
    """Aligned 15-minute history: temperature, electric power (W) and outdoor temperature."""

    temp_c: tuple[float | None, ...]
    power_w: tuple[float | None, ...]
    outdoor_c: tuple[float | None, ...]

    def __post_init__(self) -> None:
        if not len(self.temp_c) == len(self.power_w) == len(self.outdoor_c):
            msg = "thermal rows need one value per slot in every series"
            raise ValueError(msg)

    def __len__(self) -> int:
        return len(self.temp_c)

    def transitions(self) -> list[int]:
        """Indexes ``i`` with a complete transition ``i -> i + 1``."""
        return [
            i
            for i in range(len(self) - 1)
            if self.temp_c[i] is not None
            and self.temp_c[i + 1] is not None
            and self.power_w[i] is not None
            and self.power_w[i + 1] is not None
            and self.outdoor_c[i] is not None
            and self.outdoor_c[i + 1] is not None
        ]


def _solve(x: np.ndarray, y: np.ndarray) -> np.ndarray | None:
    try:
        coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    except np.linalg.LinAlgError:
        return None
    return coef if bool(np.all(np.isfinite(coef))) else None


def _design(rows: Rows, index: Sequence[int], cop: CopCurve) -> tuple[np.ndarray, np.ndarray]:
    x = np.empty((len(index), 3))
    y = np.empty(len(index))
    for n, i in enumerate(index):
        t0, t1 = rows.temp_c[i], rows.temp_c[i + 1]
        p0, p1 = rows.power_w[i], rows.power_w[i + 1]
        o0, o1 = rows.outdoor_c[i], rows.outdoor_c[i + 1]
        assert t0 is not None
        assert t1 is not None
        assert p0 is not None
        assert p1 is not None
        assert o0 is not None
        assert o1 is not None
        # Between two 15-minute *means* the inputs act half in each slot.
        heat = 0.5 * (max(0.0, p0) * cop.cop_at(o0) + max(0.0, p1) * cop.cop_at(o1))
        x[n] = (0.5 * (o0 + o1) - t0, heat, 1.0)
        y[n] = t1 - t0
    return x, y


def _to_model(coef: np.ndarray, step_s: float) -> ThermalModel | None:
    k, b, d = (float(v) for v in coef)
    if not (0.0 <= k < 1.0) or b <= 0.0:
        return None
    hours = step_s / 3600.0
    if k < 1e-9:  # no measurable losses: T1 - T0 = H h / C
        ua, capacity = 0.0, hours / b
    else:
        ua = k / b
        capacity = ua * hours / -math.log1p(-k)
    try:
        return ThermalModel(capacity_wh_per_k=capacity, loss_w_per_k=ua, internal_gain_w=d / b)
    except ValidationError:
        return None


def fit_rc(
    rows: Rows, *, cop: CopCurve = GENERIC_AIR_WATER_COP, step_s: float = 900.0
) -> ThermalModel | None:
    """Least-squares RC parameters from ``rows`` (module doc); ``None`` = not identifiable."""
    index = rows.transitions()
    if len(index) < MIN_ROWS:
        return None
    x, y = _design(rows, index, cop)
    coef = _solve(x, y)
    if coef is None:
        return None
    residual = y - x @ coef
    median = float(np.median(residual))
    mad = float(np.median(np.abs(residual - median)))
    limit = max(MIN_OUTLIER_K, OUTLIER_SIGMAS * 1.4826 * mad)
    keep = np.abs(residual - median) <= limit
    if int(keep.sum()) >= MIN_ROWS and not bool(keep.all()):
        refit = _solve(x[keep], y[keep])
        if refit is not None:
            coef = refit
    return _to_model(coef, step_s)


def replay_nrmse(
    model: ThermalModel,
    rows: Rows,
    *,
    cop: CopCurve = GENERIC_AIR_WATER_COP,
    step_s: float = 900.0,
    slots: int = SLOTS_PER_DAY,
) -> float | None:
    """NRMSE of a free-running replay of the last ``slots`` of ``rows`` (module doc).

    Missing power or outdoor values hold the last known value; ``None`` = too few measured
    temperatures in the window (below :data:`REPLAY_MIN_COVERAGE`) or no starting value.
    """
    if len(rows) < slots:
        return None
    first = len(rows) - slots
    start = next((i for i in range(first, len(rows)) if rows.temp_c[i] is not None), None)
    measured = [t for t in rows.temp_c[first:] if t is not None]
    if start is None or len(measured) < REPLAY_MIN_COVERAGE * slots:
        return None
    temp = rows.temp_c[start]
    assert temp is not None
    power: float = next((p for p in rows.power_w[start:] if p is not None), 0.0)
    first_outdoor = next((o for o in rows.outdoor_c[start:] if o is not None), None)
    if first_outdoor is None:
        return None
    outdoor: float = first_outdoor
    errors: list[float] = []
    for i in range(start, len(rows) - 1):
        p_i, o_i = rows.power_w[i], rows.outdoor_c[i]
        power = p_i if p_i is not None else power
        outdoor = o_i if o_i is not None else outdoor
        temp = model.step(temp, outdoor, max(0.0, power) * cop.cop_at(outdoor), step_s)
        actual = rows.temp_c[i + 1]
        if actual is not None:
            errors.append(temp - actual)
    if not errors:
        return None
    rmse = math.sqrt(sum(e * e for e in errors) / len(errors))
    spread = max(MIN_RANGE_K, max(measured) - min(measured))
    return rmse / spread


def history_days(rows: Rows) -> int:
    """Whole days of complete transitions in ``rows``."""
    return len(rows.transitions()) // SLOTS_PER_DAY


def learn(
    rows: Rows,
    *,
    now: datetime,
    cop: CopCurve = GENERIC_AIR_WATER_COP,
    step_s: float = 900.0,
) -> LearnedThermal | None:
    """Fit and judge one device; ``None`` when nothing can be fitted or replayed."""
    model = fit_rc(rows, cop=cop, step_s=step_s)
    if model is None:
        return None
    nrmse = replay_nrmse(model, rows, cop=cop, step_s=step_s)
    if nrmse is None or not math.isfinite(nrmse):
        return None
    return LearnedThermal(
        model=model, nrmse=min(100.0, nrmse), history_days=history_days(rows), fitted_at=now
    )


def rows_from_series(temp: TimeSeries, power: TimeSeries, outdoor: TimeSeries) -> Rows:
    """Align three 15-minute series of the same window (as ``read_series`` returns them)."""
    n = min(len(temp), len(power), len(outdoor))
    return Rows(temp.values[:n], power.values[:n], outdoor.values[:n])


# --- persistence ----------------------------------------------------------------------------


def to_payload(learned: LearnedThermal) -> dict[str, Any]:
    return learned.model_dump(mode="json")


def from_payload(payload: Mapping[str, Any]) -> LearnedThermal | None:
    try:
        return LearnedThermal.model_validate(payload)
    except ValidationError:
        return None


async def load_models(learned: LearnedParams) -> dict[str, LearnedThermal]:
    """Every stored fit by device id (unreadable or newer payloads are skipped)."""
    out: dict[str, LearnedThermal] = {}
    for entry in await learned.in_namespace(NS_THERMAL_MODEL):
        if entry.schema_version > SCHEMA_VERSION:
            continue
        fit = from_payload(entry.payload)
        if fit is not None:
            out[entry.key] = fit
    return out


def with_learned(
    devices: Sequence[DeviceConfig], models: Mapping[str, LearnedThermal]
) -> list[DeviceConfig]:
    """``devices`` with each thermal device's ``thermal.learned`` set from ``models``.

    The configuration is never written: the planner sees copies (the learned model is runtime
    state, plan WP11 "persisted in ``learned_param``").
    """
    out: list[DeviceConfig] = []
    for device in devices:
        fit = models.get(device.id)
        if device.thermal is None or fit is None:
            out.append(device)
            continue
        thermal = device.thermal.model_copy(update={"learned": fit})
        out.append(device.model_copy(update={"thermal": thermal}))
    return out


def learnable(device: DeviceConfig) -> bool:
    """A thermal device with a probe whose power is recorded."""
    return device.thermal is not None and TEMPERATURE_ROLE in device.entities


class ThermalLearner:
    """Refits the RC model of every learnable device once a day and keeps the fits.

    ``data`` is ``timeseries.db`` (probe ``device.<id>.temperature_c``, power
    ``device.<id>.power_w``, outdoor ``weather.temperature_c``), ``learned`` the
    ``learned_param`` store. Failures are logged and leave the previous fit in place.
    """

    def __init__(
        self,
        data: AccuracyData,
        learned: LearnedParams | None,
        *,
        clock: Callable[[], datetime],
        history_window_days: int = HISTORY_WINDOW_DAYS,
    ) -> None:
        self._data = data
        self._learned = learned
        self._clock = clock
        self._window = timedelta(days=history_window_days)
        self._models: dict[str, LearnedThermal] = {}
        self._loaded = False
        self._fitted_at: datetime | None = None

    @property
    def models(self) -> Mapping[str, LearnedThermal]:
        return dict(self._models)

    async def load(self) -> None:
        if self._loaded or self._learned is None:
            self._loaded = True
            return
        try:
            self._models.update(await load_models(self._learned))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("forecast.thermal.load_failed")
        self._loaded = True

    def due(self, now: datetime) -> bool:
        return self._fitted_at is None or now - self._fitted_at >= REFIT_INTERVAL

    async def refit(self, devices: Sequence[DeviceConfig]) -> dict[str, LearnedThermal]:
        """Fit every learnable device; returns the new fits (also kept and stored)."""
        await self.load()
        now = self._clock()
        end = floor_to_step(now, STEP)
        window = TimeRange(start=end - self._window, end=end)
        fitted: dict[str, LearnedThermal] = {}
        try:
            outdoor = await self._data.read_series(
                WEATHER_TEMPERATURE_C, window, "15m", SeriesUnit.CELSIUS
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("forecast.thermal.read_failed", key=WEATHER_TEMPERATURE_C)
            return fitted
        for device in devices:
            if not learnable(device):
                continue
            assert device.thermal is not None
            try:
                temp = await self._data.read_series(
                    device_temperature_key(device.id), window, "15m", SeriesUnit.CELSIUS
                )
                power = await self._data.read_series(
                    device_power_key(device.id), window, "15m", SeriesUnit.W
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("forecast.thermal.read_failed", device_id=device.id)
                continue
            cop = device.thermal.cop or GENERIC_AIR_WATER_COP
            fit = learn(rows_from_series(temp, power, outdoor), now=now, cop=cop)
            if fit is None:
                log.info("forecast.thermal.no_fit", device_id=device.id)
                continue
            fitted[device.id] = fit
            self._models[device.id] = fit
            log.info(
                "forecast.thermal.fitted",
                device_id=device.id,
                nrmse=round(fit.nrmse, 3),
                history_days=fit.history_days,
                usable=fit.history_days >= MODEL_MIN_HISTORY_DAYS,
            )
            if self._learned is not None:
                try:
                    await self._learned.put(
                        NS_THERMAL_MODEL,
                        device.id,
                        to_payload(fit),
                        schema_version=SCHEMA_VERSION,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("forecast.thermal.store_failed", device_id=device.id)
        self._fitted_at = now
        return fitted


__all__ = [
    "NS_THERMAL_MODEL",
    "Rows",
    "ThermalLearner",
    "fit_rc",
    "from_payload",
    "history_days",
    "learn",
    "learnable",
    "load_models",
    "replay_nrmse",
    "rows_from_series",
    "to_payload",
    "with_learned",
]
