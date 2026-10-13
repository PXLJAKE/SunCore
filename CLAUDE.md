# CLAUDE.md

SunCore is a Home Assistant app (formerly add-on) that forecasts PV, load
and prices, plans 24–48 h with an LP/MILP planner and distributes real
surplus every few seconds to consumers and the battery. Every write to a
device passes a safety layer; shadow mode is the default. Apache-2.0.

## Read first, every session

1. `docs/STATUS.md` – current state, open items, `UNVERIFIED` list.
2. The ADRs your task touches: `docs/adr/README.md` (index).
3. `docs/BRIEF.md` is the full specification – read the section you need,
   not the whole file.
4. `docs/plans/phase-<n>.md` – the approved plan for the running phase.

## Layout

```text
suncore/        Python package: core, ha, forecast, tariff, planner,
                controller, safety, templates, devices, api, storage, sim, app
frontend/       Svelte 5 + Vite SPA (from Phase 1)
addon/          HA app folders: suncore (stable), suncore_beta
templates/      YAML templates + generated JSON Schema (ADR-0006)
tests/          pytest, hypothesis, simulator scenarios
docs/           BRIEF, STATUS, roadmap, architecture, adr/, research/, plans/
scripts/        check.sh and generators
.claude/        agents/, skills/, hooks/, settings.json
```

Dependency rule: `core/` imports nothing from siblings; `planner/`,
`controller/`, `safety/` depend on `core/` only (see `docs/architecture.md`).

## Commands

```bash
uv sync --all-groups            # set up
./scripts/check.sh              # ruff, mypy, pytest with coverage (CI parity)
FAST=1 ./scripts/check.sh       # what the commit hook runs
uv run pytest tests/test_x.py -k name
docker build -f addon/suncore/Dockerfile -t local/suncore .
```

Frontend (Phase 1+): `cd frontend && npm ci && npm run dev|test|build`.

## Working rules (short form of BRIEF §0)

- Understand → plan → build. Each phase starts with `docs/plans/phase-<n>.md`;
  the maintainer approves before implementation.
- Verify, don't guess. Check HA interfaces against the developer docs
  (GitHub source mirrors when the sites are unreachable, see
  `docs/research/README.md`). Never invent entity ids, services, attributes,
  registers or API fields; mark the unverifiable `UNVERIFIED` in code and PR.
- Small steps: one topic per PR, Conventional Commits, tests + lint green,
  CHANGELOG via release-please, docs and `docs/STATUS.md` updated.
- Ask when something is missing (PR section "Offene Fragen an den
  Maintainer") and keep working on the rest. No silent mocks.
- Safety first: anything that switches devices or writes set-points goes
  through `safety/` and is reviewed by the `control-safety` agent.
- Languages: code, comments, commits, issues, primary docs in English;
  German is the first translation; PR descriptions and questions to the
  maintainer in German.
- Budget: Fable agents for architecture, optimiser, controller/safety and
  final reviews; Sonnet for routine work; Haiku for mechanical work. No
  large parallel fan-outs. Read big files selectively.
- Licences: study evcc/EMHASS/Predbat concepts freely; copy code only with
  a compatible licence and attribution in `NOTICE` (Predbat: never).

## Conventions

- Units: W, Wh, °C, currency/kWh. Signs: grid + = import, battery + =
  charging, device + = consumption, PV + = generation. Convert in adapters.
- Explanations are `Reason(key, args)` rendered through the ICU catalogue;
  no free-text reasons, no hardcoded UI strings (lint enforces it).
- SPDX header on every source file. `mypy --strict` for core, planner,
  controller, safety.
- Templates need `source` and a simulator scenario.

## Agents and skills

Agents in `.claude/agents/` (architect, optimizer-engineer, control-safety,
backend-engineer, ha-integration, frontend-engineer, device-adapters,
qa-engineer, docs-writer, i18n-sync, reviewer). Skills in `.claude/skills/`
(new-device-template, new-adapter, verify-ha-api, prepare-release,
sync-translations, demo-screenshots). Hooks format on edit and run fast
checks before `git commit`.
