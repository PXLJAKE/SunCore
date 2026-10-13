# SPDX-License-Identifier: Apache-2.0
"""Decision contracts shared by planner, controller, safety and the decision log.

ADR-0011: the controller issues :class:`Intent` objects, the safety gate
turns each one into a :class:`Decision`. Both are immutable, typed and
JSON-serialisable so they can be appended to the decision log and rendered
by the UI's "Why?" view. Explanations are :class:`Reason` keys resolved
through the ICU catalogue - never free text.

The ``Plan`` contract is deliberately absent; it is defined in Phase 3/4.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum, unique
from typing import Annotated, Final, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

ReasonArg = str | int | float | bool
"""Scalar placeholder values a catalogue message may interpolate."""

REASON_KEY_PATTERN: Final = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
"""Dotted lowercase catalogue key, e.g. ``safety.shadow_mode``."""

_REASON_KEY_RE: Final = re.compile(REASON_KEY_PATTERN)


class _FrozenModel(BaseModel):
    """Base for every contract: immutable, no unknown fields, strict enums."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_enum_values=False)


class Reason(_FrozenModel):
    """A structured, translatable explanation.

    ``key`` addresses a message in the ICU catalogue; ``args`` are the
    placeholders of that message. Free text is not allowed anywhere.
    """

    key: Annotated[str, Field(pattern=REASON_KEY_PATTERN)]
    args: Mapping[str, ReasonArg] = Field(default_factory=dict)


def is_reason_key(value: str) -> bool:
    """Return whether ``value`` is a well-formed reason key."""
    return _REASON_KEY_RE.match(value) is not None


@unique
class OperatingMode(StrEnum):
    """Operating mode per device and globally (BRIEF §4.11)."""

    SHADOW = "shadow"
    """Compute, show and log "would have switched"; never write."""
    ASSISTED = "assisted"
    """Propose; the user confirms before anything is written."""
    AUTOMATIC = "automatic"
    """Write autonomously within all invariants."""


@unique
class Origin(StrEnum):
    """The layer that produced an intent (ADR-0011)."""

    PLANNER = "planner"
    CONTROLLER = "controller"
    USER = "user"


@unique
class ActionKind(StrEnum):
    """Discriminator for the typed action targets."""

    SWITCH = "switch"
    POWER_SETPOINT = "power_setpoint"


class SwitchAction(_FrozenModel):
    """Turn a device on or off."""

    kind: Literal[ActionKind.SWITCH] = ActionKind.SWITCH
    on: bool


class PowerSetpointAction(_FrozenModel):
    """Request a power set-point in W (device + = consumption, battery + = charging)."""

    kind: Literal[ActionKind.POWER_SETPOINT] = ActionKind.POWER_SETPOINT
    power_w: float = Field(allow_inf_nan=False)


Action = Annotated[SwitchAction | PowerSetpointAction, Field(discriminator="kind")]
"""Typed action target; the ``kind`` field selects the variant."""


class Intent(_FrozenModel):
    """What a control layer wants to do to one asset, and why."""

    id: Annotated[str, Field(min_length=1)]
    ts: AwareDatetime
    asset_id: Annotated[str, Field(min_length=1)]
    action: Action
    reasons: tuple[Reason, ...] = ()
    origin: Origin


@unique
class DecisionKind(StrEnum):
    """Outcome of the safety gate for one intent."""

    ACCEPTED = "accepted"
    """Validated and handed to the actuator (automatic mode only)."""
    REJECTED = "rejected"
    """An invariant or the emergency stop fired; nothing was written."""
    SHADOW = "shadow"
    """Logged as "would have done"; nothing was written."""


class Decision(_FrozenModel):
    """The safety gate's verdict on an intent, as stored in the decision log."""

    intent: Intent
    kind: DecisionKind
    ts: AwareDatetime
    reasons: tuple[Reason, ...] = ()
    mode: OperatingMode

    @property
    def wrote_hardware(self) -> bool:
        """True only for decisions that may have reached an actuator."""
        return self.kind is DecisionKind.ACCEPTED


__all__ = [
    "REASON_KEY_PATTERN",
    "Action",
    "ActionKind",
    "Decision",
    "DecisionKind",
    "Intent",
    "OperatingMode",
    "Origin",
    "PowerSetpointAction",
    "Reason",
    "ReasonArg",
    "SwitchAction",
    "is_reason_key",
]
