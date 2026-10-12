# Phase 0 – Research, ADRs, Architecture, Repository Scaffold

Status: **draft, awaiting maintainer approval**
Source of truth: [`docs/BRIEF.md`](../BRIEF.md), sections 0, 2, 3, 7, 10, 11 and 12.
Project name: **SunCore** (the GitHub repository name).

Phase 0 produces no feature code. Its result is an approved, verifiable
blueprint: research notes, architecture decision records, an architecture
document, a roadmap, a green repository scaffold, and the agent team that will
build the following phases.

## 1. Goals and acceptance criteria

Phase 0 is done when:

1. Every HA interface SunCore will depend on is documented in `docs/research/`
   with a link to the current developer documentation, or is marked
   `UNVERIFIED` with the reason.
2. At least the nine ADRs from BRIEF §12.2 exist, each with context, options,
   decision, consequences, and a recommendation the maintainer can accept or
   reject per ADR.
3. `docs/architecture.md` describes modules, data flow, planner/controller/
   safety interplay and the device model with Mermaid diagrams.
4. `docs/roadmap.md` refines the phases of BRIEF §10 with milestones and
   acceptance criteria.
5. The repository scaffold exists: Apache-2.0 licence, `NOTICE`, README draft,
   community files, issue/PR templates, empty Python packages with lint/type/
   test setup, and CI workflows that are **green** on the PR.
6. `.claude/agents/`, `.claude/skills/`, `.claude/settings.json` hooks and a
   short `CLAUDE.md` exist as described in BRIEF §11.
7. `docs/STATUS.md` exists and reflects the state at the end of Phase 0.
8. A pull request with a German summary, the ADR list with recommendations, and
   an "Offene Fragen an den Maintainer" section is open. Work stops there.

## 2. Work packages

### WP0 – Branch and plan (this document)

- Branch `phase-0/foundation`, this plan committed first.
- Files: `docs/plans/phase-0.md`.

### WP1 – Research (`docs/research/`)

Short, sourced notes. Each note ends with a "Verified on <date> against
<URL>" line or an explicit `UNVERIFIED` block. No code, no decisions – those
go into the ADRs.

| File | Content | Primary sources |
|---|---|---|
| `ha-apps.md` | App (add-on) repository layout, `config.yaml` keys (`ingress`, `panel_admin`, `watchdog`, `services: mqtt:want`, `map`, `backup_exclude`, `image`, `arch`), Supervisor API (info, services/mqtt, addons/self), builder action, app linter, supported architectures | developers.home-assistant.io (add-ons section), `home-assistant/builder`, `frenck/action-addon-linter`, `home-assistant/addons-example` |
| `ha-websocket-api.md` | Auth with Supervisor token vs long-lived token, `subscribe_entities`/`subscribe_trigger`, `call_service`, entity/device/area registries, `energy/get_prefs`, `recorder/statistics_during_period`, `recorder/import_statistics`, `weather/get_forecasts` action | developers.home-assistant.io (WebSocket API), HA core source for message shapes |
| `mqtt-discovery.md` | Discovery payload format (device-based discovery), availability, retained config, supported platforms for our entities (sensor, binary_sensor, switch, select, number, button), how an app gets broker credentials via Supervisor services API | home-assistant.io/integrations/mqtt, developers docs |
| `competitors.md` | evcc, EMHASS, Predbat: concepts, strengths, limits, licence, which ideas SunCore adopts and what it does differently; coexistence with evcc as charge-point actor | official docs of each project |
| `integrations.md` | Solcast HACS integration (forecast attributes, P10/P50/P90 fields, update cadence), Tibber core integration (price action / attributes), LG ThinQ core integration (`climate` entity features, energy sensors in Wh, absence of a power sensor), Forecast.Solar, Nordpool, Open-Meteo Solar Forecast | home-assistant.io integration pages, integration repositories |
| `kostal-plenticore-modbus.md` | External battery control via Modbus TCP / SunSpec: relevant registers, activation prerequisites, fallback timeout behaviour, write-rate guidance. Register numbers only from the official Kostal Modbus document; otherwise `UNVERIFIED` | official Kostal "Plenticore Modbus Interface" documentation |
| `solver-on-aarch64.md` | `highspy` / HiGHS wheel availability for aarch64 and amd64, Python 3.12/3.13 support, alternatives (OR-Tools, CBC via `mip`, scipy `milp`), expected solve times for a 96–192 step MILP | PyPI, project release notes |

Tooling: web fetch against the listed sources. If the session's network policy
blocks a source, the note is marked `UNVERIFIED` and listed in the PR's open
questions. Nothing is invented from memory.

### WP2 – Architecture decision records (`docs/adr/`)

