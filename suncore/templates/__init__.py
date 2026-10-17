# SPDX-License-Identifier: Apache-2.0
"""Template schema v1, loader and role matcher (ADR-0006, ADR-0012).

Depends on ``suncore.core`` (and yaml/pydantic) only; ``devices``, ``tariff``, ``forecast``
and ``api`` import it.
"""

from suncore.templates.loader import (
    BuiltinTemplateError,
    TemplateCatalog,
    TemplateFileError,
    load_templates,
    parse_template,
    scan_directory,
)
from suncore.templates.matcher import (
    match_role,
    matched_criteria,
    rank_entities,
)
from suncore.templates.models import (
    TEMPLATE_ADAPTER,
    ActionParam,
    ActionSeries,
    AttributeSeries,
    BatteryTemplate,
    DeviceTemplate,
    EntityMatcher,
    ForecastTemplate,
    RegionTemplate,
    RoleSpec,
    SeriesFields,
    TariffTemplate,
    Template,
)

__all__ = [
    "TEMPLATE_ADAPTER",
    "ActionParam",
    "ActionSeries",
    "AttributeSeries",
    "BatteryTemplate",
    "BuiltinTemplateError",
    "DeviceTemplate",
    "EntityMatcher",
    "ForecastTemplate",
    "RegionTemplate",
    "RoleSpec",
    "SeriesFields",
    "TariffTemplate",
    "Template",
    "TemplateCatalog",
    "TemplateFileError",
    "load_templates",
    "match_role",
    "matched_criteria",
    "parse_template",
    "rank_entities",
    "scan_directory",
]
