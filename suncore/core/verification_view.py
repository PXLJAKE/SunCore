# SPDX-License-Identifier: Apache-2.0
"""What the inverter limit verification shows the outside (PR 7-J, plan phase 7 WP8).

Pure data for the API and the panel: the procedure's phase, the reasons it waits or ended, the
proposed test limit and the live values. The window the gate holds is in
``suncore.core.verification`` (safety imports that one, not this).
"""

from __future__ import annotations

from enum import StrEnum, unique
from typing import Final, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from suncore.core.config import InverterVerificationRecord
from suncore.core.decisions import InverterLimitAction, Reason


@unique
class VerificationPhase(StrEnum):
    """Where the verification procedure is (``suncore.app.verification``)."""

    IDLE = "idle"
    CHECKING = "checking"
    """Preconditions hold; waiting until they have held for the stability time."""
    READY = "ready"
    """The test limit is proposed; nothing is written until the user confirms it."""
    WRITING = "writing"
    """The user confirmed: the gate judges and writes the test limit."""
    OBSERVING = "observing"
    """The test limit is held; watching whether and how the export follows."""
    RELEASING = "releasing"
    """The user's own limit is being restored and read back."""
    PASSED = "passed"
    FAILED = "failed"
    ABORTED = "aborted"


TERMINAL_PHASES: Final = frozenset(
    {
        VerificationPhase.IDLE,
        VerificationPhase.PASSED,
        VerificationPhase.FAILED,
        VerificationPhase.ABORTED,
    }
)


class VerificationView(BaseModel):
    """The procedure as the dialog shows it (``GET /api/v1/inverter/verification``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    phase: VerificationPhase = VerificationPhase.IDLE
    adapter: bool = False
    """An inverter limit adapter is configured and built."""
    capability: Literal["export_limit", "ac_limit"] | None = None
    verified: bool = False
    """``inverter.limit_verified`` for the current adapter."""
    reasons: list[Reason] = []
    """Why the procedure waits, refused to start or ended (catalogue keys)."""
    stable_s: float = 0.0
    stable_needed_s: float = 0.0
    baseline: InverterLimitAction | None = None
    """The user's own limit, read before the test (restored afterwards)."""
    test_limit: InverterLimitAction | None = None
    test_limit_w: float | None = None
    """The test limit in W of AC output or export (for the dialog)."""
    confirm_by: AwareDatetime | None = None
    window_expires_at: AwareDatetime | None = None
    export_w: float | None = None
    house_w: float | None = None
    battery_w: float | None = None
    export_before_w: float | None = None
    export_during_w: float | None = None
    semantics: Literal["export_limit", "ac_limit"] | None = None
    last: InverterVerificationRecord | None = None
    """The outcome of the last finished test that was stored (``inverter.last_verification``)."""


class VerificationRefusedError(RuntimeError):
    """The verification cannot start or continue now; ``reasons`` say why (API 409)."""

    def __init__(self, reasons: tuple[Reason, ...]) -> None:
        super().__init__(", ".join(r.key for r in reasons))
        self.reasons = reasons


__all__ = [
    "TERMINAL_PHASES",
    "VerificationPhase",
    "VerificationRefusedError",
    "VerificationView",
]
