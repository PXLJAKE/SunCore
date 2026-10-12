# ADR-0001: Backend stack

- **Status:** accepted (maintainer delegated the decision on 2026-10-12)
- **Date:** 2026-10-12
- **Deciders:** maintainer, lead engineer
- **Research:**
  [`ha-apps.md`](../research/ha-apps.md),
  [`solver-on-aarch64.md`](../research/solver-on-aarch64.md)

## Context

SunCore runs as a Home Assistant app on amd64 and aarch64 with a budget of
< 300 MB RAM idle on a Raspberry Pi 4 (BRIEF §2). It needs an asyncio
WebSocket client to Home Assistant, an HTTP/WebSocket API for its own UI, an
LP/MILP solver, SQLite, and later an optional ML load forecaster. The HA base
images ship Python 3.12–3.14 on Alpine (musl) and Debian variants. BRIEF §3
proposes Python 3.12+, FastAPI, Pydantic v2, SQLite.

## Options considered

### Option A – Python 3.13, FastAPI, Pydantic v2, aiohttp, SQLite, Alpine base image

- Pros: matches the HA ecosystem (Core itself is Python/aiohttp), fastest
  path for contributors coming from HA; Pydantic v2 gives schema generation
  for templates and the API; `highspy`, `numpy`, `pydantic-core`, `aiohttp`
  all ship `musllinux` wheels for aarch64; Alpine base keeps the image
  around 150 MB.
- Cons: Python is slower than Go/Rust for the 5–10 s controller loop, but
  the loop is I/O bound; musl occasionally lacks wheels for scientific
  packages (SciPy, scikit-learn) – mitigated by `--only-binary=:all:` in CI
  so a missing wheel fails the build, and by the Debian base image as a
  documented fallback.

### Option B – Go

- Pros: single static binary, low memory, evcc proves it for this domain.
- Cons: no mature LP/MILP binding comparable to HiGHS' Python API, weaker
  schema/validation tooling, smaller pool of HA-savvy contributors, and the
  project would have to re-implement what Pydantic/FastAPI give for free.

### Option C – Python with Flask/Quart or aiohttp-only server

- Pros: fewer dependencies.
- Cons: no automatic OpenAPI, weaker typing; FastAPI's cost is small.

## Decision

Option A.

- Python **3.13** in the image (`ghcr.io/home-assistant/base-python:3.13-alpine3.22`
  or the newest 3.13 tag at build time, pinned in the Dockerfile),
  `requires-python >= 3.12` in `pyproject.toml` so contributors on 3.12 can
  run the test suite.
- `asyncio` throughout, one process: scheduler, HA connector, controller,
  API. The solver runs in a `ProcessPoolExecutor` worker so a long solve
  cannot block the controller.
- Libraries: FastAPI + Uvicorn (`uvicorn` core, `uvloop` optional),
  Pydantic v2 (+ `pydantic-settings`), `aiohttp` (HA WebSocket/REST client
  and outbound HTTP), `aiosqlite`, `structlog`, `highspy` (ADR-0003),
  `numpy` for time-series maths. No pandas in the core (memory).
- Tooling: `uv` for lock files and CI installs, `ruff` (lint + format),
  `mypy --strict` for `core`, `planner`, `controller`, `safety`, `pytest`,
  `hypothesis`, `pytest-asyncio`.
- Dependencies are pinned in `uv.lock`; the image installs from the lock
  with `--only-binary=:all:`.

Not decided here: ML library for the load forecaster (Phase 4 ADR), whether
SciPy is needed.

## Consequences

- Positive: shared language with HA Core and the companion integration;
  Pydantic models serve as the single source for API schema, template JSON
  Schema and the generated parameter reference.
- Negative: image size and RAM must be measured on a Pi 4 in Phase 1 (smoke
  test records RSS after start); a Debian base image is the escape hatch.
- Follow-up: Phase 1 `backend-engineer` builds the skeleton; `qa-engineer`
  adds the memory smoke test.

## Recommendation to the maintainer

Accept. Confirm Python 3.12 as minimum for contributors and 3.13 in the
image.
