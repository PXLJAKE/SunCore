# SPDX-License-Identifier: Apache-2.0
"""Single-use action tokens of actionable notifications (ADR-0016 decision 7, plan WP5).

The ``mobile_app_notification_action`` event carries nothing that proves who fired it: any
client that may fire events on the Home Assistant bus can send any ``action`` string. The only
authentication is therefore the action id itself: ``SUNCORE_<VERB>_<token>`` with a 128-bit
random token that exists only in SunCore's memory and in the notification SunCore sent. A token

* is bound to one proposal (intent id) and dies with it (``expires_at``),
* works once (the first valid tap consumes it; a replay is reported as ``used``),
* is revoked together with its sibling (confirming kills "reject" and vice versa),
* is never logged and never persisted (a restart invalidates every open button),
* is compared through its SHA-256 digest, so a lookup does not depend on the secret's bytes.

Nothing here confirms anything: :meth:`ActionTokens.consume` only says what a tap *means*; the
caller decides (and re-checks that the proposal is still current).
"""

from __future__ import annotations

import hashlib
import re
import secrets
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

PREFIX: Final = "SUNCORE"
_ACTION_RE: Final = re.compile(r"^SUNCORE_(CONFIRM|REJECT)_([0-9a-f]{32})$")
MAX_OPEN: Final = 32
"""Open proposals with buttons at most (a proposal list is short; the oldest are dropped)."""
MAX_USED: Final = 256


class Verb(StrEnum):
    CONFIRM = "CONFIRM"
    REJECT = "REJECT"


class Refusal(StrEnum):
    NOT_OURS = "not_ours"
    """Not a SunCore action id (another integration's button): ignored silently."""
    UNKNOWN = "unknown"
    """Well-formed but no such token (guessed, forged or from before a restart)."""
    USED = "used"
    """The token (or its sibling) was already used or revoked."""
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class Consumed:
    verb: Verb
    intent_id: str


@dataclass(slots=True)
class _Entry:
    verb: Verb
    intent_id: str
    expires_at: datetime


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def action_id(verb: Verb, token: str) -> str:
    return f"{PREFIX}_{verb.value}_{token}"


class ActionTokens:
    def __init__(self, *, token_hex: Callable[[int], str] = secrets.token_hex) -> None:
        self._token_hex = token_hex
        self._open: OrderedDict[str, _Entry] = OrderedDict()
        self._used: OrderedDict[str, None] = OrderedDict()

    def issue(self, intent_id: str, expires_at: datetime) -> dict[Verb, str]:
        """Fresh action ids for one proposal (any older ids of it are revoked first)."""
        self.revoke(intent_id)
        ids: dict[Verb, str] = {}
        for verb in Verb:
            token = self._token_hex(16)
            self._open[_digest(token)] = _Entry(verb, intent_id, expires_at)
            ids[verb] = action_id(verb, token)
        while len(self._open) > MAX_OPEN * len(Verb):
            self._burn(next(iter(self._open)))
        return ids

    def consume(self, action: str, now: datetime) -> Consumed | Refusal:
        """What a tap means. The token is spent by the first call that finds it valid."""
        match = _ACTION_RE.match(action)
        if match is None:
            return Refusal.NOT_OURS
        digest = _digest(match.group(2))
        entry = self._open.get(digest)
        if entry is None:
            return Refusal.USED if digest in self._used else Refusal.UNKNOWN
        if entry.verb.value != match.group(1):
            return Refusal.UNKNOWN  # the id must be the one we issued, verb included
        if now >= entry.expires_at:
            self.revoke(entry.intent_id)
            return Refusal.EXPIRED
        self.revoke(entry.intent_id)  # single use, and the sibling dies with it
        return Consumed(entry.verb, entry.intent_id)

    def revoke(self, intent_id: str) -> None:
        for digest in [d for d, e in self._open.items() if e.intent_id == intent_id]:
            self._burn(digest)

    def prune(self, now: datetime) -> None:
        for digest in [d for d, e in self._open.items() if now >= e.expires_at]:
            self._burn(digest)

    def clear(self) -> None:
        for digest in list(self._open):
            self._burn(digest)

    def open_count(self) -> int:
        return len(self._open)

    def _burn(self, digest: str) -> None:
        self._open.pop(digest, None)
        self._used[digest] = None
        while len(self._used) > MAX_USED:
            self._used.popitem(last=False)
