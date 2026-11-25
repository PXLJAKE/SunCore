#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Copy the stable app's option translations to the beta app (ADR-0020, WP12).

Weblate manages one component, ``addon/suncore/translations/*.yaml``. The beta app
(``addon/suncore_beta``) must ship the same texts as long as both apps have the same
options. The copy is byte for byte, so Weblate's output formatting is kept.

* Equal option sets (the keys of ``options`` and ``schema`` in both ``config.yaml``):
  ``addon/suncore_beta/translations/<lang>.yaml`` becomes a copy of the stable file of the
  same name; a beta file without a stable counterpart is removed.
* Different option sets: nothing is copied and the run fails with a message. The beta then
  needs its own Weblate component (plan phase 7, WP12) and this script is retired.

``--check`` writes nothing and exits 1 when the beta files are stale (CI and ``check.sh``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
STABLE = ROOT / "addon" / "suncore"
BETA = ROOT / "addon" / "suncore_beta"


def option_names(app: Path) -> set[str]:
    """The option names declared under ``options`` and under ``schema``."""
    config: Any = yaml.safe_load((app / "config.yaml").read_text(encoding="utf-8"))
    return set(config.get("options") or {}) | set(config.get("schema") or {})


def _label(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def plan(stable: Path = STABLE, beta: Path = BETA) -> tuple[dict[Path, str], list[Path]]:
    """Files to write (path -> text) and files to remove so that beta mirrors stable."""
    source = stable / "translations"
    target = beta / "translations"
    writes: dict[Path, str] = {}
    for path in sorted(source.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        dest = target / path.name
        if not dest.exists() or dest.read_text(encoding="utf-8") != text:
            writes[dest] = text
    names = {p.name for p in source.glob("*.yaml")}
    removals = sorted(p for p in target.glob("*.yaml") if p.name not in names)
    return writes, removals


def main(argv: list[str], stable: Path = STABLE, beta: Path = BETA) -> int:
    check = "--check" in argv
    stable_names, beta_names = option_names(stable), option_names(beta)
    if stable_names != beta_names:
        print(
            "the stable and beta apps have different option sets "
            f"(only stable: {sorted(stable_names - beta_names)}, "
            f"only beta: {sorted(beta_names - stable_names)}); the beta translations cannot "
            "be copied. Add a second Weblate component for addon/suncore_beta/translations "
            "and retire scripts/sync_beta_translations.py.",
            file=sys.stderr,
        )
        return 1
    writes, removals = plan(stable, beta)
    if not writes and not removals:
        return 0
    stale = sorted(_label(p) for p in [*writes, *removals])
    if check:
        print(
            "stale beta translations (run `uv run python scripts/sync_beta_translations.py`): "
            + ", ".join(stale),
            file=sys.stderr,
        )
        return 1
    for path, text in writes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for path in removals:
        path.unlink()
    print("updated: " + ", ".join(stale))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
