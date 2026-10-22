# SPDX-License-Identifier: Apache-2.0
"""Rebuild of the 15-minute site history from Home Assistant's statistics (Phase 4 WP3).

Runs once after the first successful connect when ``measurement_15m`` is empty (a restore
without ``timeseries.db``, a fresh install). The configured site entities are asked for their
hourly statistics (``suncore.ha.statistics``); the answers are combined the way the live
balance does (``suncore.core.balance.compute_flows``): grid as ``import - export`` for a pair,
PV as the sum of the arrays, the house from its sensor or from ``pv + grid - battery``. The
rows are stored as *derived* quarter hours.

Controlled devices with a power sensor (``power_model.power_role`` mapped) are rebuilt too, from
the same statistics (``mean``, W) into ``device.<id>.power_w``, so the load forecast can take
their draw out of the base load from the first day (PR 4-E review M2). A device without a power
sensor (nominal or counter-derived model) has no statistic; its quarter hours stay unmeasured.
``UNVERIFIED``: whether the Supervisor proxy forwards ``recorder/*``; an error is reported to
the caller, which logs it and carries on without history.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog

from suncore.core.devices import DeviceConfig
from suncore.core.measurements import (
    SITE_BATTERY_W,
    SITE_GRID_W,
    SITE_HOUSE_W,
    SITE_PV_W,
    SITE_SOC_PCT,
    device_power_key,
)
from suncore.core.series import Grid, SeriesUnit, TimeSeries, resample_power
from suncore.core.site import SiteConfig
from suncore.core.units import house_load_w
from suncore.ha.connection import HAConnection
from suncore.ha.statistics import StatisticSpec, fetch_quarter_history
from suncore.storage.timeseries import TimeseriesStore

log = structlog.get_logger(__name__)

REBUILD_DAYS = 28
"""History that is rebuilt (the load forecast needs 14 days, the risk model starts at 30)."""

_GRID, _GRID_IMPORT, _GRID_EXPORT = "grid", "grid_import", "grid_export"
_BATTERY, _SOC, _HOUSE = "battery", "soc", "house"


@dataclass(frozen=True, slots=True)
class RebuildResult:
    """What a rebuild stored: quarter hours per measurement key, and the statistic ids
    Home Assistant had no data for."""

    stored: Mapping[str, int]
    missing: tuple[str, ...]

    @property
    def total(self) -> int:
        return sum(self.stored.values())


def site_statistic_specs(site: SiteConfig) -> list[StatisticSpec]:
    """The statistics to ask for: every mapped power entity and the state of charge."""
    specs: list[StatisticSpec] = []
    grid = site.grid
    if grid.power is not None:
        specs.append(StatisticSpec(_GRID, grid.power.entity_id, invert=grid.power.invert_sign))
    else:
        assert grid.import_power is not None
        assert grid.export_power is not None
        specs.append(
            StatisticSpec(
                _GRID_IMPORT, grid.import_power.entity_id, invert=grid.import_power.invert_sign
            )
        )
        specs.append(
            StatisticSpec(
                _GRID_EXPORT, grid.export_power.entity_id, invert=grid.export_power.invert_sign
            )
        )
    specs.extend(
        StatisticSpec(f"pv{i}", ref.entity_id, invert=ref.invert_sign)
        for i, ref in enumerate(site.pv_power_refs)
    )
    if site.battery is not None:
        power = site.battery.power
        specs.append(StatisticSpec(_BATTERY, power.entity_id, invert=power.invert_sign))
        specs.append(StatisticSpec(_SOC, site.battery.soc.entity_id, unit=SeriesUnit.PERCENT))
    if site.house is not None:
        specs.append(StatisticSpec(_HOUSE, site.house.entity_id, invert=site.house.invert_sign))
    return specs


def _common_grid(series: Sequence[TimeSeries]) -> Grid:
    start = min(s.start for s in series)
    end = max(s.end for s in series)
    step = series[0].step
    return Grid(start=start, step=step, count=int((end - start) / step))


def _combine(parts: Sequence[TimeSeries | None], fn: Callable[..., float]) -> TimeSeries | None:
    """Slot-wise ``fn`` over series on one grid; ``None`` if a part is missing in the slot."""
    present = [p for p in parts if p is not None]
    if len(present) != len(parts) or not present:
        return None
    grid = _common_grid(present)
    aligned = [resample_power(p, grid) for p in present]
    values = tuple(
        None if any(v is None for v in slot) else fn(*slot)
        for slot in zip(*(a.values for a in aligned), strict=True)
    )
    return TimeSeries(start=grid.start, step=grid.step, values=values, unit=aligned[0].unit)


def assemble_site_series(
    site: SiteConfig, fetched: Mapping[str, TimeSeries]
) -> dict[str, TimeSeries]:
    """Measurement key -> 15-minute series, combined like the live power balance."""
    get = fetched.get
    if site.grid.power is not None:
        grid = get(_GRID)
    else:
        grid = _combine([get(_GRID_IMPORT), get(_GRID_EXPORT)], lambda i, e: i - e)
    pv_parts = [get(f"pv{i}") for i in range(len(site.pv_power_refs))]
    pv = _combine(pv_parts, lambda *v: sum(v)) if pv_parts else None
    if site.battery is None:
        battery = soc = None
    else:
        battery, soc = get(_BATTERY), get(_SOC)
    if site.house is not None:
        house = get(_HOUSE)
    else:
        # pv + grid - battery; a site without PV or battery contributes 0 W for it.
        has_pv, has_battery = bool(pv_parts), site.battery is not None
        parts = [grid, *([pv] if has_pv else []), *([battery] if has_battery else [])]

        def balance(*v: float) -> float:
            it = iter(v)
            g = next(it)
            return house_load_w(next(it) if has_pv else 0.0, g, next(it) if has_battery else 0.0)

        house = _combine(parts, balance)
    named = {
        SITE_GRID_W: grid,
        SITE_PV_W: pv,
        SITE_BATTERY_W: battery,
        SITE_SOC_PCT: soc,
        SITE_HOUSE_W: house,
    }
    return {key: series for key, series in named.items() if series is not None}


_DEVICE_PREFIX = "device:"


def device_statistic_specs(devices: Sequence[DeviceConfig]) -> list[StatisticSpec]:
    """The power sensor of every device that maps one (``power_model.power_role``)."""
    specs: list[StatisticSpec] = []
    for device in devices:
        role = device.power_model.power_role
        ref = None if role is None else device.entities.get(role)
        if ref is not None:
            specs.append(
                StatisticSpec(f"{_DEVICE_PREFIX}{device.id}", ref.entity_id, invert=ref.invert_sign)
            )
    return specs


async def rebuild_history(
    connection: HAConnection,
    store: TimeseriesStore,
    site: SiteConfig,
    now: datetime,
    *,
    days: int = REBUILD_DAYS,
    devices: Sequence[DeviceConfig] = (),
) -> RebuildResult:
    """Fetch the site's (and the devices' power) hourly statistics and store them as derived
    quarter hours.

    Home Assistant errors propagate. Existing measured rows are never overwritten.
    """
    end = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    specs = [*site_statistic_specs(site), *device_statistic_specs(devices)]
    history = await fetch_quarter_history(connection, specs, end - timedelta(days=days), end)
    stored: dict[str, int] = {}
    for key, series in assemble_site_series(site, history.series).items():
        stored[key] = await store.store_quarter_hours(key, series, derived=True)
    for key, series in history.series.items():
        if key.startswith(_DEVICE_PREFIX):
            device_key = device_power_key(key.removeprefix(_DEVICE_PREFIX))
            stored[device_key] = await store.store_quarter_hours(device_key, series, derived=True)
    result = RebuildResult(stored=stored, missing=history.missing)
    log.info("history.rebuilt", quarter_hours=result.total, missing=list(result.missing))
    return result
