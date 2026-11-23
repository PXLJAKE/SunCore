# SPDX-License-Identifier: Apache-2.0
"""Keys of the recorded site measurements (Phase 4 WP3).

The time-series store (``timeseries.db``) and its readers (load forecast, accuracy
statistics, the rebuild from Home Assistant's statistics) agree on these keys. Units and
signs are the core conventions: W for power, % for the state of charge; grid + = import,
battery + = charging, device + = consumption, PV + = generation.
"""

from __future__ import annotations

from typing import Final

SITE_GRID_W: Final = "site.grid_w"
SITE_PV_W: Final = "site.pv_w"
SITE_BATTERY_W: Final = "site.battery_w"
SITE_SOC_PCT: Final = "site.soc_pct"
SITE_HOUSE_W: Final = "site.house_w"

SITE_KEYS: Final[tuple[str, ...]] = (
    SITE_GRID_W,
    SITE_PV_W,
    SITE_BATTERY_W,
    SITE_SOC_PCT,
    SITE_HOUSE_W,
)

_DEVICE_PREFIX: Final = "device."
_DEVICE_SUFFIX: Final = ".power_w"


def device_power_key(device_id: str) -> str:
    """Key of a controlled device's estimated power (W)."""
    return f"{_DEVICE_PREFIX}{device_id}{_DEVICE_SUFFIX}"


def is_percent_key(key: str) -> bool:
    """Whether the measurement is a state in percent (not a power in W)."""
    return key == SITE_SOC_PCT


__all__ = [
    "SITE_BATTERY_W",
    "SITE_GRID_W",
    "SITE_HOUSE_W",
    "SITE_KEYS",
    "SITE_PV_W",
    "SITE_SOC_PCT",
    "device_power_key",
    "is_percent_key",
]
