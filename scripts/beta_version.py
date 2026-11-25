#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Derive the beta app version (ADR-0009 addendum).

The Supervisor offers an app update whenever the `version` in the store's
`config.yaml` differs from the installed one (string inequality, no ordering)
and pulls `<image>:<version>`. Every beta image published from `main` therefore
needs a new, Docker-tag-safe version in `addon/suncore_beta/config.yaml`.

Scheme: `<base>-beta.<n>`.

- `base` is the stable minor + 1, unless `release-please-config.json` sets `release-as`
  (package `.`) to a version above the stable one: then that is the base, so the betas
  before a jump read `1.0.0-beta.<n>`. A `release-as` that is not above the stable version is
  stale (already released) and ignored. Never edit `addon/suncore_beta/config.yaml` by hand.
  (`release-as` is marked deprecated in release-please's schema, which prefers a
  `Release-As:` commit footer; the footer is invisible to this script, hence the key.)
- `n` is `max(run_number, current_n + 1)` while the previous beta base equals
  the new base, `run_number` otherwise. A result that would order before the
  committed version is refused.

Usage:
  beta_version.py compute --run-number N [--stable F] [--beta F] [--release-config F]
  beta_version.py write VERSION [--beta F]                         # rewrites config.yaml
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STABLE_CONFIG = ROOT / "addon" / "suncore" / "config.yaml"
BETA_CONFIG = ROOT / "addon" / "suncore_beta" / "config.yaml"
RELEASE_CONFIG = ROOT / "release-please-config.json"

_VERSION_LINE = re.compile(r'^(version:\s*)"?([^"\s#]+)"?(.*)$', re.MULTILINE)
_STABLE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_BETA = re.compile(r"^(\d+)\.(\d+)\.(\d+)-beta\.(\d+)$")

Triple = tuple[int, int, int]


def read_version(path: Path) -> str:
    match = _VERSION_LINE.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"no top-level `version:` line in {path}")
    return match.group(2)


def parse_stable(version: str) -> Triple:
    match = _STABLE.match(version)
    if match is None:
        raise ValueError(f"stable version must be x.y.z, got {version!r}")
    return int(match[1]), int(match[2]), int(match[3])


def parse_beta(version: str) -> tuple[Triple, int]:
    match = _BETA.match(version)
    if match is None:
        raise ValueError(f"beta version must be x.y.z-beta.n, got {version!r}")
    return (int(match[1]), int(match[2]), int(match[3])), int(match[4])


def read_release_as(path: Path) -> str | None:
    """The `release-as` of package `.` in release-please's config, `None` if unset or no file."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data.get("packages", {}).get(".", {}).get("release-as")
    except (ValueError, AttributeError) as err:
        raise ValueError(f"cannot read release-as from {path}: {err}") from err
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"release-as must be a string x.y.z, got {value!r}")
    return value


def next_beta_version(
    stable: str, current_beta: str, run_number: int, release_as: str | None = None
) -> str:
    """Return the beta version for this build; see the module docstring."""
    if run_number < 1:
        raise ValueError("run number must be >= 1")
    stable_triple = parse_stable(stable)
    base = (stable_triple[0], stable_triple[1] + 1, 0)
    if release_as is not None:
        explicit = parse_stable(release_as)
        if explicit > stable_triple:  # otherwise stale: that version is already out
            base = explicit
    previous_base, current_n = parse_beta(current_beta)
    if previous_base == base:
        number = max(run_number, current_n + 1)
    elif previous_base > base:
        raise ValueError(
            f"refusing to go backwards: beta {current_beta} is ahead of base "
            f"{base[0]}.{base[1]}.{base[2]}"
        )
    else:
        number = run_number
    return f"{base[0]}.{base[1]}.{base[2]}-beta.{number}"


def is_newer(candidate: str, current: str) -> bool:
    """True if `candidate` orders strictly after `current` (base, then n)."""
    return parse_beta(candidate) > parse_beta(current)


def write_version(path: Path, version: str) -> bool:
    """Rewrite the version line; False if the file already has that version."""
    parse_beta(version)
    text = path.read_text(encoding="utf-8")
    if read_version(path) == version:
        return False
    new_text, count = _VERSION_LINE.subn(rf'\g<1>"{version}"\g<3>', text, count=1)
    if count != 1:
        raise ValueError(f"could not rewrite version in {path}")
    path.write_text(new_text, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    compute = sub.add_parser("compute", help="print the next beta version")
    compute.add_argument("--run-number", type=int, required=True)
    compute.add_argument("--stable", type=Path, default=STABLE_CONFIG)
    compute.add_argument("--beta", type=Path, default=BETA_CONFIG)
    compute.add_argument("--release-config", type=Path, default=RELEASE_CONFIG)
    write = sub.add_parser("write", help="write a beta version into config.yaml")
    write.add_argument("version")
    write.add_argument("--beta", type=Path, default=BETA_CONFIG)
    args = parser.parse_args(argv)

    try:
        if args.command == "compute":
            print(
                next_beta_version(
                    read_version(args.stable),
                    read_version(args.beta),
                    args.run_number,
                    read_release_as(args.release_config),
                )
            )
        else:
            current = read_version(args.beta)
            if not is_newer(args.version, current) and args.version != current:
                print(f"refusing to go backwards: {current} -> {args.version}", file=sys.stderr)
                return 3
            print("changed" if write_version(args.beta, args.version) else "unchanged")
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
