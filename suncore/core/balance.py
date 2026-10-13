# SPDX-License-Identifier: Apache-2.0
"""Pure power-balance computation: raw samples + SiteConfig -> FlowSnapshot.

Rules:
- ``invert_sign`` is applied per entity, then values are converted to W (or %).
- Unknown/missing unit, wrong dimension or ``invalid`` quality -> value ``None``
  with quality ``invalid``; missing sample or ``unavailable`` -> ``None`` with
  ``unavailable``. ``stale`` samples keep their value and carry ``stale``.
- Combined values (pair, PV sum, derived house) carry the worst input quality
  and are ``None`` as soon as one input has no value.
- Unconfigured optional parts: no PV -> 0 W (ok); no battery -> 0 W (ok) and
  SoC ``None``/``unavailable``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from suncore.core.signals import Quality, Sample
from suncore.core.site import EntityRef, SiteConfig
from suncore.core.units import (
    Dimension,
    apply_invert,
    house_load_w,
    net_grid_w,
    to_base,
)


@dataclass(frozen=True, slots=True)
class Flow:
    """A value in base units (W or %) with its quality."""

    value: float | None
    quality: Quality


@dataclass(frozen=True, slots=True)
class FlowSnapshot:
    """Site power flows in W (core sign convention) and battery SoC in %."""

    grid_w: Flow
    pv_w: Flow
    battery_w: Flow
    house_w: Flow
    battery_soc_pct: Flow
    ts: datetime | None = None
    """Timestamp of the oldest sample that contributed; ``None`` if there was none."""


_MISSING = Flow(None, Quality.UNAVAILABLE)


def read_flow(
    ref: EntityRef,
    samples: Mapping[str, Sample],
    dimension: Dimension,
) -> Flow:
    """Read one entity as a base-unit flow, applying ``invert_sign``."""
    sample = samples.get(ref.entity_id)
    if sample is None:
        return _MISSING
    if sample.quality is Quality.INVALID:
        return Flow(None, Quality.INVALID)
    if sample.value is None or sample.quality is Quality.UNAVAILABLE:
        return Flow(None, Quality.worst((sample.quality, Quality.UNAVAILABLE)))
    if sample.unit is None or sample.unit.dimension is not dimension:
        return Flow(None, Quality.INVALID)
    value = to_base(sample.value, sample.unit)
    if not math.isfinite(value):
        return Flow(None, Quality.INVALID)
    return Flow(apply_invert(value, ref.invert_sign), sample.quality)


def _sum(flows: list[Flow]) -> Flow:
    quality = Quality.worst(f.quality for f in flows)
    values = [f.value for f in flows]
    total = 0.0
    for v in values:
        if v is None:
            return Flow(None, _no_value_quality(quality))
        total += v
    return Flow(total, quality)


def compute_flows(site: SiteConfig, samples: Mapping[str, Sample]) -> FlowSnapshot:
    """Compute the :class:`FlowSnapshot` for ``site`` from raw ``samples`` (by entity id)."""
    power = Dimension.POWER

    # Grid
    g = site.grid
    if g.power is not None:
        grid = read_flow(g.power, samples, power)
    else:
        assert g.import_power is not None
        assert g.export_power is not None
        imp = read_flow(g.import_power, samples, power)
        exp = read_flow(g.export_power, samples, power)
        quality = Quality.worst((imp.quality, exp.quality))
        if imp.value is None or exp.value is None:
            grid = Flow(None, _no_value_quality(quality))
        else:
            grid = Flow(net_grid_w(imp.value, exp.value), quality)

    # PV
    pv = _sum([read_flow(r, samples, power) for r in site.pv_power_refs])

    # Battery
    if site.battery is None:
        battery = Flow(0.0, Quality.OK)
        soc = _MISSING
    else:
        battery = read_flow(site.battery.power, samples, power)
        soc = read_flow(site.battery.soc, samples, Dimension.RATIO)

    # House
    if site.house is not None:
        house = read_flow(site.house, samples, power)
    else:
        quality = Quality.worst((pv.quality, grid.quality, battery.quality))
        if pv.value is None or grid.value is None or battery.value is None:
            house = Flow(None, _no_value_quality(quality))
        else:
            house = Flow(house_load_w(pv.value, grid.value, battery.value), quality)

    used = [samples[r.entity_id].ts for r in site.entity_refs() if r.entity_id in samples]
    return FlowSnapshot(
        grid_w=grid,
        pv_w=pv,
        battery_w=battery,
        house_w=house,
        battery_soc_pct=soc,
        ts=min(used) if used else None,
    )


def _no_value_quality(quality: Quality) -> Quality:
    """Quality for a combined value that could not be computed (never better than unavailable)."""
    return quality if quality.severity >= Quality.UNAVAILABLE.severity else Quality.UNAVAILABLE
