# SPDX-License-Identifier: Apache-2.0
"""``energy/solar_forecast``: the forecasts of the integrations linked in the Energy dashboard.

Read from ``home-assistant/core`` ``energy/websocket_api.py`` and ``energy/types.py``
(``docs/research/ha-websocket-api.md`` "energy/solar_forecast"):

* request ``{"type": "energy/solar_forecast"}``, no fields, not admin gated;
* result ``{"<config_entry_id>": {"wh_hours": {"<ISO 8601 timestamp>": <number>}}}``; an
  integration that returned ``None`` is omitted, the result is ``{}`` when the dashboard has no
  solar source or no forecast linked.

**Selecting the entry.** The key is the *config entry id* (not the domain). The command does not
say which integration an entry belongs to, so a forecast source is bound to its entry by the
user's choice (``ForecastSourceConfig.config_entry_id``; the wizard offers the ids that
``energy/get_prefs`` lists in ``config_entry_solar_forecast``). Without a choice a source takes
the entry only if the result holds exactly one - SunCore never guesses between several.

Only this module (and the connection) touches the command: ``tests/safety/test_boundary.py``
rule 9 keeps ``energy_solar_forecast`` inside ``suncore.ha`` and ``suncore.sim``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Final

import structlog

from suncore.ha.connection import HAConnection

log = structlog.get_logger(__name__)

WH_HOURS: Final = "wh_hours"
"""Field of one entry (``SolarForecastType`` in ``energy/types.py``)."""

SolarForecast = Mapping[str, Mapping[str, float]]
"""``{config_entry_id: {iso_timestamp: Wh}}`` (a value is a finite number)."""


def parse_solar_forecast(result: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    """The ``wh_hours`` of every entry; malformed entries and non-numeric values are dropped."""
    out: dict[str, dict[str, float]] = {}
    for entry_id, body in result.items():
        hours = body.get(WH_HOURS) if isinstance(body, Mapping) else None
        if not isinstance(hours, Mapping):
            log.warning("ha.solar_forecast_entry_malformed", entry=str(entry_id)[:64])
            continue
        out[str(entry_id)] = {
            str(stamp): float(value)
            for stamp, value in hours.items()
            if isinstance(value, int | float)
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        }
    return out


async def fetch_solar_forecast(connection: HAConnection) -> SolarForecast:
    """``energy/solar_forecast`` of the connection (the one place the adapters call it)."""
    return await connection.energy_solar_forecast()


__all__ = ["WH_HOURS", "SolarForecast", "fetch_solar_forecast", "parse_solar_forecast"]
