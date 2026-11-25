# SPDX-License-Identifier: Apache-2.0
"""Decision contracts shared by planner, controller, safety and the decision log.

ADR-0011: the controller issues :class:`Intent` objects, the safety gate
turns each one into a :class:`Decision`. Both are immutable, typed and
JSON-serialisable so they can be appended to the decision log and rendered
by the UI's "Why?" view. Explanations are :class:`Reason` keys resolved
through the ICU catalogue - never free text.

Schema v2 (Phase 3, plan WP2): the action union gained the set-point, step,
mode and release variants, ``Decision`` carries ``schema_version`` and the
kinds ``proposed``, ``held`` and ``failed``. Stored v1 rows (no
``schema_version``) still load through :func:`decision_from_payload`.
The ``Plan`` contract lives in :mod:`suncore.core.control`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from enum import StrEnum, unique
from typing import Annotated, Any, Final, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from suncore.core.validation import invalid

ReasonArg = str | int | float | bool
"""Scalar placeholder values a catalogue message may interpolate."""

REASON_KEY_PATTERN: Final = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
"""Dotted lowercase catalogue key, e.g. ``safety.shadow_mode``."""

_REASON_KEY_RE: Final = re.compile(REASON_KEY_PATTERN)

DECISION_SCHEMA_VERSION: Final = 2
"""Version written into every new :class:`Decision`.

