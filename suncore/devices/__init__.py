# SPDX-License-Identifier: Apache-2.0
"""Devices (Phase 3): power models (``power``) and HA entity drivers (``drivers``).

Depends on ``suncore.core`` only; the drivers get their write and read capabilities injected
(``drivers/base.py``) and are importable by ``suncore.safety`` and ``suncore.app`` only.
"""
