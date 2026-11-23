# SPDX-License-Identifier: Apache-2.0
"""The ``expert`` configuration section (Phase 5 WP5, ADR-0015).

A sparse map ``key -> value`` of expert-mode overrides of controller, planner and solver
parameters that were constants or environment variables before. An empty map (the default,
also a missing document) changes nothing: every key falls back to the default of the
parameter registry (:mod:`suncore.core.params`), which equals today's behaviour.

The model validates against the registry (unknown key, wrong type, outside the hard range
are rejected), so a hand-edited document, an import or a history restore cannot carry a value
the safety layer would not accept. Which keys carry the ``safety`` tag is a matter of
``PUT /api/v1/params`` (confirmation); this model enforces only the bounds.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

EXPERT_SCHEMA_VERSION: Final = 1
"""Own schema version of the section (additive: new keys need no bump, a renamed key does)."""

ExpertValue = bool | int | float | str
"""A stored override: a number, a switch or an enumeration choice."""


class ExpertConfig(BaseModel):
    """Configuration section ``expert``: overrides of expert-mode parameters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = EXPERT_SCHEMA_VERSION
    overrides: dict[str, ExpertValue] = Field(default_factory=dict, max_length=200)
    """Only keys that differ from the default are stored; a reset removes the key."""

    @field_validator("overrides")
    @classmethod
    def _registry_checked(cls, value: dict[str, ExpertValue]) -> dict[str, ExpertValue]:
        from suncore.core.params import normalise_overrides  # noqa: PLC0415 - import cycle

        return dict(normalise_overrides(value))


__all__ = ["EXPERT_SCHEMA_VERSION", "ExpertConfig", "ExpertValue"]
