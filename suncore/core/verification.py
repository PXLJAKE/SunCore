# SPDX-License-Identifier: Apache-2.0
"""The inverter limit verification window (ADR-0018 addendum 2026-11-25, plan phase 7 WP8).

The verification test must curtail export below its current value while devices or the battery
could still absorb power, which invariant X3 (``safety.inverter.check_chain``) forbids. The
**only** relaxation of X3 is a :class:`VerificationWindow`: a short-lived permission the gate
holds while the test runs. Pure data and pure predicates here; the gate owns the window
(``SafetyGate.open_verification_window``) and the waiver itself is judged in
``safety.inverter.window_waives_chain``.

A window

* has an id (``[a-z0-9-]``, at most 64 characters) and binds the intents it covers by their id
  (:func:`verification_intent_id`: ``verify:<window id>:<step>``) - a confirmed controller
  proposal (``Origin.USER`` too) never matches;
* lasts at most :data:`MAX_WINDOW_S` (10 min) from ``opened_at`` - the constructor refuses a
  longer one, so no caller can open an unbounded waiver;
* allows nothing tighter than ``lowest`` (the test limit), in the same unit.

Units per ``core/units.py``: W and %.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from suncore.core.decisions import InverterLimitAction
from suncore.core.inverter import capability_of, looser

MAX_WINDOW_S: Final = 600.0
"""ADR-0018 addendum decision 2: a window expires at most 10 min after it was opened."""
VERIFY_INTENT_PREFIX: Final = "verify:"
_WINDOW_ID_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass(frozen=True, slots=True)
class VerificationWindow:
    """A bounded permission to curtail below the current export (X3) for the test's intents.

    Raises ``ValueError`` for an id outside ``[a-z0-9-]{1,64}``, naive times, an empty window
    or one longer than :data:`MAX_WINDOW_S`.
    """

    id: str
    opened_at: datetime
    expires_at: datetime
    lowest: InverterLimitAction
    """The tightest limit the window allows (the test limit), W or % like the adapter."""

    def __post_init__(self) -> None:
        if not _WINDOW_ID_RE.match(self.id):
            msg = "verification window id must match [a-z0-9][a-z0-9-]{0,63}"
            raise ValueError(msg)
        if self.opened_at.tzinfo is None or self.expires_at.tzinfo is None:
            msg = "verification window times must be timezone-aware"
            raise ValueError(msg)
        length = (self.expires_at - self.opened_at).total_seconds()
        if not 0.0 < length <= MAX_WINDOW_S:
            msg = f"verification window must last (0, {MAX_WINDOW_S:.0f}] s, not {length:.0f} s"
            raise ValueError(msg)

    def active(self, now: datetime) -> bool:
        """Open now: ``opened_at <= now < expires_at``."""
        return self.opened_at <= now < self.expires_at

    def owns(self, intent_id: str) -> bool:
        """The intent was made for this window (:func:`verification_intent_id`)."""
        return intent_id.startswith(f"{VERIFY_INTENT_PREFIX}{self.id}:")

    def allows(self, requested: InverterLimitAction) -> bool:
        """``requested`` is in the window's unit and not tighter than :attr:`lowest`."""
        return capability_of(requested) == capability_of(self.lowest) and not looser(
            self.lowest, requested
        )

    @property
    def length(self) -> timedelta:
        return self.expires_at - self.opened_at


def verification_intent_id(window_id: str, step: str) -> str:
    """The id of an intent the window covers (``verify:<window id>:<step>``)."""
    return f"{VERIFY_INTENT_PREFIX}{window_id}:{step}"


__all__ = [
    "MAX_WINDOW_S",
    "VERIFY_INTENT_PREFIX",
    "VerificationWindow",
    "verification_intent_id",
]
