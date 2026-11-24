#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate the backend message catalogue from the frontend catalogues.

``suncore/i18n/catalogue/<lang>.json`` holds the keys of the frontend catalogues
(``frontend/src/lib/i18n/locales/<lang>.json``) whose prefix is one the backend renders
(ADR-0016 decision 6). Every message must parse in the renderer's ICU subset; a message
outside it fails the run, so the frontend cannot silently break a backend text.

``--check`` writes nothing and exits 1 when a generated file is stale (CI and ``check.sh``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from suncore.i18n import LANGUAGES, MessageSyntaxError, parse_message  # noqa: E402

LOCALES = ROOT / "frontend" / "src" / "lib" / "i18n" / "locales"
TARGET = ROOT / "suncore" / "i18n" / "catalogue"
PREFIXES = ("mqtt.", "notify.", "safety.", "controller.", "planner.", "device.", "grid.")


def generate(language: str) -> str:
    source = json.loads((LOCALES / f"{language}.json").read_text(encoding="utf-8"))
    picked = {k: v for k, v in sorted(source.items()) if k.startswith(PREFIXES)}
    bad: list[str] = []
    for key, message in picked.items():
        try:
            parse_message(message)
        except MessageSyntaxError as exc:
            bad.append(f"{language}:{key}: {exc}")
    if bad:
        raise SystemExit("messages outside the supported ICU subset:\n  " + "\n  ".join(bad))
    return json.dumps(picked, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale: list[str] = []
    for language in LANGUAGES:
        text = generate(language)
        path = TARGET / f"{language}.json"
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == text:
            continue
        if check:
            stale.append(str(path.relative_to(ROOT)))
        else:
            TARGET.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    if stale:
        print(
            "stale backend catalogue (run `uv run python scripts/gen_backend_catalogue.py`): "
            + ", ".join(stale),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
