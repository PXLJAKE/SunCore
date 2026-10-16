# SPDX-License-Identifier: Apache-2.0
"""What the control layer shows the outside: per-device and site state, overrides.

Pure data (Phase 3 WP9). The composition root builds a :class:`ControlState` after every
controller tick from the controller's views, the power estimates, the gate's runtime and the
mode provider; the API serves it (``GET /api/v1/control/state``) and the live feed carries it.
Nothing here decides anything.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from suncore.core.control import DeviceState, PowerEstimate
from suncore.core.decisions import Decision, OperatingMode, Reason
from suncore.core.devices import DeviceKind
from suncore.core.signals import Quality
from suncore.core.validation import invalid


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DeviceControlState(_Frozen):
    """One controlled device as the "Now" view shows it."""

    device_id: str
    name: str
    kind: DeviceKind
    identity: str
    """Opaque identity token (id, template, mapped entities). Send it back when raising the
    device's mode: a changed device (re-mapped entities) invalidates it."""
    global_mode: OperatingMode
    device_mode: OperatingMode
    effective_mode: OperatingMode
    """``min(global, device)``: what the gate actually applies."""
    state: DeviceState | None
    """Observed state from the entity cache (``None`` before the first tick)."""
    estimate: PowerEstimate | None
    """Current power with its uncertainty (``None`` before the first tick)."""
    phase: str | None
    """Controller state machine: idle, steady, pending_up, pending_down, in_flight, retry."""
    level: int | None
    """The level SunCore considers applied (0 = off)."""
    top: int | None
    """The highest level of the device."""
    expected_power_w: float | None
    """Power of the applied level as the controller expects it."""
    committed_w: float | None
    base_setpoint_c: float | None
    under_control: bool
    """SunCore has taken the device over (a takeover baseline exists)."""
    held: bool
    """A manual override or a user hold keeps the controller away."""
    held_until: AwareDatetime | None
    """End of the hold; ``null`` while ``held`` means until "resume now"."""
    release_pending: bool
    release_due: AwareDatetime | None
    paused: Reason | None
    """Why control is blocked right now (emergency stop, watchdog, connection)."""
    reasons: list[Reason]
    """The controller's reasons for the current level ("Why?")."""
    since: AwareDatetime | None
    last_decision: Decision | None
    """The newest decision the gate made for this device."""


class SiteControlState(_Frozen):
    """The smoothed site view of the latest tick (W; grid + = import)."""

    available_w: float | None
    """Smoothed power available to the controlled devices before the reserve."""
    total_w: float | None
    """Available minus the reserve."""
    free_w: float | None
    """Total minus what the controller already committed to running devices."""
    allocated_draw_w: float
    deficit_w: float
    """Import or battery discharge caused by the controlled devices."""
    uncertainty_w: float
    quality: Quality
    grid_w: float | None
    battery_w: float | None
    soc_pct: float | None


class ControlState(_Frozen):
    """Everything the control layer exposes after a tick."""

    ts: AwareDatetime | None
    """Time of the latest tick (``null`` before the first one)."""
    active: bool
    """The control loop is ticking (devices recovered, Home Assistant connected)."""
    emergency_stop: bool
    global_mode: OperatingMode
    site: SiteControlState | None
    devices: list[DeviceControlState]
    proposals: list[Decision]
    """Open assisted-mode proposals (``DecisionKind.PROPOSED`` with ``expires_at``)."""


OverrideAction = Literal["boost", "pause", "resume"]


class OverrideCommand(_Frozen):
    """A user override of one device (``POST /api/v1/devices/{id}/override``).

    * ``boost`` + ``minutes``: SunCore keeps its hands off the device for that long (the user runs
      it by hand; automatic control resumes afterwards);
    * ``pause`` (+ optional ``until``): the same hold until a given time, or until ``resume``;
    * ``resume``: SunCore may control the device again now.
    """

    action: OverrideAction
    minutes: Annotated[int, Field(ge=1, le=1440)] | None = None
    until: AwareDatetime | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.action == "boost" and self.minutes is None:
            raise invalid("override_boost_minutes", "override: boost needs `minutes`")
        if self.action != "boost" and self.minutes is not None:
            raise invalid(
                "override_minutes_only_boost", "override: `minutes` only belongs to boost"
            )
        if self.action != "pause" and self.until is not None:
            raise invalid("override_until_only_pause", "override: `until` only belongs to pause")
        return self


class ModeRequest(_Frozen):
    """``PUT /api/v1/operation/mode``: the global operating mode."""

    mode: OperatingMode


class DeviceModeRequest(_Frozen):
    """``PUT /api/v1/operation/devices/{id}``: one device's operating mode.

    Raising the mode above shadow needs ``identity``, the token the device carried in
    ``GET /api/v1/control/state`` when the user decided: a device that was re-mapped meanwhile
    answers 409 and keeps its mode.
    """

    mode: OperatingMode
    identity: Annotated[str, Field(min_length=1, max_length=64)] | None = None

    @model_validator(mode="after")
    def _identity_for_raise(self) -> Self:
        if self.mode is not OperatingMode.SHADOW and self.identity is None:
            raise invalid(
                "mode_identity_required", "mode: raising a device above shadow needs `identity`"
            )
        return self


class UnknownDeviceError(LookupError):
    """No configured device has this id."""


class UnknownProposalError(LookupError):
    """The proposal does not exist (any more): confirmed, dismissed or expired."""


class DeviceIdentityChangedError(RuntimeError):
    """The device changed since the caller read its identity token."""


class OverrideUntilPastError(ValueError):
    """A pause ``until`` a time that has already passed (judged with the app's clock)."""


def override_until_past() -> PydanticCustomError:
    """The validation error the API reports for :class:`OverrideUntilPastError` (``loc`` until)."""
    return invalid("override_until_past", "override: `until` is in the past")


class ModeRaiseRejectedError(RuntimeError):
    """A full-document ``operation`` put tried to raise a mode (only lowering is allowed)."""


__all__ = [
    "ControlState",
    "DeviceControlState",
    "DeviceIdentityChangedError",
    "DeviceModeRequest",
    "ModeRaiseRejectedError",
    "ModeRequest",
    "OverrideAction",
    "OverrideCommand",
    "OverrideUntilPastError",
    "SiteControlState",
    "UnknownDeviceError",
    "UnknownProposalError",
    "override_until_past",
]