Template `docs/adr/0000-template.md` (Context, Options considered,
Decision, Consequences, Status, Links). Each ADR states a recommendation; the
maintainer accepts or changes it in PR review. Current leanings, to be
confirmed by WP1:

| ADR | Topic | Leaning |
|---|---|---|
| 0001 | Backend stack | Python 3.12+, asyncio, FastAPI, Pydantic v2, structlog |
| 0002 | Frontend framework and charts | Svelte 5 + Vite + TypeScript, Apache ECharts |
| 0003 | Optimiser / solver | HiGHS via `highspy` (LP first, MILP where needed), heuristic fallback; decision depends on `solver-on-aarch64.md` |
| 0004 | Data storage | SQLite (WAL) under `/data`, schema migrations, downsampled time series, decision log |
| 0005 | Entities in HA | v1: MQTT Discovery with full UI without MQTT; companion integration (HACS) later |
| 0006 | Template and plugin system | Declarative YAML templates validated by JSON Schema; versioned Python plugin interface later |
| 0007 | i18n and translation platform | ICU MessageFormat, key-based strings front- and backend, Hosted Weblate |
| 0008 | Documentation tooling | MkDocs Material on GitHub Pages, English primary, German translation |
| 0009 | Release and versioning | release-please, SemVer, Conventional Commits, two app channels (stable/beta), images on GHCR |
| 0010 | Target platform and architectures | HA OS app on amd64 and aarch64 only; HA connection abstracted for a later standalone container |
| 0011 | Two control layers | 15-min planner and 5–10 s real-time controller, controller may deviate from the plan but never from safety limits |

### WP3 – Architecture document

- `docs/architecture.md` with Mermaid diagrams: module map (BRIEF §3), data flow
  HA → connector → core signals → forecast/tariff → planner → controller →
  safety → actuators → HA, planner/controller/safety sequence, device model
  (asset, capabilities, power model, control kind, constraints), storage layout,
  process model inside the app container (one asyncio process, scheduler,
  API, worker for the solver).

### WP4 – Roadmap

- `docs/roadmap.md`: phases 0–7 from BRIEF §10 with milestones, deliverables,
  acceptance criteria per phase, and the model focus. Linked from README.

### WP5 – Repository scaffold

Directory layout (final names confirmed by ADR-0001/0002):

```
suncore/                 Python package (core, ha, forecast, tariff, planner,
                         controller, safety, devices, api, storage, sim)
frontend/                SPA (placeholder until ADR-0002 is accepted)
addon/suncore/           HA app: config.yaml, Dockerfile, DOCS.md, CHANGELOG.md,
                         icon/logo placeholders, translations/
addon/suncore-beta/      beta channel (shares the build, differs in config)
templates/               device, tariff, forecast, battery templates + schemas
docs/                    BRIEF, STATUS, roadmap, architecture, adr/, research/,
                         plans/
tests/                   pytest, hypothesis
.github/                 workflows, ISSUE_TEMPLATE, PULL_REQUEST_TEMPLATE
.claude/                 agents/, skills/, settings.json
```

Files: `LICENSE` (Apache-2.0), `NOTICE`, `README.md` (draft: what SunCore is,
honest comparison with evcc/EMHASS/Predbat, status badge "pre-alpha", links to
roadmap and BRIEF), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor
Covenant 2.1), `SECURITY.md`, `CHANGELOG.md` (managed by release-please),
`repository.yaml`, `pyproject.toml` (ruff, mypy strict for
core/planner/controller/safety, pytest, coverage), `.pre-commit-config.yaml`,
`.editorconfig`, `.gitattributes`, `.gitignore`, `.github/dependabot.yml`,
issue templates (bug with diagnostics export, feature, device template),
PR template (German sections: Was, Warum, Wie getestet, Risiken, Offene
Fragen, Safety-Review).

Python packages are created empty with `__init__.py`, SPDX headers and one
smoke test per package so pytest, ruff and mypy run on real files.

The frontend is **not** scaffolded in Phase 0 beyond a README placeholder; it
is created in Phase 1 once ADR-0002 is accepted (avoids throwing away a
scaffold if the framework decision changes).

### WP6 – CI workflows (`.github/workflows/`)

| Workflow | Jobs | Must be green in the Phase 0 PR |
|---|---|---|
| `ci.yml` | ruff check + format, mypy, pytest with coverage upload as artifact | yes |
| `addon-lint.yml` | `frenck/action-addon-linter` for `addon/suncore` and `addon/suncore-beta` | yes |
| `docs-lint.yml` | markdownlint, link check for `docs/` (internal links only, external links in warn mode) | yes |
| `codeql.yml` | CodeQL for Python | yes |
| `build.yml` | multi-arch image build via `home-assistant/builder`, push to GHCR on tags only | present, runs in dry-run mode in the PR, real push deferred to Phase 1 |
| `release-please.yml` | release-please on `main` | present, inactive until first release |

