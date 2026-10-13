#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate the template JSON Schemas into ``templates/schema/``.

Writes ``{device,tariff,forecast,region}.schema.json`` and ``template.schema.json``
(the discriminated union) from the Pydantic models in ``suncore.templates``.
Run via ``uv run python scripts/gen_schemas.py [--check] [output_dir]``; ``--check`` exits 1
if a committed file is missing, outdated or stale (CI drift guard).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from suncore.templates.models import KIND_MODELS, TEMPLATE_ADAPTER

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "templates" / "schema"
SCHEMA_ID_BASE = "https://raw.githubusercontent.com/pxljake/suncore/main/templates/schema"


def _dump(schema: dict[str, Any], name: str, title: str) -> str:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{SCHEMA_ID_BASE}/{name}",
        "title": title,
        **schema,
    }
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def render() -> dict[str, str]:
    """File name -> content for all generated schemas."""
    files: dict[str, str] = {}
    for kind, model in KIND_MODELS.items():
        name = f"{kind}.schema.json"
        files[name] = _dump(model.model_json_schema(), name, f"SunCore {kind} template v1")
    union = TEMPLATE_ADAPTER.json_schema()
    files["template.schema.json"] = _dump(union, "template.schema.json", "SunCore template v1")
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="fail if files are outdated")
    args = parser.parse_args(argv)
    out: Path = args.output
    files = render()
    if args.check:
        problems = []
        for name, content in files.items():
            path = out / name
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                problems.append(f"{path} is out of date")
        if out.is_dir():
            problems.extend(
                f"{p} is not generated (stale)"
                for p in sorted(out.glob("*.schema.json"))
                if p.name not in files
            )
        if problems:
            sys.stderr.write("\n".join(problems) + "\nrun: uv run python scripts/gen_schemas.py\n")
            return 1
        print(f"gen_schemas: {len(files)} schemas up to date")
        return 0
    out.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (out / name).write_text(content, encoding="utf-8")
    print(f"gen_schemas: wrote {len(files)} schemas to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