Adding a new action kind (Phase 4: :class:`BatteryAction`) is an *additive, non-bumping*
change: every stored v2 row stays valid as it is, and an older build that meets a row with
an unknown ``kind`` fails to parse it and skips it safely (the decision log reader logs
``decision_log.row_skipped``). The version is bumped only when an existing field changes
meaning or a stored row would need an upgrade step."""


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

    @property
    def rank(self) -> int:
        """Ordering used by :func:`effective_mode`: shadow < assisted < automatic."""
        return _MODE_RANK[self]


_MODE_RANK: Final[dict[OperatingMode, int]] = {
    OperatingMode.SHADOW: 0,
    OperatingMode.ASSISTED: 1,
    OperatingMode.AUTOMATIC: 2,
}


def effective_mode(global_mode: OperatingMode, device_mode: OperatingMode) -> OperatingMode:
    """The mode that applies to a device: the more conservative of global and device.

    ``min(global, device)`` in the order shadow < assisted < automatic, so a device can
    never be more autonomous than the system and the system can never force a device
    beyond what the user enabled for it.
    """
    return global_mode if global_mode.rank <= device_mode.rank else device_mode


@unique
class Origin(StrEnum):
    """The layer that produced an intent (ADR-0011)."""

    PLANNER = "planner"
    CONTROLLER = "controller"
    USER = "user"
    SAFETY = "safety"
    """Release intents the gate synthesises (shutdown, watchdog, crash recovery)."""


@unique
class ActionKind(StrEnum):
    """Discriminator for the typed action targets."""

    SWITCH = "switch"
    POWER_SETPOINT = "power_setpoint"
    SETPOINT = "setpoint"
    STEP = "step"
    MODE = "mode"
    RELEASE = "release"
    BATTERY = "battery"
    # Phase 6 (PR 6-B, additive; rejected by the gate until their invariants exist):
    CHARGE = "charge"
    LOADPOINT = "loadpoint"
    INVERTER_LIMIT = "inverter_limit"


NOT_YET_SUPPORTED_KINDS: Final[frozenset[ActionKind]] = frozenset()
"""Action kinds that are in the contract but not judged by any invariant yet (ADR-0011
addendum): the safety gate rejects each with ``safety.<kind>_not_supported`` before mode
handling. A kind leaves this set only in the PR that adds its invariants, scenarios and the
``control-safety`` sign-off. Empty since Phase 6: ``charge`` left in 6-I (E1-E9,
``safety.flex``), ``loadpoint`` in 6-J (L1-L5, ``safety.evcc``), ``inverter_limit`` in 6-G
(X1-X6, ``safety.inverter``)."""


class SwitchAction(_FrozenModel):
    """Turn a device on or off.

    For a climate device "on" means the configured ``switch_on_hvac_mode`` and "off"
    means HVAC mode ``off``; a switch-on needs the device's ``allow_switch_on`` opt-in
    (checked by the safety invariants).
    """

    kind: Literal[ActionKind.SWITCH] = ActionKind.SWITCH
    on: bool


class PowerSetpointAction(_FrozenModel):
    """Request a power set-point in W (device + = consumption, battery + = charging)."""

    kind: Literal[ActionKind.POWER_SETPOINT] = ActionKind.POWER_SETPOINT
    power_w: float = Field(allow_inf_nan=False)


class SetpointAction(_FrozenModel):
    """Write an absolute temperature set-point in °C.

    ``base_c`` is the user's own set-point the controller shifts from; it is recorded so
    the decision log and the release path know what "back to normal" means. A shift of
    zero (``setpoint_c == base_c``) restores the user's value.
    """

    kind: Literal[ActionKind.SETPOINT] = ActionKind.SETPOINT
    setpoint_c: float = Field(allow_inf_nan=False, ge=-50, le=150)
    base_c: float = Field(allow_inf_nan=False, ge=-50, le=150)

    @property
    def shift_c(self) -> float:
        return self.setpoint_c - self.base_c


class StepAction(_FrozenModel):
    """Select step ``step`` (index into the device's ``control.steps``; 0 = lowest)."""

    kind: Literal[ActionKind.STEP] = ActionKind.STEP
    step: Annotated[int, Field(ge=0, le=15)]


class ModeAction(_FrozenModel):
    """Select a mode value (select option, climate HVAC mode or preset)."""

    kind: Literal[ActionKind.MODE] = ActionKind.MODE
    mode: Annotated[str, Field(min_length=1, max_length=64)]


class ReleaseAction(_FrozenModel):
    """Give the device back: restore the takeover baseline and stop controlling it."""

    kind: Literal[ActionKind.RELEASE] = ActionKind.RELEASE


class BatteryAction(_FrozenModel):
    """Battery limits for the gate (Phase 4 WP2; semantics ADR-0013, decisions 1 and 2).

    **Full desired state, not a delta.** An accepted ``BatteryAction`` is the complete state
    SunCore wants the battery adapter to hold; a field that is ``None`` means "no SunCore
    restriction on this limit", i.e. the adapter's **baseline** value of that field (what the
    gate read back when it took the battery over). The gate resolves the ``None`` fields from
    the baseline before it judges, writes and reads back (``suncore.core.battery.
    resolve_battery_action``); the heartbeat re-sends the resolved state idempotently. At least
    one field must be set: "everything at baseline" is a :class:`ReleaseAction`, never an empty
    ``BatteryAction``.

    Powers are magnitudes in W (battery + = charging, so ``grid_charge_w`` is a charging power
    drawn from the grid): ``max_charge_w`` / ``max_discharge_w`` cap the inverter's own
    self-consumption control (they never command a power), ``min_soc_pct`` is the discharge
    floor (a discharge lock when at or above the current SoC), ``grid_charge_w > 0`` asks for
    forced charging from the grid. ``grid_charge_w`` never exceeds ``max_charge_w`` when both
    are set (forced charging cannot be above the charge cap, and a cap of 0 forbids it). Every
    field needs the matching adapter capability (``BatteryCapability``); the gate rejects a
    field the adapter cannot execute (``safety.battery_capability_missing``).
    """

    kind: Literal[ActionKind.BATTERY] = ActionKind.BATTERY
    max_charge_w: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)
    max_discharge_w: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)
    min_soc_pct: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    grid_charge_w: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if (
            self.max_charge_w is None
            and self.max_discharge_w is None
            and self.min_soc_pct is None
            and self.grid_charge_w is None
        ):
            raise invalid("battery_action_empty", "battery action: at least one limit must be set")
        if (
            self.grid_charge_w is not None
            and self.max_charge_w is not None
            and self.grid_charge_w > self.max_charge_w
        ):
            raise invalid(
                "grid_charge_above_cap",
                "battery action: grid_charge_w ({grid_charge_w}) above max_charge_w "
                "({max_charge_w})",
                grid_charge_w=self.grid_charge_w,
                max_charge_w=self.max_charge_w,
            )
        return self


