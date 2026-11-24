# SPDX-License-Identifier: Apache-2.0
"""Thermal model and COP curve of a heat pump or other thermal store (ADR-0017 decision 6).

Pure data and arithmetic, no I/O; imports nothing from sibling modules but the validation
helper (plan §WP1 boundary rule). The model is a first-order RC store::

    C · dT/dt = P_heat + P_gain - UA · (T - T_out)

with ``C = capacity_wh_per_k`` (Wh/K), ``UA = loss_w_per_k`` (W/K), ``P_heat`` the heat put in
(electric power x COP) and ``P_gain`` a constant internal gain. :meth:`ThermalModel.step` uses
the exact solution for a constant input over the step, so it is stable for any step length.
The parameters are learned from history (``suncore/forecast/thermal.py``, PR 6-K) and used by
the planner only when :func:`model_usable` (plan §0 decision 10).
"""

from __future__ import annotations

import itertools
import math
from typing import Annotated, Final, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from suncore.core.validation import invalid

MODEL_MAX_NRMSE: Final = 0.2
"""A learned model is used when the normalised RMSE of a 24 h replay is at most this."""
MODEL_MIN_HISTORY_DAYS: Final = 14
"""At least this much 15-minute history before a model is fitted."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CopPoint(_Frozen):
    """The coefficient of performance at one outdoor temperature."""

    outdoor_c: float = Field(ge=-40, le=50, allow_inf_nan=False)
    cop: float = Field(gt=0, le=10, allow_inf_nan=False)


class CopCurve(_Frozen):
    """COP over outdoor temperature: 2-8 points, piecewise linear, clamped at the ends.

    ``assumed`` marks a curve that is not from a vendor source (the generic default); the UI
    labels it as an assumption.
    """

    points: Annotated[tuple[CopPoint, ...], Field(min_length=2, max_length=8)]
    assumed: bool = False

    @model_validator(mode="after")
    def _increasing(self) -> Self:
        temps = [p.outdoor_c for p in self.points]
        if any(a >= b for a, b in itertools.pairwise(temps)):
            raise invalid(
                "cop_curve_order", "cop curve: outdoor temperatures must be strictly increasing"
            )
        return self

    def cop_at(self, outdoor_c: float) -> float:
        """The COP at ``outdoor_c`` (linear between points, the end value outside)."""
        pts = self.points
        if outdoor_c <= pts[0].outdoor_c:
            return pts[0].cop
        if outdoor_c >= pts[-1].outdoor_c:
            return pts[-1].cop
        for a, b in itertools.pairwise(pts):
            if a.outdoor_c <= outdoor_c <= b.outdoor_c:
                frac = (outdoor_c - a.outdoor_c) / (b.outdoor_c - a.outdoor_c)
                return a.cop + frac * (b.cop - a.cop)
        return pts[-1].cop  # pragma: no cover - the loop covers the interior


GENERIC_AIR_WATER_COP: Final = CopCurve(
    points=(
        CopPoint(outdoor_c=-7, cop=2.5),
        CopPoint(outdoor_c=2, cop=3.0),
        CopPoint(outdoor_c=7, cop=3.6),
        CopPoint(outdoor_c=15, cop=4.3),
    ),
    assumed=True,
)
"""Plan §0 decision 10: the generic air/water assumption when the template has no curve."""


class ThermalModel(_Frozen):
    """First-order RC parameters of a thermal store."""

    capacity_wh_per_k: float = Field(gt=0, le=1_000_000, allow_inf_nan=False)
    loss_w_per_k: float = Field(ge=0, le=100_000, allow_inf_nan=False)
    internal_gain_w: float = Field(default=0.0, ge=-100_000, le=100_000, allow_inf_nan=False)

    def step(self, t_c: float, outdoor_c: float, heat_w: float, dt_s: float) -> float:
        """The temperature after ``dt_s`` seconds of constant ``heat_w`` (thermal W)."""
        if dt_s < 0:
            msg = "dt_s must not be negative"
            raise ValueError(msg)
        power = heat_w + self.internal_gain_w
        hours = dt_s / 3600.0
        if self.loss_w_per_k == 0:
            return t_c + power * hours / self.capacity_wh_per_k
        t_eq = outdoor_c + power / self.loss_w_per_k
        decay = math.exp(-self.loss_w_per_k * hours / self.capacity_wh_per_k)
        return t_eq + (t_c - t_eq) * decay


class LearnedThermal(_Frozen):
    """A fitted model with its quality (``nrmse`` of a 24 h replay) and history length."""

    model: ThermalModel
    nrmse: float = Field(ge=0, le=100, allow_inf_nan=False)
    history_days: int = Field(ge=0, le=10_000)
    fitted_at: AwareDatetime


def model_usable(learned: LearnedThermal | None, max_nrmse: float = MODEL_MAX_NRMSE) -> bool:
    """Whether the planner may use the learned model (else the device is energy-only)."""
    return (
        learned is not None
        and learned.history_days >= MODEL_MIN_HISTORY_DAYS
        and learned.nrmse <= max_nrmse
    )


__all__ = [
    "GENERIC_AIR_WATER_COP",
    "MODEL_MAX_NRMSE",
    "MODEL_MIN_HISTORY_DAYS",
    "CopCurve",
    "CopPoint",
    "LearnedThermal",
    "ThermalModel",
    "model_usable",
]
