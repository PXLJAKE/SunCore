# SPDX-License-Identifier: Apache-2.0
"""Tariff adapters, price formula application and the price service (Phase 4 WP4).

* :mod:`suncore.tariff.parse` - pure parsing of price lists (timestamps, shapes, segments);
* :mod:`suncore.tariff.engine` - a :class:`TariffTemplate` becomes a price source;
* :mod:`suncore.tariff.sources` - fixed and HT/NT sources, the source protocol;
* :mod:`suncore.tariff.pricing` - formula, export side and the negative-price rule;
* :mod:`suncore.tariff.service` - :class:`TariffService`: refresh, cache, store, serve.

Home Assistant is reached only through the injected ``ActionCaller`` and ``AttributeReader``
(wired in ``suncore.app.adapters``).
"""

from suncore.tariff.engine import (
    ActionCaller,
    CurrentPriceSource,
    EntityReader,
    EntityReading,
    SourceBinding,
    TemplatePriceSource,
)
from suncore.tariff.errors import (
    AmbiguousSeriesError,
    ExportConfigError,
    ExportTiersError,
    MalformedPayloadError,
    MissingEntityError,
    MissingParamError,
    SourceFailedError,
    SourceThrottledError,
    SourceUnavailableError,
    TariffError,
    UnitMismatchError,
)
from suncore.tariff.pricing import apply_import_formula, export_base_price, planning_prices
from suncore.tariff.report import PriceReport
from suncore.tariff.service import (
    PriceStore,
    RefreshResult,
    ServiceSettings,
    TariffPlan,
    TariffService,
)
from suncore.tariff.sources import (
    FixedPriceSource,
    SegmentSource,
    SourceData,
    TouPriceSource,
    finest_series,
)

__all__ = [
    "ActionCaller",
    "AmbiguousSeriesError",
    "CurrentPriceSource",
    "EntityReader",
    "EntityReading",
    "ExportConfigError",
    "ExportTiersError",
    "FixedPriceSource",
    "MalformedPayloadError",
    "MissingEntityError",
    "MissingParamError",
    "PriceReport",
    "PriceStore",
    "RefreshResult",
    "SegmentSource",
    "ServiceSettings",
    "SourceBinding",
    "SourceData",
    "SourceFailedError",
    "SourceThrottledError",
    "SourceUnavailableError",
    "TariffError",
    "TariffPlan",
    "TariffService",
    "TemplatePriceSource",
    "TouPriceSource",
    "UnitMismatchError",
    "apply_import_formula",
    "export_base_price",
    "finest_series",
    "planning_prices",
]