class ChargeAction(_FrozenModel):
    """Desired state of an EV charger (ADR-0017 decision 1; judged since PR 6-I, E1-E9).

    **Full desired state.** ``current_a`` 0 = paused (the car is not charged: the driver turns
    the ``enable`` switch off, or writes 0 A where the current entity accepts it), otherwise
    within the charger's ``[min_current_a, max_current_a]`` - the range check needs the device
    and is invariant E1 of the gate, not of this contract. ``phases`` ``None`` = leave the
    phases as they are. Release = the baseline captured at takeover (current, phases, enable).

    ``enabled`` (PR 6-I, additive) is the charger's ``enable`` switch as a driver *reads it
    back*: a baseline keeps the user's current setting **and** their switch, so a release
    restores both (E9). ``None`` = derived from the current (enabled iff ``current_a > 0``);
    the controller never sets it. :meth:`effective_current_a` is what the car may draw.
    """

    kind: Literal[ActionKind.CHARGE] = ActionKind.CHARGE
    current_a: float = Field(ge=0, le=500, allow_inf_nan=False)
    phases: Literal[1, 3] | None = None
    enabled: bool | None = None

    @property
    def effective_current_a(self) -> float:
        """The current the car may draw: 0 while the enable switch is off."""
        return 0.0 if self.enabled is False else self.current_a


LOADPOINT_CURRENT_TOLERANCE_A: Final = 0.05
"""Two ``maxCurrent`` values closer than this are the same (evcc rounds to three decimals)."""

EVCC_MODES: Final = ("off", "smart", "now")
"""evcc loadpoint ``mode`` values (evcc MQTT API, read 2026-11-24; ``pv``/``minpv`` are gone)."""


class LoadpointAction(_FrozenModel):
    """What SunCore asks of an evcc loadpoint (ADR-0017 decision 2; judged since PR 6-J).

    evcc regulates current and phases itself; SunCore sets the mode, the limit SoC, a vehicle
    plan (``plan_soc_pct`` and ``plan_time`` together or neither) and a maximum current. ``None``
    fields are left as evcc has them. SunCore never writes ``evcc/site/*``.
    """

    kind: Literal[ActionKind.LOADPOINT] = ActionKind.LOADPOINT
    mode: Literal["off", "smart", "now"]
    limit_soc_pct: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    plan_soc_pct: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    plan_time: AwareDatetime | None = None
    max_current_a: float | None = Field(default=None, gt=0, le=500, allow_inf_nan=False)

    @model_validator(mode="after")
    def _plan_pair(self) -> Self:
        if (self.plan_soc_pct is None) != (self.plan_time is None):
            raise invalid(
                "loadpoint_plan_pair",
                "loadpoint action: `plan_soc_pct` and `plan_time` are set together",
            )
        return self

    def matches(self, observed: LoadpointAction) -> bool:
        """Read-back: ``observed`` shows the mode and every value this action sets."""
        if self.mode != observed.mode:
            return False
        if self.max_current_a is not None and (
            observed.max_current_a is None
            or abs(self.max_current_a - observed.max_current_a) > LOADPOINT_CURRENT_TOLERANCE_A
        ):
            return False
        return self.limit_soc_pct is None or (
            observed.limit_soc_pct is not None
            and round(self.limit_soc_pct) == round(observed.limit_soc_pct)
        )


class InverterLimitAction(_FrozenModel):
    """An export or AC output limit for the inverter (ADR-0018 decision 3; PR 6-G).

    Exactly one of ``max_export_w`` (capability ``export_limit``) and ``max_ac_pct``
    (capability ``ac_limit``, % of the nominal AC power) is set. The asset id is ``inverter``.
    Release = the baseline captured at takeover (100 % for a volatile register).
    """

    kind: Literal[ActionKind.INVERTER_LIMIT] = ActionKind.INVERTER_LIMIT
    max_export_w: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)
    max_ac_pct: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.max_export_w is None) == (self.max_ac_pct is None):
            raise invalid(
                "inverter_limit_one",
                "inverter limit: set exactly one of `max_export_w` and `max_ac_pct`",
            )
        return self


