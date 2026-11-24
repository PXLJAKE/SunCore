# SPDX-License-Identifier: Apache-2.0
"""Rendering of notification texts through the backend ICU catalogue (ADR-0016 decision 6).

A message family ``<key>`` has the catalogue entries ``<key>`` (the message) and ``<key>.title``.
A key missing from the catalogue renders as the key itself (never an exception, never free text
from a rule).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from suncore.core.decisions import Reason
from suncore.i18n import load_catalogue

PANEL_HINT_KEY: Final = "notify.panel_hint"
ACTION_CONFIRM_KEY: Final = "notify.action.confirm"
ACTION_REJECT_KEY: Final = "notify.action.reject"


@dataclass(frozen=True, slots=True)
class Rendered:
    title: str
    message: str


def render_reason(reason: Reason, language: str, *, panel_hint: bool = False) -> Rendered:
    """Title and message of ``reason``; ``panel_hint`` appends the pointer to the panel."""
    catalogue = load_catalogue(language)
    args = dict(reason.args)
    message = catalogue.render(reason.key, args)
    if panel_hint:
        message = f"{message}\n{catalogue.render(PANEL_HINT_KEY)}"
    return Rendered(catalogue.render(f"{reason.key}.title", args), message)


def render_action_titles(language: str) -> tuple[str, str]:
    """``(confirm, reject)`` button labels."""
    catalogue = load_catalogue(language)
    return catalogue.render(ACTION_CONFIRM_KEY), catalogue.render(ACTION_REJECT_KEY)
