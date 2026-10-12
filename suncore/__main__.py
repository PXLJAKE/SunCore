# SPDX-License-Identifier: Apache-2.0
"""Command-line entry point.

Phase 0 only knows ``--version``; the application bootstrap arrives with
Phase 1 (``suncore.app``).
"""

from __future__ import annotations

import argparse
import sys

from suncore import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="suncore", description="SunCore energy management")
    parser.add_argument("--version", action="version", version=f"suncore {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    sys.stderr.write("SunCore is not runnable yet (Phase 0 skeleton). See docs/roadmap.md.\n")
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
