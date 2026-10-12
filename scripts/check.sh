#!/usr/bin/env bash
# Fast local checks – what CI runs, minus the image build. Used by the
# pre-commit hook in .claude/settings.json (with FAST=1) and by contributors.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/export_openapi.py --check
if [ "${FAST:-0}" = "1" ]; then
  uv run pytest -q -x -m "not slow"
else
  uv run pytest -q --cov --cov-report=term-missing
  # BRIEF §7: >= 80 % on the control-critical packages.
  uv run coverage report --include="suncore/core/*,suncore/safety/*,suncore/planner/*,suncore/controller/*" --fail-under=80 >/dev/null
fi
