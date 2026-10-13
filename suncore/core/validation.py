# SPDX-License-Identifier: Apache-2.0
"""Validation errors with stable keys.

Cross-field validators of the configuration models raise :func:`invalid` instead of a bare
``ValueError``. Pydantic reports it with the error type ``suncore.<key>`` and the context, so the
API can answer with a catalogue key (``api.validation.<key>``) and arguments instead of an
English sentence. The English ``message`` stays for logs, tests and non-API callers.
"""

from __future__ import annotations

from pydantic_core import PydanticCustomError

TYPE_PREFIX = "suncore."


def invalid(key: str, message: str, **context: str | int | float | bool) -> PydanticCustomError:
    """Error of type ``suncore.<key>``; ``message`` may use ``{name}`` of ``context``."""
    return PydanticCustomError(f"{TYPE_PREFIX}{key}", message, dict(context))
