# SPDX-License-Identifier: Apache-2.0
"""Live COP and model quality of a heat pump for the control state (PR 7-I).

Pure arithmetic on what the runtime already holds: the device's COP curve (or the generic
air/water assumption), the current outdoor temperature and the learned RC model. Nothing here
reads Home Assistant or writes anything.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta

from suncore.core.controlstate import HeatPumpLive, ThermalModelQuality
from suncore.core.devices import DeviceConfig
from suncore.core.series import TimeSeries
from suncore.core.thermal import (
    GENERIC_AIR_WATER_COP,
    LearnedThermal,
    model_usable,
)


def outdoor_now(series: TimeSeries | None, now: datetime) -> float | None:
    """The outdoor temperature of the hour that contains ``now`` (``None`` if not covered).

    Many weather integrations (and the demo) start the hourly forecast at the next full hour,
    so a ``now`` less than one step before the series start takes the first value.
    """
    if series is None or not series.values:
        return None
    offset = now - series.start
    if -series.step < offset < timedelta(0):
        return series.values[0]
    index = int(offset / series.step)
    if index < 0 or index >= len(series.values):
        return None
    return series.values[index]


def heat_pump_live(
    device: DeviceConfig,
    learned: Mapping[str, LearnedThermal],
    outdoor_c: float | None,
) -> HeatPumpLive | None:
    """The live thermal values of ``device``; ``None`` for a device without ``thermal``.

    ``learned`` is the learner's fits (a fit stored in the configuration is the fallback). The
    curve is the device's own or :data:`GENERIC_AIR_WATER_COP`; ``cop_assumed`` follows the
    curve's ``assumed`` flag, so a template or user curve is not labelled an assumption.
    """
    spec = device.thermal
    if spec is None:
        return None
    curve = spec.cop if spec.cop is not None else GENERIC_AIR_WATER_COP
    fit = learned.get(device.id) or spec.learned
    return HeatPumpLive(
        outdoor_c=outdoor_c,
        cop=None if outdoor_c is None else round(curve.cop_at(outdoor_c), 2),
        cop_assumed=curve.assumed,
        model=None
        if fit is None
        else ThermalModelQuality(
            fit_error=fit.nrmse,
            history_days=fit.history_days,
            fitted_at=fit.fitted_at,
            used_by_planner=model_usable(fit),
        ),
    )
