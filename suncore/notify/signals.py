# SPDX-License-Identifier: Apache-2.0
"""Site signals: the notification events as MQTT event types (Phase 7 WP6).

A signal is what the ``events`` entity of Home Assistant gets: a type from
``suncore.ha.mqtt.catalogue.EVENT_TYPES`` and a few plain attributes. It is derived from the same
:class:`~suncore.notify.model.NotifyEvent` that drives the push message (one source of truth), but
it is independent of the push: the category switch, the quiet hours and the daily cap only govern
pushes. Pure data and a pure mapping; this module does not import ``suncore.ha``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from suncore.notify.model import NotifyEvent

CHEAP_WINDOW_END: Final = "cheap_window_end"

_BY_TEXT_KEY: Final[Mapping[str, str]] = {
    "notify.safety.island_start": "island_start",
    "notify.safety.island_end": "island_end",
    "notify.safety.para14a_start": "para14a_start",
    "notify.safety.para14a_end": "para14a_end",
    "notify.cheap_window.start": "cheap_window_start",
    "notify.program.start_now": "program_start_now",
    "notify.program.done": "program_done",
    "notify.ev_target.at_risk": "ev_target_at_risk",
    "notify.ev_target.at_risk_short": "ev_target_at_risk",
}
"""Notification text key -> event type. ``missed`` and ``unknown`` of a program have no event type
(the plan's list names only start and done), so they stay push-only."""

_ATTRS: Final[Mapping[str, tuple[str, ...]]] = {
    "cheap_window_start": ("start", "end", "price"),
    "program_start_now": ("device", "deadline"),
    "program_done": ("device",),
    "ev_target_at_risk": ("device", "time", "need_kwh", "planned_kwh"),
}
"""The reason arguments that are copied into the event (the others carry nothing a HA automation
needs)."""


@dataclass(frozen=True, slots=True)
class SiteSignal:
    """One entry for the MQTT ``events`` entity."""

    type: str
    attrs: Mapping[str, str | int | float | bool] = field(default_factory=dict)


def signal_for(event: NotifyEvent) -> SiteSignal | None:
    """The signal of ``event`` (``None`` = this notification has no event type)."""
    kind = _BY_TEXT_KEY.get(event.reason.key)
    if kind is None:
        return None
    wanted = _ATTRS.get(kind, ())
    attrs = {k: v for k, v in event.reason.args.items() if k in wanted}
    if "device" in attrs:  # the reason carries the device's display name, not its id
        attrs["name"] = attrs.pop("device")
    return SiteSignal(kind, attrs)


__all__ = ["CHEAP_WINDOW_END", "SiteSignal", "signal_for"]
