# SPDX-License-Identifier: Apache-2.0
"""Read-only Home Assistant actions a template may name (ADR-0012 item 7).

A tariff or forecast template can fetch a price or PV curve from the response of an
action. Only the actions listed here are accepted; ``ha`` executes them through a
separate read-only response call (WP5), never through the actuator path. A new action
needs a PR that verifies it against the integration source.
"""

from __future__ import annotations

from typing import Final

READ_ONLY_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "tibber.get_prices",
        "nordpool.get_prices_for_date",
        "solcast_solar.query_forecast_data",
        "weather.get_forecasts",
    }
)
"""``<domain>.<service>`` of the actions whose response SunCore may read."""

ERR_ACTION_NOT_ALLOWED: Final = "template.error.action_not_allowed"
"""Catalogue key of the load-time refusal for a template naming another action."""


def is_read_only_action(domain: str, service: str) -> bool:
    """Whether ``domain.service`` is on the built-in read-only allowlist."""
    return f"{domain}.{service}" in READ_ONLY_ACTIONS
