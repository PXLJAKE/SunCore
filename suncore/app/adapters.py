# SPDX-License-Identifier: Apache-2.0
"""Composition of the tariff and forecast adapters (Phase 4 WP4/WP5).

This module will build the price and forecast sources from the configured templates and hand
them the Home Assistant connection. It is the only module of ``suncore.app`` that may call
``HAConnection.call_action_response`` (``tests/safety/test_boundary.py``, rule 7): the call is a
read-only action with ``return_response`` and is rate limited per ``(domain, service)`` by the
connection (``ActionRateLimiter``). An adapter passes the polling interval of its template as
``min_interval_s`` and treats ``ActionRateLimitedError`` as "use the last value".

Empty on purpose until the first adapter lands (PR 4-C).
"""

from __future__ import annotations
