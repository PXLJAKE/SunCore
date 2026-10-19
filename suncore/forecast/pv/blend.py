# SPDX-License-Identifier: Apache-2.0
"""Blending of several PV forecast sources into one quantile forecast (Phase 4 WP5).

**Weights** are set per horizon bucket (the lead time of a slot: ``0_6h``, ``6_24h``,
``24_48h``; a slot beyond 48 h uses ``24_48h``):

* ``weight_mode = auto``: the inverse of the source's MAE over the last 14 days
  (:mod:`suncore.forecast.accuracy`, records with at least ``ACCURACY_MIN_SAMPLES``
  quarter hours; the MAE is floored at ``MAE_FLOOR_W``). While **any** auto source lacks such a
  record for the bucket, the auto sources share equally (``basis = equal_no_history``) - skill
  cannot be compared with one side unknown.
* every auto source keeps at least ``min_weight`` (0.1, or ``0.5 / sources`` when there are more
  than five), so a source that has data is never dropped entirely: ``w = f + (share - n f) s``
  where ``s`` is the normalised skill, ``f`` the floor and ``share`` what the auto sources get.
* ``weight_mode = manual``: the configured ``weight`` is the source's share of the total. The
  auto sources split the rest (``1 - sum(manual)``, but at least their floors; the manual shares
  are scaled down to make room). Only manual sources: their weights are normalised.

In every slot the weights are **renormalised over the sources that have a value there**.

**Quantiles.** ``P50 = sum(w_i P50_i)``. The spread combines two parts like the law of total
variance (within-source uncertainty + disagreement between sources):

* ``d_lo = sqrt(sum_Q w_i (P50_i - P10_i)^2 / sum_Q w_i)`` over the sources ``Q`` that provide
  real quantiles in the slot (``P10 < P50`` or ``P90 > P50``), ``d_hi`` alike with ``P90``; 0
  when no source provides quantiles;
* ``s = sqrt(sum w_i (P50_i - P50)^2)``, the weighted spread of the sources' medians;
* ``P10 = max(0, P50 - sqrt(d_lo^2 + (z s)^2))``, ``P90 = P50 + sqrt(d_hi^2 + (z s)^2)`` with
  ``z = 1.2816`` (the 90 % quantile of a normal distribution).

So ``P10 <= P50 <= P90`` always holds, agreeing sources keep their own band, and disagreeing
ones widen it. A slot with a single source passes its values through unchanged.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final, Literal

from suncore.core.forecast import ACCURACY_MIN_SAMPLES, AccuracyRecord, HorizonBucket
from suncore.core.series import (
    PLANNING_STEP,
    Grid,
    QuantileSeries,
    SeriesUnit,
    TimeSeries,
    planning_grid,
    resample_quantiles,
)

MIN_WEIGHT: Final = 0.1
MAE_FLOOR_W: Final = 10.0
Z90: Final = 1.2815515655446004
BLEND_HORIZON: Final = timedelta(hours=48)

WEIGHT_BUCKETS: Final = (HorizonBucket.H0_6, HorizonBucket.H6_24, HorizonBucket.H24_48)
AutoBasis = Literal["inverse_mae", "equal_no_history"]
BlendMethod = Literal["weighted", "single_source"]


@dataclass(frozen=True, slots=True)
class BlendInput:
    """One source as the blender sees it."""

    source_id: str
    series: QuantileSeries
    weight_mode: Literal["auto", "manual"] = "auto"
    manual_weight: float | None = None
    records: Sequence[AccuracyRecord] = ()

    @property
    def is_manual(self) -> bool:
        return self.weight_mode == "manual" and self.manual_weight is not None


@dataclass(frozen=True, slots=True)
class BucketWeights:
    """The weights of one horizon bucket before the per-slot renormalisation."""

    horizon: HorizonBucket
    weights: Mapping[str, float]
    auto_basis: AutoBasis | None
    """How the auto sources were weighted; ``None`` = there is no auto source."""
    manual: bool
    """Whether a manual weight took part."""


@dataclass(frozen=True, slots=True)
class BlendResult:
    series: QuantileSeries
    method: BlendMethod
    weights: tuple[BucketWeights, ...]
    sources: tuple[str, ...] = field(default=())


def weight_bucket(lead: timedelta) -> HorizonBucket:
    """The bucket whose weights apply to a slot ``lead`` after the blend time."""
    bucket = HorizonBucket.of(lead)
    return HorizonBucket.H24_48 if bucket is HorizonBucket.H48_PLUS else bucket


def _usable_mae(records: Sequence[AccuracyRecord], bucket: HorizonBucket) -> float | None:
    for record in records:
        if record.horizon is bucket and record.n >= ACCURACY_MIN_SAMPLES:
            return record.mae_w
    return None


def source_weights(
    inputs: Sequence[BlendInput],
    bucket: HorizonBucket,
    *,
    min_weight: float = MIN_WEIGHT,
    mae_floor_w: float = MAE_FLOOR_W,
) -> BucketWeights:
    """The weights of ``inputs`` for one bucket (sum 1; module doc)."""
    if not inputs:
        return BucketWeights(bucket, {}, None, manual=False)
    manual = [i for i in inputs if i.is_manual]
    auto = [i for i in inputs if not i.is_manual]
    floor = min(min_weight, 0.5 / len(inputs))
    weights: dict[str, float] = {}
    manual_sum = sum(i.manual_weight or 0.0 for i in manual)
    if not auto:
        for item in manual:
            weights[item.source_id] = (item.manual_weight or 0.0) / manual_sum
        return BucketWeights(bucket, weights, None, manual=True)
    auto_share = 1.0 if not manual else min(1.0, max(1.0 - manual_sum, floor * len(auto)))
    for item in manual:
        weights[item.source_id] = (item.manual_weight or 0.0) * (1.0 - auto_share) / manual_sum
    maes = [_usable_mae(i.records, bucket) for i in auto]
    basis: AutoBasis
    if all(m is not None for m in maes):
        inverse = [1.0 / max(m or 0.0, mae_floor_w) for m in maes]
        total = sum(inverse)
        skill = [v / total for v in inverse]
        basis = "inverse_mae"
    else:
        skill = [1.0 / len(auto)] * len(auto)
        basis = "equal_no_history"
    spare = auto_share - floor * len(auto)
    for item, s in zip(auto, skill, strict=True):
        weights[item.source_id] = floor + spare * s
    return BucketWeights(bucket, weights, basis, manual=bool(manual))


SlotValues = tuple[float, float | None, float, float | None]
"""``(weight, p10, p50, p90)`` of one source in one slot."""


def blend_slot(values: Sequence[SlotValues]) -> tuple[float, float, float] | None:
    """``(P10, P50, P90)`` of one slot from the sources that have a P50 there (module doc)."""
    if not values:
        return None
    if len(values) == 1:
        _, lo, mid, hi = values[0]
        return (mid if lo is None else lo, mid, mid if hi is None else hi)
    total = sum(v[0] for v in values)
    if total <= 0:
        return None
    p50 = sum(w * mid for w, _, mid, _ in values) / total
    spread2 = sum(w * (mid - p50) ** 2 for w, _, mid, _ in values) / total
    q_weight = 0.0
    lo2 = hi2 = 0.0
    for w, lo, mid, hi in values:
        if lo is None or hi is None or (lo >= mid and hi <= mid):
            continue
        q_weight += w
        lo2 += w * max(0.0, mid - lo) ** 2
        hi2 += w * max(0.0, hi - mid) ** 2
    if q_weight > 0:
        lo2 /= q_weight
        hi2 /= q_weight
    disagreement2 = (Z90**2) * spread2
    p10 = p50 - math.sqrt(lo2 + disagreement2)
    if p50 >= 0:
        p10 = max(0.0, p10)
    p90 = p50 + math.sqrt(hi2 + disagreement2)
    return (min(p10, p50), p50, max(p90, p50))


def blend(
    inputs: Sequence[BlendInput],
    now: datetime,
    *,
    horizon: timedelta = BLEND_HORIZON,
    grid: Grid | None = None,
    min_weight: float = MIN_WEIGHT,
) -> BlendResult | None:
    """The blended forecast on the 15-minute planning grid from ``now`` (``None``: no data)."""
    target = grid or planning_grid(now, horizon, PLANNING_STEP)
    on_grid = [(i, resample_quantiles(i.series, target)) for i in inputs]
    present = [(i, q) for i, q in on_grid if any(v is not None for v in q.p50.values)]
    if not present:
        return None
    used = [i for i, _ in present]
    per_bucket = {b: source_weights(used, b, min_weight=min_weight) for b in WEIGHT_BUCKETS}
    p10: list[float | None] = []
    p50: list[float | None] = []
    p90: list[float | None] = []
    for index, begin in enumerate(target.starts()):
        weights = per_bucket[weight_bucket(begin - now)].weights
        values: list[SlotValues] = []
        for item, series in present:
            mid = series.p50.values[index]
            if mid is None:
                continue
            values.append(
                (weights[item.source_id], series.p10.values[index], mid, series.p90.values[index])
            )
        slot = blend_slot(values)
        p10.append(None if slot is None else slot[0])
        p50.append(None if slot is None else slot[1])
        p90.append(None if slot is None else slot[2])

    def part(values: list[float | None]) -> TimeSeries:
        return TimeSeries(
            start=target.start, step=target.step, values=tuple(values), unit=SeriesUnit.W
        )

    return BlendResult(
        series=QuantileSeries(p10=part(p10), p50=part(p50), p90=part(p90)),
        method="single_source" if len(present) == 1 else "weighted",
        weights=tuple(per_bucket[b] for b in WEIGHT_BUCKETS),
        sources=tuple(i.source_id for i in used),
    )


__all__ = [
    "BLEND_HORIZON",
    "MAE_FLOOR_W",
    "MIN_WEIGHT",
    "WEIGHT_BUCKETS",
    "AutoBasis",
    "BlendInput",
    "BlendMethod",
    "BlendResult",
    "BucketWeights",
    "blend",
    "blend_slot",
    "source_weights",
    "weight_bucket",
]
