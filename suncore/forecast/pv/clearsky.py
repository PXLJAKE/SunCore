# SPDX-License-Identifier: Apache-2.0
"""Clear-sky reference of the plant and the weather class of a forecast slot (Phase 4 WP5).

The bias calibration needs a *weather class* per slot - how cloudy the forecast itself thinks the
slot is. It is the **clear-sky index** ``kt = forecast / clear-sky estimate`` of the forecast's
own P50, bucketed into :class:`WeatherClass`. Two ways to get the clear-sky estimate:

``clear_sky`` (location and every array's ``kwp`` known)
    A simple sun-position model (declination, equation of time, hour angle; no refraction) gives
    the sun's elevation and azimuth; the Haurwitz clear-sky model gives the global horizontal
    irradiance ``GHI = 1098 cos(Z) exp(-0.057 / cos(Z))`` W/m2, split into 10 % diffuse and the
    rest direct, transposed onto every array (azimuth/tilt; missing values default to an
    equator-facing 30 deg plane) with an isotropic sky and 20 % ground albedo. The plant's
    clear-sky power is ``sum(kwp * POA)`` (W, 1 kWp yields 1 kW at 1000 W/m2), without losses:
    a clear day therefore reads ``kt`` of about 0.75-0.95. The location comes from Home
    Assistant's ``get_config`` (``latitude``/``longitude``), the arrays from ``site.pv``.

``daily_peak`` (fallback: no location or an array without ``kwp``)
    No sun model is possible, so the class is a **day-level** index: the forecast's peak of the
    local day divided by a reference peak: the 95th percentile of the daily forecast peaks the
    bias table has stored (up to a year; a percentile, not the maximum, so one absurd forecast
    does not push every later day into ``overcast``), or - without stored peaks - of the
    series' own daily peaks. All daytime slots of a day share the class; a slot is daytime when
    the forecast exceeds 2 % of the reference peak.

The exact numbers matter little: calibration only needs the class to be a *consistent*
function of the forecast, because the same function sorts the learning samples and the slots
the factors are applied to. A systematic offset of the model (an east-west roof, a shaded
plane) shifts slots between classes the same way in both.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from enum import StrEnum, unique
from typing import Final, Literal

from suncore.core.series import TimeSeries
from suncore.core.site import PvArray

ClassifierMode = Literal["clear_sky", "daily_peak"]

OVERCAST_BELOW: Final = 0.3
"""``kt`` below this is ``overcast``."""
CLEAR_FROM: Final = 0.65
"""``kt`` from this on is ``clear``; in between ``mixed``."""
TWILIGHT_SHARE: Final = 0.03
"""Clear-sky power below this share of the installed kWp is twilight: no class."""
DAYTIME_SHARE: Final = 0.02
"""``daily_peak`` mode: a forecast below this share of the reference peak is not daytime."""
DIFFUSE_SHARE: Final = 0.10
ALBEDO: Final = 0.20
DEFAULT_TILT_DEG: Final = 30.0


@unique
class WeatherClass(StrEnum):
    """Cloudiness of a slot as the forecast sees it (clear-sky index bucket)."""

    OVERCAST = "overcast"
    MIXED = "mixed"
    CLEAR = "clear"


def classify_kt(kt: float) -> WeatherClass:
    if kt < OVERCAST_BELOW:
        return WeatherClass.OVERCAST
    if kt < CLEAR_FROM:
        return WeatherClass.MIXED
    return WeatherClass.CLEAR


@dataclass(frozen=True, slots=True)
class ArrayGeometry:
    kwp: float
    azimuth_deg: float
    """0 = north, 90 = east, 180 = south."""
    tilt_deg: float


@dataclass(frozen=True, slots=True)
class PlantGeometry:
    latitude: float
    longitude: float
    arrays: tuple[ArrayGeometry, ...]

    @property
    def total_kwp(self) -> float:
        return sum(a.kwp for a in self.arrays)


def plant_geometry(
    latitude: float | None, longitude: float | None, arrays: Sequence[PvArray]
) -> PlantGeometry | None:
    """The geometry for the ``clear_sky`` mode, or ``None`` (fallback ``daily_peak``).

    ``None`` when the location is unknown, there is no array or an array has no ``kwp``.
    """
    if latitude is None or longitude is None or not arrays:
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    default_azimuth = 180.0 if latitude >= 0 else 0.0
    out: list[ArrayGeometry] = []
    for array in arrays:
        if array.kwp is None:
            return None
        out.append(
            ArrayGeometry(
                kwp=array.kwp,
                azimuth_deg=default_azimuth if array.azimuth_deg is None else array.azimuth_deg,
                tilt_deg=DEFAULT_TILT_DEG if array.tilt_deg is None else array.tilt_deg,
            )
        )
    return PlantGeometry(latitude=latitude, longitude=longitude, arrays=tuple(out))


def sun_position(when: datetime, latitude: float, longitude: float) -> tuple[float, float]:
    """``(elevation, azimuth)`` in radians; azimuth from north, clockwise (90 deg = east)."""
    utc = when.astimezone(UTC)
    day = utc.timetuple().tm_yday
    hours = utc.hour + utc.minute / 60 + utc.second / 3600
    gamma = 2 * math.pi / 365 * (day - 1 + (hours - 12) / 24)
    declination = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )
    eq_time_min = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    solar_min = hours * 60 + eq_time_min + 4 * longitude
    hour_angle = math.radians(solar_min / 4 - 180)
    lat = math.radians(latitude)
    sin_el = math.sin(lat) * math.sin(declination) + math.cos(lat) * math.cos(
        declination
    ) * math.cos(hour_angle)
    sin_el = min(1.0, max(-1.0, sin_el))
    elevation = math.asin(sin_el)
    cos_el = math.cos(elevation)
    if cos_el < 1e-9 or abs(math.cos(lat)) < 1e-9:
        return elevation, math.pi
    cos_az = (math.sin(declination) - sin_el * math.sin(lat)) / (cos_el * math.cos(lat))
    azimuth = math.acos(min(1.0, max(-1.0, cos_az)))
    if hour_angle > 0:
        azimuth = 2 * math.pi - azimuth
    return elevation, azimuth


def clear_sky_power_w(plant: PlantGeometry, when: datetime) -> float:
    """Clear-sky power of the plant at one instant (W, no losses)."""
    elevation, sun_az = sun_position(when, plant.latitude, plant.longitude)
    cos_z = math.sin(elevation)
    if cos_z <= 0.01:
        return 0.0
    ghi = 1098.0 * cos_z * math.exp(-0.057 / cos_z)
    dhi = DIFFUSE_SHARE * ghi
    dni = (ghi - dhi) / cos_z
    sin_z = math.cos(elevation)
    total = 0.0
    for array in plant.arrays:
        beta = math.radians(array.tilt_deg)
        cos_inc = cos_z * math.cos(beta) + sin_z * math.sin(beta) * math.cos(
            sun_az - math.radians(array.azimuth_deg)
        )
        poa = (
            dni * max(0.0, cos_inc)
            + dhi * (1 + math.cos(beta)) / 2
            + ALBEDO * ghi * (1 - math.cos(beta)) / 2
        )
        total += array.kwp * poa
    return total


def clear_sky_slot_w(plant: PlantGeometry, start: datetime, step: timedelta) -> float:
    """Mean clear-sky power over a slot (three sample points)."""
    points = (start + step * (k / 6) for k in (1, 3, 5))
    return sum(clear_sky_power_w(plant, p) for p in points) / 3


PEAK_PERCENTILE: Final = 0.95


def peak_reference(peaks: Sequence[float]) -> float | None:
    """The ``PEAK_PERCENTILE`` (nearest rank) of positive daily peaks, ``None`` without any."""
    ordered = sorted(p for p in peaks if p > 0)
    if not ordered:
        return None
    rank = max(1, math.ceil(PEAK_PERCENTILE * len(ordered)))
    return ordered[rank - 1]


def daily_peaks(series: TimeSeries, tz: tzinfo) -> dict[date, float]:
    """Largest known value per local day."""
    peaks: dict[date, float] = {}
    for begin, _, value in series.slots():
        if value is None:
            continue
        day = begin.astimezone(tz).date()
        peaks[day] = max(peaks.get(day, 0.0), value)
    return peaks


def classify_slots(
    forecast: TimeSeries,
    *,
    plant: PlantGeometry | None,
    tz: tzinfo,
    peak_reference_w: float | None = None,
) -> list[WeatherClass | None]:
    """The weather class of every slot of ``forecast`` (P50, W); ``None`` = night/unknown.

    ``plant`` selects the mode (module doc); ``peak_reference_w`` is the ``daily_peak`` mode's
    reference (``None``: the percentile of the series' own daily peaks).
    """
    if plant is not None:
        twilight = TWILIGHT_SHARE * plant.total_kwp * 1000
        out: list[WeatherClass | None] = []
        for begin, end, value in forecast.slots():
            clear = clear_sky_slot_w(plant, begin, end - begin)
            out.append(None if value is None or clear < twilight else classify_kt(value / clear))
        return out
    peaks = daily_peaks(forecast, tz)
    reference = peak_reference_w or peak_reference(list(peaks.values())) or 0.0
    if reference <= 0:
        return [None] * len(forecast)
    result: list[WeatherClass | None] = []
    for begin, _, value in forecast.slots():
        if value is None or value < DAYTIME_SHARE * reference:
            result.append(None)
            continue
        result.append(classify_kt(peaks[begin.astimezone(tz).date()] / reference))
    return result


def classifier_mode(plant: PlantGeometry | None) -> ClassifierMode:
    return "daily_peak" if plant is None else "clear_sky"


__all__ = [
    "CLEAR_FROM",
    "OVERCAST_BELOW",
    "ArrayGeometry",
    "ClassifierMode",
    "PlantGeometry",
    "WeatherClass",
    "classifier_mode",
    "classify_kt",
    "classify_slots",
    "clear_sky_power_w",
    "clear_sky_slot_w",
    "daily_peaks",
    "peak_reference",
    "plant_geometry",
    "sun_position",
]
