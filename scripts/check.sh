#!/usr/bin/env bash
# Fast local checks – what CI runs, minus the image build. Used by the
# pre-commit hook in .claude/settings.json (with FAST=1) and by contributors.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/export_openapi.py --check
uv run python scripts/check_reasons.py
if [ "${FAST:-0}" = "1" ]; then
  uv run pytest -q -x -m "not slow"
else
  uv run pytest -q --cov --cov-report=term-missing
  # BRIEF §7: >= 80 % on the control-critical packages.
  uv run coverage report --include="suncore/core/*,suncore/safety/*,suncore/planner/*,suncore/controller/*" --fail-under=80 >/dev/null
fi

# Frontend checks only where the toolchain is installed (the Python path works without Node).
# E2E (Playwright) runs in CI and with `npm run test:e2e`; set E2E=1 to include it here.
if [ -d frontend/node_modules ]; then
  (
    cd frontend
    npm run --silent gen:api:check
    npm run --silent lint
    npm run --silent format:check
    npm run --silent check
    npm run --silent test
    if [ "${FAST:-0}" != "1" ]; then
      npm run --silent build
      npm run --silent size
      if [ "${E2E:-0}" = "1" ]; then npm run --silent test:e2e; fi
    fi
  )
fi
