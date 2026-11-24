# SPDX-License-Identifier: Apache-2.0
"""Notifications (Phase 6 WP5, ADR-0016 decision 7).

Rules, rate limits, rendering and delivery through ``HAConnection.notify``, plus the confirm path
for assisted proposals. Depends on ``core``, ``i18n`` and the types of ``ha.connection`` only;
nothing here writes to a device, switches a mode or reaches the safety gate except through the
injected :class:`ProposalPort`.
"""

from suncore.notify.actions import ActionTokens, Consumed, Refusal, Verb
from suncore.notify.model import POLICIES, Category, NotifyEvent, ProposalRef, Verdict
from suncore.notify.ratelimit import NotifyLimiter, in_quiet_hours
from suncore.notify.rules import (
    DeviceObs,
    Evaluation,
    Observation,
    ProposalObs,
    RuleEngine,
    cheap_window,
)
from suncore.notify.service import (
    ActionsState,
    ConfirmOutcome,
    NotificationService,
    NotifyStatus,
    ProposalPort,
    ProposalView,
    TestResult,
)

__all__ = [
    "POLICIES",
    "ActionTokens",
    "ActionsState",
    "Category",
    "ConfirmOutcome",
    "Consumed",
    "DeviceObs",
    "Evaluation",
    "NotificationService",
    "NotifyEvent",
    "NotifyLimiter",
    "NotifyStatus",
    "Observation",
    "ProposalObs",
    "ProposalPort",
    "ProposalRef",
    "ProposalView",
    "Refusal",
    "RuleEngine",
    "TestResult",
    "Verb",
    "Verdict",
    "cheap_window",
    "in_quiet_hours",
]
