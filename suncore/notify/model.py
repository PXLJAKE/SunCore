# SPDX-License-Identifier: Apache-2.0
"""Value types of the notification layer (Phase 6 WP5, ADR-0016 decision 7).

Pure data: a rule produces :class:`NotifyEvent`, the limiter judges it, the sink delivers it.
Texts are never stored here: an event carries a catalogue key and its arguments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final

from suncore.core.decisions import Reason


class Category(StrEnum):
    """Notification categories (plan WP5 table); the values are the config field names."""

    ERROR = "error"
    SAFETY = "safety"
    PROPOSAL = "proposal"
    PROGRAM = "program"
    EV_TARGET = "ev_target"
    CHEAP_WINDOW = "cheap_window"
    DAILY_SUMMARY = "daily_summary"


@dataclass(frozen=True, slots=True)
class CategoryPolicy:
    persistent: bool
    """Also a persistent notification in Home Assistant (errors and safety events)."""
    min_interval_s: float
    """Minimum spacing of two messages with the same dedupe key (0 = once per key)."""
    quiet_hours: bool
    """Push messages of this category are dropped during quiet hours."""


POLICIES: Final[dict[Category, CategoryPolicy]] = {
    Category.ERROR: CategoryPolicy(persistent=True, min_interval_s=30 * 60, quiet_hours=False),
    Category.SAFETY: CategoryPolicy(persistent=True, min_interval_s=0, quiet_hours=False),
    Category.PROPOSAL: CategoryPolicy(persistent=False, min_interval_s=0, quiet_hours=True),
    Category.PROGRAM: CategoryPolicy(persistent=False, min_interval_s=0, quiet_hours=False),
    Category.EV_TARGET: CategoryPolicy(persistent=False, min_interval_s=0, quiet_hours=True),
    Category.CHEAP_WINDOW: CategoryPolicy(persistent=False, min_interval_s=0, quiet_hours=True),
    Category.DAILY_SUMMARY: CategoryPolicy(persistent=False, min_interval_s=0, quiet_hours=False),
}
"""Defaults of the plan's table. ``min_interval_s`` 0 means: one message per dedupe key (the
key carries the proposal, the job, the window or the date)."""


@dataclass(frozen=True, slots=True)
class ProposalRef:
    """The assisted-mode proposal a notification offers to confirm."""

    intent_id: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class NotifyEvent:
    """Something worth telling the user.

    ``reason.key`` is the message family: the catalogue holds ``<key>.title`` and
    ``<key>.message``; ``reason.args`` are their placeholders.
    """

    category: Category
    key: str
    """Dedupe key: the same key is announced at most once per the category's interval."""
    reason: Reason
    ts: datetime
    proposal: ProposalRef | None = None
    quiet_hours: bool | None = None
    """Overrides the category's quiet-hours rule for this event (``None`` = category rule)."""
    resolves: tuple[str, ...] = field(default=())
    """Dedupe keys of earlier persistent notifications this event dismisses."""
    persist: bool | None = None
    """Overrides the category's persistent rule (``None`` = category rule)."""
    cap_exempt: bool = False
    """Never held back by the daily push cap (review 6-L L1: a program's "start now", the only
    start path of a notify-only device, and its "missed" message)."""


class Verdict(StrEnum):
    """What the limiter says about one push message."""

    SEND = "send"
    DUPLICATE = "duplicate"
    QUIET_HOURS = "quiet_hours"
    DAILY_CAP = "daily_cap"
    DISABLED = "disabled"
