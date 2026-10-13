# SPDX-License-Identifier: Apache-2.0
"""Result types of entity detection and Energy dashboard import (Phase 2 WP5).

Pure data shared by ``ha`` (producer), ``app`` (composition) and ``api`` (wire models), so
that the API never imports ``ha``. Explanations are :class:`~suncore.core.decisions.Reason`
keys or criterion keys (``detect.criterion.<name>``), rendered by the frontend catalogue.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from suncore.core.decisions import Reason
from suncore.core.signals import Quality


@dataclass(frozen=True, slots=True)
class DetectedEntity:
    """One entity offered for a role, with why it matches and what it reads right now.

    ``unit`` and ``device_class`` are empty strings when unknown (the Phase 1 picker relied
    on plain strings). ``value`` is the raw numeric state in ``unit`` (not converted, no sign
    inversion); ``quality`` is the entity cache's judgement (``unavailable`` when the entity
    has no usable state at all).
    """

    entity_id: str
    name: str
    domain: str
    platform: str | None
    device_class: str
    unit: str
    state_class: str | None
    area_id: str | None
    area_name: str | None
    device_id: str | None
    device_name: str | None
    score: int
    criteria: tuple[str, ...]
    """Catalogue keys ``detect.criterion.<name>`` of every criterion that matched."""
    state: str
    available: bool
    value: float | None
    quality: Quality


@dataclass(frozen=True, slots=True)
class Suggestion:
    """One proposed setting. ``path`` addresses a SiteConfig field (``battery.power``)."""

    path: str
    value: Any
    """JSON value: an ``EntityRef`` object (``entity_id``, ``invert_sign``) or a number."""
    reason: Reason


@dataclass(frozen=True, slots=True)
class EnergySuggestions:
    """What the Energy dashboard preferences suggest. Never applied automatically."""

    configured: bool
    """``False`` if the dashboard was never saved (HA answers ``not_found``)."""
    suggestions: tuple[Suggestion, ...] = ()
    notes: tuple[Reason, ...] = ()
    """Things worth telling the user that are not settings (ignored sources, ...)."""
    site: Mapping[str, Any] = field(default_factory=dict)
    """The suggestions folded into a (partial) SiteConfig document."""
