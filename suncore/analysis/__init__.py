# SPDX-License-Identifier: Apache-2.0
"""Analytics core (Phase 5 WP2, ADR-0014): energy, flows, KPIs, baselines, cost, periods.

Pure functions over quarter-hour energy. Depends on ``suncore.core`` and the standard library
only. Units and signs per ``core/units.py``: Wh, currency/kWh; grid + = import, battery + =
charging, PV + = generation, house + = consumption.
"""
