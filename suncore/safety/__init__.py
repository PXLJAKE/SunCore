# SPDX-License-Identifier: Apache-2.0
"""SunCore safety layer: the single writer boundary (ADR-0011, plan phase-3 WP3).

``gate.SafetyGate`` is the only object that calls an actuator; ``invariants`` are pure,
property-tested rules; ``overrides``, ``watchdog``, ``ratelimit`` and ``runtime`` are its
building blocks; ``modes`` defines the injected mode provider.
"""