### WP7 – Agent team, skills, hooks, CLAUDE.md

- `.claude/agents/`: the eleven agents of BRIEF §11 with `model`, 1–2 sentence
  `description`, tool restrictions (read-only for `architect` and `reviewer`,
  docs-only for `docs-writer`, locale files only for `i18n-sync`), and a system
  prompt that reads `docs/STATUS.md` and relevant ADRs first.
- `.claude/skills/`: `new-device-template`, `new-adapter` (tariff/forecast),
  `verify-ha-api` (checklist with documentation sources), `prepare-release`,
  `sync-translations`, `demo-screenshots`. Phase 0 creates each with its
  `SKILL.md`; skills that depend on code that does not yet exist state the
  phase in which they become usable.
- `.claude/settings.json`: hooks `PostToolUse` (Edit/Write → `ruff format`,
  `prettier` when the file type matches), `PreToolUse` on `git commit` → fast
  tests (`pytest -x -q -m "not slow"`); permission allowlist for pytest, ruff,
  mypy, npm, docker build, git.
- `CLAUDE.md` under 150 lines: purpose in three sentences, directory map,
  commands, rules from BRIEF §0 in short form, pointers to BRIEF, STATUS, ADRs.
  No copy of the brief.

### WP8 – STATUS.md and pull request

- `docs/STATUS.md`: done / open / next steps / known problems / UNVERIFIED
  items.
- Pull request `phase-0/foundation` → `main` with the German summary, ADR list
  with recommendations and the open-questions section. Then stop.

## 3. Execution order and model use

```
WP0 plan ──► WP1 research ──► WP2 ADRs ──► WP3 architecture ──► WP4 roadmap
                                  │
                                  └──► WP5 scaffold ──► WP6 CI ──► WP7 agents ──► WP8 PR
```

WP1 and WP5/WP6 are independent and can be interleaved; WP2 waits for WP1.

Model use (BRIEF §0.8): research notes and scaffold files are routine work
(Sonnet-level subagents where delegation pays off, otherwise inline); ADR
reasoning, architecture document and the final self-review are done at Fable
level in this session. No parallel fan-out larger than two subagents.

## 4. Tests and verification in Phase 0

- CI from WP6 green on the PR head.
- `pytest` runs at least one test per package; `mypy --strict` passes on the
  empty packages; `ruff` clean.
- App linter passes for both app folders.
- Every Mermaid diagram renders (checked with `mmdc` locally or by GitHub
  preview).
- Each research note has a verification line; each ADR has a status and a
  recommendation.
- The agent definitions are loaded by Claude Code without errors (frontmatter
  valid), the hooks run on a test edit.

## 5. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Network policy of the cloud session blocks developer docs or PyPI | research incomplete | mark `UNVERIFIED`, list in the PR, re-verify in a session with access |
| `highspy` has no usable aarch64 wheel or is too slow on a Pi 4 | solver choice changes | ADR-0003 documents the fallback (scipy `milp`/HiGHS bundled in scipy, OR-Tools) and a benchmark task in Phase 4 |
| HA renames or changes app keys again (Add-ons → Apps naming) | scaffold rejected by linter | app linter in CI, research note pinned to a documentation date |
| Kostal official Modbus document not accessible | battery adapter design unverified | adapter stays `UNVERIFIED` until the maintainer provides the document |
| Scope creep into feature code | Phase 0 grows | hard rule: no feature code; placeholders only |
| Budget: Fable usage for every file | cost | Fable only for ADRs, architecture, final review |

## 6. Open questions for the maintainer

Answered in PR review or before WP1 starts; defaults in bold are used if
unanswered.

1. **One PR or two?** Recommendation: **two** – PR A (research, ADRs,
   architecture, roadmap, STATUS) so the decisions can be approved before the
   scaffold is built on them; PR B (scaffold, CI, agent team, CLAUDE.md).
   BRIEF §12.8 says one PR; both work.
2. GHCR image namespace: **`ghcr.io/pxljake/suncore-{arch}`** or an
   organisation?
3. Minimum Python version: **3.12** (HA base images ship 3.13; 3.12 keeps
   wheel availability wider).
4. App slugs: **`suncore`** and **`suncore_beta`**?
5. Is the Kostal Modbus documentation (PDF) available to be placed under
   `docs/research/vendor/` (licence permitting) or should it stay external?
6. Default locale of the UI before the user picks one: **English**, with German
   auto-selected from the HA user language where possible.
7. Documentation site domain: **GitHub Pages at `pxljake.github.io/SunCore`**.

## 7. Out of scope for Phase 0

Feature code of any kind, frontend scaffold, real image publication, Weblate
project creation, HACS companion integration, Lovelace cards.
