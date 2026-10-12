# SPDX-License-Identifier: Apache-2.0
"""Command-line entry point: ``python -m suncore`` starts the application."""

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
    from suncore.app.main import run  # noqa: PLC0415 - keep --version free of heavy imports

    return run()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
