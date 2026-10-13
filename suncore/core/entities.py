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
        )
