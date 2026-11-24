# SPDX-License-Identifier: Apache-2.0
"""The Python renderer equals ``intl-messageformat`` on the golden file (ADR-0016)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from suncore.i18n import load_catalogue, render_message

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = json.loads((ROOT / "tests" / "fixtures" / "i18n_golden.json").read_text("utf-8"))
CATALOGUE = ROOT / "suncore" / "i18n" / "catalogue"


def _messages(lang: str) -> dict[str, str]:
    return json.loads((CATALOGUE / f"{lang}.json").read_text("utf-8"))


def test_golden_covers_every_generated_key() -> None:
    for lang in ("en", "de"):
        seen = {c["key"] for c in GOLDEN if c["lang"] == lang}
        assert seen == set(_messages(lang))


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: f"{c['lang']}:{c['key']}:{c['args']}")
def test_renderer_matches_intl_messageformat(case: dict[str, object]) -> None:
    lang = str(case["lang"])
    source = _messages(lang)[str(case["key"])]
    args = case["args"]
    assert isinstance(args, dict)
    assert render_message(source, lang, args) == case["expected"]


def test_catalogue_render_uses_the_packaged_files() -> None:
    cat = load_catalogue("de")
    assert cat.render("mqtt.state.mode.automatic") == "Automatisch"
    assert load_catalogue("fr").render("mqtt.state.mode.automatic") == "Automatic"
