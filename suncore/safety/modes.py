# SPDX-License-Identifier: Apache-2.0
"""Operating modes as the gate sees them: through an injected provider only.

``safety`` never imports the configuration sections or the storage (boundary rule 6).
The composition root (``suncore.app``, Phase 3 WP9) implements :class:`ModeProvider`
on top of the stored operating modes; without a provider everything is shadow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from suncore.core.decisions import OperatingMode, effective_mode


class ModeProvider(Protocol):
    """Source of the global and the per-device operating mode."""

    def global_mode(self) -> OperatingMode: ...

    def device_mode(self, device_id: str) -> OperatingMode:
        """Mode of one device; an unknown device is shadow."""
        ...

    def battery_mode(self) -> OperatingMode:
        """Mode of the battery asset (ADR-0013); shadow unless the user raised it."""
        ...


@dataclass(frozen=True, slots=True)
class ModeView:
    """The modes that applied to one device at one moment."""

    global_mode: OperatingMode
    device_mode: OperatingMode

    @property
    def effective(self) -> OperatingMode:
        """``min(global, device)``: shadow < assisted < automatic."""
        return effective_mode(self.global_mode, self.device_mode)


class AllShadow:
    """The default provider: shadow for the system and for every device."""

    def global_mode(self) -> OperatingMode:
        return OperatingMode.SHADOW

    def device_mode(self, device_id: str) -> OperatingMode:  # noqa: ARG002 - protocol
        return OperatingMode.SHADOW

    def battery_mode(self) -> OperatingMode:
        return OperatingMode.SHADOW


class StaticModes:
    """A provider over fixed values (tests, demo): ``device_modes`` missing = shadow."""

    def __init__(
        self,
        global_mode: OperatingMode = OperatingMode.SHADOW,
        devices: dict[str, OperatingMode] | None = None,
        battery: OperatingMode = OperatingMode.SHADOW,
    ) -> None:
        self._global = global_mode
        self._devices = dict(devices or {})
        self._battery = battery

    def global_mode(self) -> OperatingMode:
        return self._global

    def device_mode(self, device_id: str) -> OperatingMode:
        return self._devices.get(device_id, OperatingMode.SHADOW)

    def set_global(self, mode: OperatingMode) -> None:
        self._global = mode

    def set_device(self, device_id: str, mode: OperatingMode) -> None:
        self._devices[device_id] = mode

    def battery_mode(self) -> OperatingMode:
        return self._battery

    def set_battery(self, mode: OperatingMode) -> None:
        self._battery = mode


def view(provider: ModeProvider, device_id: str) -> ModeView:
    return ModeView(provider.global_mode(), provider.device_mode(device_id))


__all__ = ["AllShadow", "ModeProvider", "ModeView", "StaticModes", "view"]
