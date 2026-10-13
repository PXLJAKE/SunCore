# SPDX-License-Identifier: Apache-2.0
"""Neutral entity metadata shared by ``ha`` (producer) and ``devices`` (template matching).

It lives in ``core`` so neither package has to import the other. The values are
what the Home Assistant entity registry and state attributes provide; every field
besides ``entity_id`` and ``domain`` may be unknown (``None``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EntityMeta:
    """Registry/state metadata of one entity, used to rank template role matches."""

    entity_id: str
    domain: str
    platform: str | None = None
    device_class: str | None = None
    unit: str | None = None
    translation_key: str | None = None
    state_class: str | None = None
    area_id: str | None = None
    device_id: str | None = None
    """Registry device the entity belongs to (``di``/``device_id``), if known."""
    entity_category: str | None = None
    """``config`` or ``diagnostic`` (registry), ``None`` for a primary entity."""
    hidden: bool = False
    """Hidden in the registry (by the user or the integration)."""
    disabled: bool = False
    """Disabled in the registry (only the full ``list`` command reports it)."""
    name: str | None = None
    """Display name (friendly name from the state, else the registry name)."""

    @classmethod
    def of(
        cls,
        entity_id: str,
        *,
        platform: str | None = None,
        device_class: str | None = None,
        unit: str | None = None,
        translation_key: str | None = None,
        state_class: str | None = None,
        area_id: str | None = None,
        device_id: str | None = None,
        entity_category: str | None = None,
        hidden: bool = False,
        disabled: bool = False,
        name: str | None = None,
    ) -> EntityMeta:
        """Build metadata and derive ``domain`` from the entity id."""
        return cls(
            entity_id=entity_id,
            domain=entity_id.split(".", 1)[0],
            platform=platform,
            device_class=device_class,
            unit=unit,
            translation_key=translation_key,
            state_class=state_class,
            area_id=area_id,
            device_id=device_id,
            entity_category=entity_category,
            hidden=hidden,
            disabled=disabled,
            name=name,
        )
