# SPDX-License-Identifier: Apache-2.0
"""The generated backend catalogue (``catalogue/<lang>.json``) and key lookup."""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Final

from suncore.i18n.renderer import MessageArgs, MessageError, render_message

LANGUAGES: Final = ("en", "de")
FALLBACK_LANGUAGE: Final = "en"
CATALOGUE_DIR: Final = Path(__file__).parent / "catalogue"


def resolve_language(language: str | None) -> str:
    """``de``, ``de-AT``, ``de_CH`` -> ``de``; anything unknown -> ``en``."""
    base = (language or "").lower().replace("_", "-").split("-")[0]
    return base if base in LANGUAGES else FALLBACK_LANGUAGE


class Catalogue:
    """Messages of one language with the English fallback; rendering never raises."""

    def __init__(
        self,
        language: str,
        messages: Mapping[str, str],
        fallback: Mapping[str, str] | None = None,
    ) -> None:
        self.language = resolve_language(language)
        self._messages = messages
        self._fallback = fallback if fallback is not None else {}

    def has(self, key: str) -> bool:
        return key in self._messages or key in self._fallback

    def render(self, key: str, args: MessageArgs | None = None) -> str:
        """Render ``key``; on a missing key or argument try English, then return the key."""
        attempts = ((self.language, self._messages), (FALLBACK_LANGUAGE, self._fallback))
        for language, messages in attempts:
            source = messages.get(key)
            if source is None:
                continue
            try:
                return render_message(source, language, args)
            except MessageError:
                continue
        return key


def _read(language: str, directory: Path) -> dict[str, str]:
    try:
        data = json.loads((directory / f"{language}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _build(language: str, directory: Path) -> Catalogue:
    english = _read(FALLBACK_LANGUAGE, directory)
    own = english if language == FALLBACK_LANGUAGE else _read(language, directory)
    return Catalogue(language, own, english)


@lru_cache(maxsize=8)
def _packaged(language: str) -> Catalogue:
    return _build(language, CATALOGUE_DIR)


def load_catalogue(language: str | None, directory: Path | None = None) -> Catalogue:
    """The catalogue of ``language`` (cached for the packaged directory)."""
    resolved = resolve_language(language)
    return _packaged(resolved) if directory is None else _build(resolved, directory)