Action = Annotated[
    SwitchAction
    | PowerSetpointAction
    | SetpointAction
    | StepAction
    | ModeAction
    | ReleaseAction
    | BatteryAction
    | ChargeAction
    | LoadpointAction
    | InverterLimitAction,
    Field(discriminator="kind"),
]
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
    """Validated, written and confirmed by the read-back (automatic mode only)."""
    REJECTED = "rejected"
    """An invariant, a watchdog or the emergency stop fired; nothing was written."""
    SHADOW = "shadow"
    """Logged as "would have done"; nothing was written."""
    PROPOSED = "proposed"
    """Assisted mode: awaiting the user's confirmation until ``expires_at``."""
    HELD = "held"
    """A manual override is respected until ``held_until``; nothing was written."""
    FAILED = "failed"
    """The write or the read-back failed; the device may be in an unknown state."""


class Decision(_FrozenModel):
    """The safety gate's verdict on an intent, as stored in the decision log."""

    schema_version: Annotated[int, Field(ge=1)] = DECISION_SCHEMA_VERSION
    intent: Intent
    kind: DecisionKind
    ts: AwareDatetime
    reasons: tuple[Reason, ...] = ()
    mode: OperatingMode
    """Effective mode of the asset when the decision was made."""
    expires_at: AwareDatetime | None = None
    """``proposed`` only: the proposal is void after this time."""
    held_until: AwareDatetime | None = None
    """``held`` only: SunCore takes over again at this time (``None`` = until resumed)."""
    observed: Action | None = None
    """``accepted`` / ``failed``: what the read-back saw after the settle time."""

    @property
    def wrote_hardware(self) -> bool:
        """True for decisions that reached an actuator (whether or not it succeeded)."""
        return self.kind in (DecisionKind.ACCEPTED, DecisionKind.FAILED)


def decision_from_payload(payload: Mapping[str, Any]) -> Decision:
    """Load a stored decision of any known schema version.

    v1 rows (Phase 1, no ``schema_version``) are upgraded in place: every v1 field exists
    unchanged in v2, so the upgrade only stamps the version. A payload newer than
    :data:`DECISION_SCHEMA_VERSION` raises ``ValueError`` (the caller skips the row).
    """
    data = dict(payload)
    version = data.get("schema_version", 1)
    if not isinstance(version, int) or version < 1:
        msg = f"decision payload has an invalid schema_version {version!r}"
        raise ValueError(msg)
    if version > DECISION_SCHEMA_VERSION:
        msg = f"decision payload schema_version {version} is newer than {DECISION_SCHEMA_VERSION}"
        raise ValueError(msg)
    data["schema_version"] = DECISION_SCHEMA_VERSION
    return Decision.model_validate(data)


def decision_from_json(text: str) -> Decision:
    """:func:`decision_from_payload` for a JSON string."""
    data = json.loads(text)
    if not isinstance(data, dict):
        msg = "decision payload must be a JSON object"
        raise ValueError(msg)
    return decision_from_payload(data)


__all__ = [
    "DECISION_SCHEMA_VERSION",
    "EVCC_MODES",
    "NOT_YET_SUPPORTED_KINDS",
    "REASON_KEY_PATTERN",
    "Action",
    "ActionKind",
    "BatteryAction",
    "ChargeAction",
    "Decision",
    "DecisionKind",
    "Intent",
    "InverterLimitAction",
    "LoadpointAction",
    "ModeAction",
    "OperatingMode",
    "Origin",
    "PowerSetpointAction",
    "Reason",
    "ReasonArg",
    "ReleaseAction",
    "SetpointAction",
    "StepAction",
    "SwitchAction",
    "decision_from_json",
    "decision_from_payload",
    "effective_mode",
    "is_reason_key",
]
