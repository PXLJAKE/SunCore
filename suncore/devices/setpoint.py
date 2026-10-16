# SPDX-License-Identifier: Apache-2.0
"""Set-point rounding: re-export of :mod:`suncore.core.setpoint`.

The function moved to ``core`` in Phase 3 WP7 so that the controller (which may import
``core`` only) computes the same targets the climate driver writes.
"""

from suncore.core.setpoint import snap_setpoint

__all__ = ["snap_setpoint"]
