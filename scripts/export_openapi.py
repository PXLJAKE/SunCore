#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write the generated OpenAPI document to ``frontend/src/lib/api/openapi.json``.

The frontend derives its TypeScript types from this file. Run via
``uv run python scripts/export_openapi.py [--check] [output]``; ``--check`` exits 1 if the
committed file is out of date (CI drift guard).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from suncore.api import create_app
from suncore.app.openapi_stub import StubServices

DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "api" / "openapi.json"
)


def render() -> str:
    schema = create_app(StubServices()).openapi()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="fail if the file is outdated")
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current != text:
            sys.stderr.write(f"{args.output} is out of date; run scripts/export_openapi.py\n")
            return 1
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
