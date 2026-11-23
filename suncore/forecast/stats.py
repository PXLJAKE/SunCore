# SPDX-License-Identifier: Apache-2.0
"""Small robust statistics shared by the load forecast and the risk model (Phase 4 WP6/WP7).

Pure functions on plain floats; no NumPy needed for the sizes involved (a few thousand values).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Final

MAD_TO_SIGMA: Final = 1.4826
"""Scale factor that makes the median absolute deviation a consistent estimate of the standard
deviation of a normal distribution."""


def median(values: Sequence[float]) -> float:
    """The median; ``ValueError`` on an empty sequence."""
    if not values:
        msg = "median of an empty sequence"
        raise ValueError(msg)
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def robust_scale(values: Sequence[float], center: float) -> float:
    """``MAD_TO_SIGMA`` times the median absolute deviation from ``center``."""
    return MAD_TO_SIGMA * median([abs(v - center) for v in values])


def clip_outliers(values: Sequence[float], z: float, floor: float) -> list[float]:
    """Clipped z-score: every value is clipped into ``median +- z * scale``.

    ``scale`` is the MAD-based robust standard deviation, at least ``floor`` (a cell whose
    values are almost all equal would otherwise clip everything onto the median).
    """
    if not values:
        return []
    center = median(values)
    scale = max(robust_scale(values, center), floor)
    lo, hi = center - z * scale, center + z * scale
    return [min(hi, max(lo, v)) for v in values]


def weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    total = sum(weights)
    if total <= 0:
        msg = "weighted mean needs a positive total weight"
        raise ValueError(msg)
    return sum(v * w for v, w in zip(values, weights, strict=True)) / total


def weighted_quantile(values: Sequence[float], weights: Sequence[float], q: float) -> float:
    """The ``q``-quantile of a weighted sample (linear interpolation between the midpoints of
    the cumulative weights; equal weights give the usual "Hazen" plotting positions).

    ``q`` in ``[0, 1]``; values beyond the outermost midpoints take the extreme value (the
    sample never extrapolates).
    """
    if not values:
        msg = "quantile of an empty sample"
        raise ValueError(msg)
    if not 0 <= q <= 1:
        msg = "q must be in [0, 1]"
        raise ValueError(msg)
    pairs: list[tuple[float, float]] = sorted(
        ((v, w) for v, w in zip(values, weights, strict=True) if w > 0), key=lambda p: p[0]
    )
    if not pairs:
        msg = "quantile needs a positive weight"
        raise ValueError(msg)
    total = sum(w for _, w in pairs)
    positions: list[float] = []
    running = 0.0
    for _, w in pairs:
        positions.append((running + w / 2) / total)
        running += w
    if q <= positions[0]:
        return pairs[0][0]
    if q >= positions[-1]:
        return pairs[-1][0]
    for i in range(1, len(pairs)):
        if q <= positions[i]:
            lo, hi = positions[i - 1], positions[i]
            share = 0.0 if math.isclose(hi, lo) else (q - lo) / (hi - lo)
            return pairs[i - 1][0] + share * (pairs[i][0] - pairs[i - 1][0])
    return pairs[-1][0]  # pragma: no cover - q < positions[-1] ends the loop


def recency_weight(age_days: float, half_life_days: float) -> float:
    """Exponential-smoothing weight of an observation ``age_days`` old: ``0.5 ** (age / h)``."""
    return math.pow(0.5, max(0.0, age_days) / half_life_days)


__all__ = [
    "MAD_TO_SIGMA",
    "clip_outliers",
    "median",
    "recency_weight",
    "robust_scale",
    "weighted_mean",
    "weighted_quantile",
]
