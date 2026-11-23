# SPDX-License-Identifier: Apache-2.0
"""Errors of the tariff adapters; each carries a catalogue key (``tariff.error.*``).

The keys are rendered by the frontend's ICU catalogue (``en.json`` / ``de.json``); ``args``
are the ICU arguments. An error never carries free text for the user.
"""

from __future__ import annotations

from typing import Any, ClassVar, Final

ERR_UNAVAILABLE: Final = "tariff.error.source_unavailable"
ERR_MALFORMED: Final = "tariff.error.malformed_payload"
ERR_AMBIGUOUS: Final = "tariff.error.ambiguous_series"
ERR_MISSING_PARAM: Final = "tariff.error.missing_param"
ERR_MISSING_ROLE: Final = "tariff.error.missing_entity"
ERR_THROTTLED: Final = "tariff.error.throttled"
ERR_FAILED: Final = "tariff.error.action_failed"
ERR_UNIT_MISMATCH: Final = "tariff.error.unit_mismatch"
ERR_CURRENCY_MISMATCH: Final = "tariff.error.currency_mismatch"
ERR_EXPORT_NEEDS_KWP: Final = "tariff.error.export_needs_kwp"
ERR_EXPORT_TIERS: Final = "tariff.error.export_tiers"


class TariffError(Exception):
    """Base class: ``key`` is the catalogue key, ``args`` its ICU arguments."""

    key: ClassVar[str] = ERR_FAILED

    def __init__(self, detail: str = "", **args: Any) -> None:
        super().__init__(detail or self.key)
        self.detail = detail
        self.args_: dict[str, Any] = args

    @property
    def message_args(self) -> dict[str, Any]:
        return dict(self.args_)


class SourceUnavailableError(TariffError):
    """The price entity (or all of them) is unknown, ``unavailable`` or has no price list."""

    key = ERR_UNAVAILABLE


class MalformedPayloadError(TariffError):
    """The response or attribute does not have the documented shape."""

    key = ERR_MALFORMED


class AmbiguousSeriesError(TariffError):
    """A response holds several series (homes, areas) and the configuration names none."""

    key = ERR_AMBIGUOUS


class MissingParamError(TariffError):
    """The action needs a request field the tariff configuration does not supply."""

    key = ERR_MISSING_PARAM


class MissingEntityError(TariffError):
    """A role the series is read from has no configured entity."""

    key = ERR_MISSING_ROLE


class SourceThrottledError(TariffError):
    """The action was asked for too often and skipped (not a failure: use the last data)."""

    key = ERR_THROTTLED

    def __init__(self, retry_after_s: float, detail: str = "") -> None:
        super().__init__(detail, retry_after_s=retry_after_s)
        self.retry_after_s = retry_after_s


class SourceFailedError(TariffError):
    """Home Assistant or the integration failed to answer the action."""

    key = ERR_FAILED


class UnitMismatchError(TariffError):
    """The entity's price unit (per kWh or per MWh) contradicts the template's value unit."""

    key = ERR_UNIT_MISMATCH


class CurrencyMismatchError(UnitMismatchError):
    """The source's currency differs from the region's (prices would be in the wrong money)."""

    key = ERR_CURRENCY_MISMATCH


class ExportConfigError(TariffError):
    """The export tariff cannot be turned into a price (tiered without the plant size)."""

    key = ERR_EXPORT_NEEDS_KWP


class ExportTiersError(ExportConfigError):
    """The feed-in tiers do not cover the whole plant (or are not ordered)."""

    key = ERR_EXPORT_TIERS
