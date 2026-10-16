# SPDX-License-Identifier: Apache-2.0
"""The gate's :class:`~suncore.safety.modes.ModeProvider`, backed by the ``operation`` section.

``safety`` never reads the configuration (boundary rule 6): the runtime keeps the stored
:class:`~suncore.core.config.OperationConfig` here and the gate asks this provider. A change of
the section is pushed with :meth:`ConfigModes.update`, after which the runtime calls
``gate.sync_modes()`` so that devices that fell back to shadow are released.
"""

from __future__ import annotations

from suncore.core.config import OperationConfig
from suncore.core.decisions import OperatingMode


class ConfigModes:
    """Global and per-device mode from the stored operation document (missing = shadow)."""

    def __init__(self, operation: OperationConfig | None = None) -> None:
        self._operation = operation if operation is not None else OperationConfig()

    @property
    def operation(self) -> OperationConfig:
        return self._operation

    def update(self, operation: OperationConfig | None) -> None:
        """Replace the document (``None`` = never stored = everything shadow)."""
        self._operation = operation if operation is not None else OperationConfig()

    def global_mode(self) -> OperatingMode:
        return self._operation.mode

    def device_mode(self, device_id: str) -> OperatingMode:
        return self._operation.device_modes.get(device_id, OperatingMode.SHADOW)


__all__ = ["ConfigModes"]
