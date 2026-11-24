# SPDX-License-Identifier: Apache-2.0
"""SG-Ready and heat-pump rules shared by the controller, the gate and the driver (PR 6-K).

Pure functions, no I/O (ADR-0017 decision 5, plan WP11):

* the step <-> state <-> contact mapping of :class:`~suncore.core.devices.SgReadyControl`
  (step 0 = SG state 2 normal, 1 = state 3 intensified, 2 = state 4 forced; **state 1, the
  utility block, has no step and is never written**);
* :func:`contact_path`: the order in which the two contacts are switched so that no
  intermediate contact pair is anything but a writable state - a change of both contacts must
  never pass through the block pattern (or any pattern the template does not name);
* :func:`heat_pump_failsafe`: why a heat pump must go back to SG state 2 (or its base
  set-point) **now**, whatever its dwell, minimum run and switch limit say - an unknown or stale
  store temperature, the store at ``comfort.max_c``, §14a dimming of a flagged heat pump, island
  operation. State 2 hands control back to the heat pump's own controller, which keeps its own
  compressor protection; it never forces the compressor off.
"""

from __future__ import annotations

from enum import StrEnum, unique
from typing import Final

from suncore.core.control import TEMPERATURE_ROLE, DeviceState, heat_capped
from suncore.core.devices import (
    DeviceConfig,
    DeviceKind,
    SetpointControl,
    SgContacts,
    SgReadyControl,
)

SG_NORMAL: Final = 0
"""Step of SG state 2 (normal operation): the baseline and the release."""
SG_INTENSIFIED: Final = 1
"""Step of SG state 3 (intensified operation, a recommendation to run more)."""
SG_FORCED: Final = 2
"""Step of SG state 4 (forced on, a definitive command): only with ``allow_forced``."""
SG_RESTART_K: Final = 3.0
"""After the store reached ``comfort.max_c`` SunCore intensifies again only once it has cooled
this far below it, so the contacts do not chatter at the comfort maximum (design default,
``UNVERIFIED`` against a manufacturer rule)."""


def sg_state(step: int) -> int:
    """The SG-Ready state number (2, 3, 4) of a step."""
    return step + 2


def sg_contacts_label(contacts: SgContacts) -> str:
    """A compact label of a contact pair (``"sg:10"`` = A closed, B open), at most 64 chars."""
    return f"sg:{int(contacts.a)}{int(contacts.b)}"


def sg_step_of(control: SgReadyControl, contacts: SgContacts) -> int | None:
    """The writable step the contacts show, ``None`` for any other pair (block, unmapped, or
    state 4 while ``allow_forced`` is off)."""
    for index, pair in enumerate(control.steps):
        if pair == contacts:
            return index
    return None


def contact_path(
    control: SgReadyControl, current: SgContacts, target: SgContacts
) -> tuple[SgContacts, ...] | None:
    """The contact pairs to pass from ``current`` to ``target``, one contact at a time.

    Empty when nothing changes; one pair when one contact flips; two pairs when both flip,
    through the intermediate pair that is a writable state of ``control`` (preferring the lower
    step). ``None`` when both contacts flip and neither intermediate pair is writable: the
    change could pass through the utility block (state 1) or an unknown pattern, so it is
    refused (H1).
    """
    if current == target:
        return ()
    if current.a == target.a or current.b == target.b:
        return (target,)
    writable = control.steps
    candidates = [SgContacts(a=target.a, b=current.b), SgContacts(a=current.a, b=target.b)]
    safe = [(writable.index(p), p) for p in candidates if p in writable]
    if not safe:
        return None
    return (min(safe, key=lambda item: item[0])[1], target)


@unique
class FailsafeCause(StrEnum):
    """Why a heat pump goes back to its normal state at once (:func:`heat_pump_failsafe`)."""

    TEMPERATURE_UNKNOWN = "temperature_unknown"
    HEAT_LIMIT = "heat_limit"
    NO_PROBE = "no_probe"
    PARA14A = "para14a"
    ISLAND = "island"


def sg_has_probe(device: DeviceConfig) -> bool:
    """An SG-Ready heat pump SunCore may intensify: a store probe and a comfort band."""
    return device.comfort is not None and TEMPERATURE_ROLE in device.entities


def is_heat_pump_lift(device: DeviceConfig) -> bool:
    """A heat pump driven by a set-point lift (hot water, flow or buffer set-point)."""
    return device.kind is DeviceKind.HEAT_PUMP and isinstance(device.control, SetpointControl)


def sg_restart_c(device: DeviceConfig, state: DeviceState | None) -> float | None:
    """The temperature an SG-Ready store in state 2 must fall to before SunCore intensifies
    it again, while it reads above it; ``None`` otherwise (not SG-Ready, no comfort band, no
    reading - the last is a fail-safe cause of its own)."""
    if not isinstance(device.control, SgReadyControl) or device.comfort is None:
        return None
    temperature = None if state is None else state.temperature_c
    restart = device.comfort.max_c - SG_RESTART_K
    if temperature is None or temperature <= restart:
        return None
    return restart


def heat_pump_failsafe(  # noqa: PLR0911 - one return per cause
    device: DeviceConfig,
    state: DeviceState | None,
    *,
    island: bool,
    para14a_dimmed: bool,
) -> FailsafeCause | None:
    """Why ``device`` must return to SG state 2 / its base set-point now, or ``None``.

    Applies to SG-Ready heat pumps and heat-pump set-point lifts only. ``para14a_dimmed`` is
    ``True`` while the §14a signal is active **or unknown**; it counts only for a device
    flagged ``para14a`` (plan WP11: "§14a -> state 2, no lift").
    """
    sg = isinstance(device.control, SgReadyControl)
    if not sg and not is_heat_pump_lift(device):
        return None
    if island:
        return FailsafeCause.ISLAND
    if para14a_dimmed and device.para14a:
        return FailsafeCause.PARA14A
    if not sg:
        return None  # a lift is bounded by its own set-point (comfort band)
    if not sg_has_probe(device):
        return FailsafeCause.NO_PROBE
    if heat_capped(device, state):
        temperature = None if state is None else state.temperature_c
        return (
            FailsafeCause.TEMPERATURE_UNKNOWN if temperature is None else FailsafeCause.HEAT_LIMIT
        )
    return None


__all__ = [
    "SG_FORCED",
    "SG_INTENSIFIED",
    "SG_NORMAL",
    "SG_RESTART_K",
    "FailsafeCause",
    "contact_path",
    "heat_pump_failsafe",
    "is_heat_pump_lift",
    "sg_contacts_label",
    "sg_has_probe",
    "sg_restart_c",
    "sg_state",
    "sg_step_of",
]
