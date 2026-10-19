# SPDX-License-Identifier: Apache-2.0
"""Forecast shapes of the demo: what Solcast, Open-Meteo Solar Forecast and Forecast.Solar
would publish for the virtual house (Phase 4 WP5).

All sources derive from the simulator's PV model (clear-sky power times the cloud factor) and add
**deterministic** noise: the same ``seed`` and the same instants always give the same numbers, so
tests and the demo are reproducible. Each source has its own error character so the accuracy
statistics have something to show:

* ``solcast``: unbiased, 6 % noise, P10 = 0.6 x P50 and P90 = min(clear sky, 1.15 x P50);
* ``open_meteo``: 6 % too low, 8 % noise, no quantiles;
* ``forecast_solar``: 4 % too high, 7 % noise, hourly Wh, zero hours dropped except midnight
  (the behaviour of the real integration's ``energy`` platform, ``forecast_solar/energy.py``);
* ``template``: the generic template's own contract, points ``{start, power_w}`` (30 min).

The noise grows with the lead time (half at lead 0, full at 48 h), and a forecast issued in a
later hour differs slightly from an earlier one, like a real refresh.

Shapes (all from ``docs/research/integrations.md``): Solcast ``detailedForecast`` entries
``{period_start, pv_estimate, pv_estimate10, pv_estimate90}`` in kW (average power of 30 min);
Open-Meteo ``wh_period_15m`` ``{interval start ISO: Wh per 15 min}``; Forecast.Solar
``wh_hours`` ``{ISO timestamp: Wh}`` of hourly periods.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from suncore.sim.house import clear_sky_w
from suncore.sim.scenario import Scenario

FORECAST_SEED: Final = 20_261_016
"""Seed of the demo forecast noise."""

TOMORROW_CLOUD_FACTOR: Final = 0.8
"""Assumed cloud factor of the demo forecast for the following days."""

SOLCAST_SLOT_MIN: Final = 30
OPEN_METEO_SLOT_MIN: Final = 15
TEMPLATE_SLOT_MIN: Final = 30

_BIAS: Final = {"solcast": 1.0, "open_meteo": 0.94, "forecast_solar": 1.04, "template": 1.0}
_SIGMA: Final = {"solcast": 0.06, "open_meteo": 0.08, "forecast_solar": 0.07, "template": 0.05}


@dataclass(frozen=True, slots=True)
class SimForecastModel:
    """The forecasts of the demo, as the integrations would publish them at ``now``."""

    scenario: Scenario
    cloud_at: Callable[[datetime], float]
    seed: int = FORECAST_SEED

    def _factor(self, source: str, slot: datetime, now: datetime) -> float:
        """Bias and noise of one slot as issued at ``now`` (deterministic)."""
        lead_h = min(max((slot - now).total_seconds() / 3600, 0.0), 48.0)
        sigma = _SIGMA[source] * (0.5 + 0.5 * lead_h / 48.0)
        issue = now.replace(minute=0, second=0, microsecond=0).isoformat()
        key = f"{self.seed}|{source}|{slot.isoformat()}|{issue}"
        noise = random.Random(key).gauss(0.0, sigma)
        return _BIAS[source] * min(max(1.0 + noise, 0.6), 1.4)

    def _powers_w(
        self, source: str, start: datetime, minutes: int, now: datetime
    ) -> tuple[float, float]:
        """``(clear_sky_w, p50_w)`` of the slot ``[start, start + minutes)`` as seen at ``now``."""
        mid = start + timedelta(minutes=minutes / 2)
        clear = clear_sky_w(mid, self.scenario.house.pv)
        beyond_today = start.date() > now.date()
        cloud = TOMORROW_CLOUD_FACTOR if beyond_today else self.cloud_at(mid)
        p50 = min(clear, clear * cloud * self._factor(source, start, now))
        return clear, p50

    # --- Solcast -------------------------------------------------------------------

    def solcast_day(self, midnight: datetime, now: datetime) -> list[dict[str, Any]]:
        """``detailedForecast`` of the local day starting at ``midnight`` (kW, 30 min)."""
        slots: list[dict[str, Any]] = []
        for i in range(24 * 60 // SOLCAST_SLOT_MIN):
            start = midnight + timedelta(minutes=i * SOLCAST_SLOT_MIN)
            clear, p50 = self._powers_w("solcast", start, SOLCAST_SLOT_MIN, now)
            p50_kw = p50 / 1000
            slots.append(
                {
                    "period_start": start.isoformat(),
                    "pv_estimate": round(p50_kw, 4),
                    "pv_estimate10": round(p50_kw * 0.6, 4),
                    "pv_estimate90": round(min(clear / 1000, p50_kw * 1.15), 4),
                }
            )
        return slots

    def solcast_wh_hours(self, start: datetime, hours: int, now: datetime) -> dict[str, float]:
        """The Solcast ``energy`` platform: half-hour keys, ``kW * 500`` Wh (P50, integers)."""
        out: dict[str, float] = {}
        for i in range(hours * 60 // SOLCAST_SLOT_MIN):
            slot = start + timedelta(minutes=i * SOLCAST_SLOT_MIN)
            _, p50 = self._powers_w("solcast", slot, SOLCAST_SLOT_MIN, now)
            out[slot.isoformat()] = float(round(p50 / 1000 * 500))
        return out

    # --- Open-Meteo ----------------------------------------------------------------

    def open_meteo_day(self, midnight: datetime, now: datetime) -> dict[str, float]:
        """``wh_period_15m`` of the local day: Wh per 15 minutes, keyed by the interval start."""
        out: dict[str, float] = {}
        for i in range(24 * 60 // OPEN_METEO_SLOT_MIN):
            start = midnight + timedelta(minutes=i * OPEN_METEO_SLOT_MIN)
            _, p50 = self._powers_w("open_meteo", start, OPEN_METEO_SLOT_MIN, now)
            out[start.isoformat()] = round(p50 * OPEN_METEO_SLOT_MIN / 60, 3)
        return out

    # --- Forecast.Solar --------------------------------------------------------------

    def forecast_solar_wh_hours(self, now: datetime, hours: int = 48) -> dict[str, float]:
        """Hourly Wh from the current hour on; zero hours are dropped except local midnight."""
        first = now.replace(minute=0, second=0, microsecond=0)
        out: dict[str, float] = {}
        for i in range(hours):
            start = first + timedelta(hours=i)
            _, p50 = self._powers_w("forecast_solar", start, 60, now)
            wh = float(round(p50))
            if wh != 0 or (start.hour, start.minute, start.second) == (0, 0, 0):
                out[start.isoformat()] = wh
        return out

    # --- generic template sensor ------------------------------------------------------

    def template_points(self, midnight: datetime, days: int, now: datetime) -> list[dict[str, Any]]:
        """Points ``{start, power_w}`` (30 min) of ``days`` local days."""
        points: list[dict[str, Any]] = []
        for i in range(days * 24 * 60 // TEMPLATE_SLOT_MIN):
            start = midnight + timedelta(minutes=i * TEMPLATE_SLOT_MIN)
            _, p50 = self._powers_w("template", start, TEMPLATE_SLOT_MIN, now)
            points.append({"start": start.isoformat(), "power_w": round(p50, 1)})
        return points


__all__ = [
    "FORECAST_SEED",
    "OPEN_METEO_SLOT_MIN",
    "SOLCAST_SLOT_MIN",
    "TEMPLATE_SLOT_MIN",
    "TOMORROW_CLOUD_FACTOR",
    "SimForecastModel",
]
