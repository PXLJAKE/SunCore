# SPDX-License-Identifier: Apache-2.0
"""Template schema v1, loader and role matcher (ADR-0006)."""

from suncore.devices.templates.loader import (
    BuiltinTemplateError,
    TemplateCatalog,
    TemplateFileError,
    load_templates,
    parse_template,
    scan_directory,
)
from suncore.devices.templates.matcher import (
    match_role,
    matched_criteria,
    rank_entities,
)
from suncore.devices.templates.models import (
    TEMPLATE_ADAPTER,
    DeviceTemplate,
    EntityMatcher,
    ForecastTemplate,
    RegionTemplate,
    RoleSpec,
    TariffTemplate,
    Template,
)

__all__ = [
    "TEMPLATE_ADAPTER",
    "BuiltinTemplateError",
    "DeviceTemplate",
    "EntityMatcher",
    "ForecastTemplate",
    "RegionTemplate",
    "RoleSpec",
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
