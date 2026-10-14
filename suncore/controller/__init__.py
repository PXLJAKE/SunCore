# SPDX-License-Identifier: Apache-2.0
"""SunCore real-time controller (Phase 3 WP7, ADR-0011 "Controller").

Deterministic, time injected, depends on ``core`` only: surplus (``surplus``), levels per
control kind (``levels``), per-device hysteresis machines (``hysteresis``), priority
allocation with deficit back-off (``allocation``) and the tick (``loop``). It never
writes: intents go to the safety gate through an injected submit callable.
"""

from suncore.controller.allocation import (
    Allocator,
    DeviationEvent,
    DeviceView,
    allocate_levels,
    window_reason,
)
from suncore.controller.hysteresis import DeviceMachine, Phase
from suncore.controller.levels import ConfigQuantiser, Level, LevelTable, Quantiser, build_levels
from suncore.controller.loop import (
    Controller,
    ControllerInputs,
    ControllerLoop,
    IntentSubmitter,
    Poller,
    TickResult,
)
from suncore.controller.surplus import SiteSurplus, SurplusTracker, battery_share_available

__all__ = [
    "Allocator",
    "ConfigQuantiser",
    "Controller",
    "ControllerInputs",
    "ControllerLoop",
    "DeviationEvent",
    "DeviceMachine",
    "DeviceView",
    "IntentSubmitter",
    "Level",
    "LevelTable",
    "Phase",
    "Poller",
    "Quantiser",
    "SiteSurplus",
    "SurplusTracker",
    "TickResult",
    "allocate_levels",
    "battery_share_available",
    "build_levels",
    "window_reason",
]
