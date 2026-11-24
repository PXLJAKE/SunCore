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

# Equivalent to HA's ``valid_entity_id`` (research/device-integrations.md): lowercase
# letters and digits in domain and object id, single underscores only between
# characters, no look-arounds so the Rust regex engine behind Pydantic accepts it.
# Lives here so that modules without sibling imports (``core/grid.py``) can use it.
ENTITY_ID_PATTERN = r"^[a-z0-9]+(?:_[a-z0-9]+)*\.[a-z0-9]+(?:_[a-z0-9]+)*$"


def invalid(key: str, message: str, **context: str | int | float | bool) -> PydanticCustomError:
    """Error of type ``suncore.<key>``; ``message`` may use ``{name}`` of ``context``."""
    return PydanticCustomError(f"{TYPE_PREFIX}{key}", message, dict(context))
