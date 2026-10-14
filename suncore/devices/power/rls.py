# SPDX-License-Identifier: Apache-2.0
"""Recursive least squares with forgetting, in covariance form, pure Python.

``y = w . x + noise``. The weight vector starts at a prior ``w0`` with a prior covariance
``P0`` (diagonal, in the units of ``y`` squared per feature), so a few samples refine the
prior instead of replacing it. The covariance is clamped to ``P0`` after every update
(no wind-up when a feature is never excited) and the update is skipped for non-finite input.
Dimensions are tiny (<= 8), so lists are faster than anything that needs an import.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any


class RecursiveLeastSquares:
    """Online linear regression with a prior and exponential forgetting."""

    def __init__(
        self,
        prior_weights: Sequence[float],
        prior_variances: Sequence[float],
        *,
        noise_variance: float,
        forgetting: float = 0.999,
    ) -> None:
        if len(prior_weights) != len(prior_variances) or not prior_weights:
            msg = "prior_weights and prior_variances must have the same non-zero length"
            raise ValueError(msg)
        if any(v <= 0 for v in prior_variances) or noise_variance <= 0:
            msg = "variances must be positive"
            raise ValueError(msg)
        if not 0.9 <= forgetting <= 1.0:
            msg = "forgetting must be in [0.9, 1]"
            raise ValueError(msg)
        self._dim = len(prior_weights)
        self._w0 = [float(w) for w in prior_weights]
        self._p0 = [float(v) for v in prior_variances]
        self._noise = float(noise_variance)
        self._lambda = float(forgetting)
        self.reset()

    # --- state ---------------------------------------------------------------------

    def reset(self) -> None:
        self.weights = list(self._w0)
        self.cov = [
            [self._p0[i] if i == j else 0.0 for j in range(self._dim)] for i in range(self._dim)
        ]
        self.updates = 0

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def noise_variance(self) -> float:
        return self._noise

    def predict(self, x: Sequence[float]) -> float:
        return sum(w * xi for w, xi in zip(self.weights, x, strict=True))

    def prediction_variance(self, x: Sequence[float]) -> float:
        """Variance of a new observation at ``x``: parameter uncertainty plus noise."""
        px = self._mat_vec(x)
        return max(0.0, sum(xi * pxi for xi, pxi in zip(x, px, strict=True))) + self._noise

    def update(self, x: Sequence[float], y: float) -> bool:
        """Learn one sample; returns ``False`` (nothing changed) for non-finite input."""
        if len(x) != self._dim or not math.isfinite(y) or not all(math.isfinite(v) for v in x):
            return False
        px = self._mat_vec(x)
        denom = self._lambda * self._noise + sum(xi * pxi for xi, pxi in zip(x, px, strict=True))
        if not math.isfinite(denom) or denom <= 0:
            return False
        gain = [v / denom for v in px]
        error = y - self.predict(x)
        new_w = [w + g * error for w, g in zip(self.weights, gain, strict=True)]
        new_cov = self._joseph(x, gain)
        # No wind-up: scale the whole matrix (it stays positive semi-definite) so that no
        # variance exceeds its prior.
        scale = min(
            (self._p0[i] / new_cov[i][i] for i in range(self._dim) if new_cov[i][i] > 0),
            default=1.0,
        )
        if scale < 1.0:
            new_cov = [[v * scale for v in row] for row in new_cov]
        if not (
            all(math.isfinite(w) for w in new_w)
            and all(math.isfinite(v) for r in new_cov for v in r)
        ):
            return False
        self.weights = new_w
        self.cov = _symmetrise(new_cov)
        self.updates += 1
        return True

    def _mat_vec(self, x: Sequence[float]) -> list[float]:
        return [sum(self.cov[i][j] * x[j] for j in range(self._dim)) for i in range(self._dim)]

    def _joseph(self, x: Sequence[float], gain: Sequence[float]) -> list[list[float]]:
        """Joseph-form covariance update with forgetting (keeps the matrix PSD)."""
        n = self._dim
        a = [[(1.0 if i == j else 0.0) - gain[i] * x[j] for j in range(n)] for i in range(n)]
        ap = [[sum(a[i][k] * self.cov[k][j] for k in range(n)) for j in range(n)] for i in range(n)]
        apa = [[sum(ap[i][k] * a[j][k] for k in range(n)) for j in range(n)] for i in range(n)]
        return [
            [(apa[i][j] + gain[i] * gain[j] * self._noise) / self._lambda for j in range(n)]
            for i in range(n)
        ]

    # --- persistence -----------------------------------------------------------------

    def dump(self) -> dict[str, Any]:
        return {
            "weights": list(self.weights),
            "cov": [list(r) for r in self.cov],
            "updates": self.updates,
        }

    def load(self, data: dict[str, Any]) -> bool:
        """Restore :meth:`dump`; ``False`` (state unchanged) for anything malformed."""
        try:
            weights = [float(v) for v in data["weights"]]
            cov = [[float(v) for v in row] for row in data["cov"]]
            updates = int(data["updates"])
        except (KeyError, TypeError, ValueError):
            return False
        if len(weights) != self._dim or len(cov) != self._dim or updates < 0:
            return False
        if any(len(row) != self._dim for row in cov):
            return False
        if not (math.isfinite(sum(weights)) and all(math.isfinite(v) for r in cov for v in r)):
            return False
        if any(not 0 <= cov[i][i] <= self._p0[i] * (1 + 1e-9) for i in range(self._dim)):
            return False
        self.weights = weights
        self.cov = cov
        self.updates = updates
        return True


def _symmetrise(m: list[list[float]]) -> list[list[float]]:
    n = len(m)
    return [[(m[i][j] + m[j][i]) / 2.0 for j in range(n)] for i in range(n)]
