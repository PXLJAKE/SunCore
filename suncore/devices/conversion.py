# SPDX-License-Identifier: Apache-2.0
"""Power <-> control value conversion: re-export of :mod:`suncore.core.conversion`.

Moved to ``core`` in Phase 3 WP7 so the controller (core-only dependency rule) uses the
same conversion as the number driver and the power models.
"""

from suncore.core.conversion import power_to_value, value_to_power_w

__all__ = ["power_to_value", "value_to_power_w"]
