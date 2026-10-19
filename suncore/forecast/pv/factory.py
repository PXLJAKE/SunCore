# SPDX-License-Identifier: Apache-2.0
"""Build the configured PV forecast sources from the ``forecast`` section (Phase 4 WP5)."""

from __future__ import annotations

import structlog

from suncore.core.config import ForecastConfig
from suncore.forecast.pv.adapter import PvDataReader, TemplateForecastSource
from suncore.templates import ForecastTemplate, TemplateCatalog

log = structlog.get_logger(__name__)


def build_sources(
    config: ForecastConfig | None, catalog: TemplateCatalog, reader: PvDataReader
) -> list[TemplateForecastSource]:
    """One source per configured forecast template, in the configured order.

    A source whose template is unknown, is not a PV forecast template or is configured without
    anything to read is skipped with a warning (the others keep working).
    """
    if config is None:
        return []
    sources: list[TemplateForecastSource] = []
    for item in config.sources:
        template = catalog.get(item.template_id)
        if not isinstance(template, ForecastTemplate) or template.forecast_kind != "pv":
            log.warning("forecast.pv.template_unusable", template=item.template_id)
            continue
        entities = {role: ref.entity_id for role, ref in item.entities.items()}
        sources.append(
            TemplateForecastSource(template, entities, reader, config_entry_id=item.config_entry_id)
        )
    return sources


__all__ = ["build_sources"]
