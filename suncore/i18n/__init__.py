# SPDX-License-Identifier: Apache-2.0
"""Backend texts: a minimal ICU MessageFormat renderer and the generated catalogue.

Standard library only (ADR-0016 decision 6, option 4C). The catalogue under ``catalogue/`` is
generated from the frontend catalogues by ``scripts/gen_backend_catalogue.py``.
"""

from suncore.i18n.catalogue import (
    FALLBACK_LANGUAGE,
    LANGUAGES,
    Catalogue,
    load_catalogue,
    resolve_language,
)
from suncore.i18n.renderer import (
    MessageArgs,
    MessageError,
    MessageSyntaxError,
    MissingArgumentError,
    parse_message,
    render_message,
)

__all__ = [
    "FALLBACK_LANGUAGE",
    "LANGUAGES",
    "Catalogue",
    "MessageArgs",
    "MessageError",
    "MessageSyntaxError",
    "MissingArgumentError",
    "load_catalogue",
    "parse_message",
    "render_message",
    "resolve_language",
]
