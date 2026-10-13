#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Validate all YAML templates under ``templates/<kind>/``.

Checks: schema (Pydantic models, same as the generated JSON Schema), unique ids, file name
``<id>.yaml``, directory matches ``kind``, non-empty ``source``, and that every i18n key a
template uses (``name``, role descriptions) exists in the frontend catalogues
``en.json`` and ``de.json`` (same catalogues as ``scripts/check_reasons.py``).
Run via ``uv run python scripts/validate_templates.py [--check]``; exit code 1 lists problems.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from suncore import __version__
from suncore.templates.loader import scan_directory

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
LOCALES = ROOT / "frontend" / "src" / "lib" / "i18n" / "locales"


def validate(templates: Path = TEMPLATES, locales: Path = LOCALES) -> tuple[int, list[str]]:
    """Return ``(number of valid templates, problems)``."""
    found, errors = scan_directory(templates, app_version=__version__)
    problems = [str(e) for e in errors]
    catalogues = {
        name: json.loads((locales / f"{name}.json").read_text(encoding="utf-8"))
        for name in ("en", "de")
    }
    for path, template in found:
        for key in template.message_keys():
            problems.extend(
                f"{path}: i18n key `{key}` missing in {name}.json"
                for name, data in catalogues.items()
                if key not in data
            )
    return len(found), problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="accepted for symmetry with CI")
    parser.add_argument("root", nargs="?", type=Path, default=TEMPLATES)
    args = parser.parse_args(argv)
    count, problems = validate(args.root)
    if problems:
        sys.stderr.write("\n".join(problems) + "\n")
        return 1
    print(f"validate_templates: {count} templates valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
