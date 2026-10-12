# Architecture decision records

One file per decision, numbered, never edited after acceptance except for
the status line; a new ADR supersedes an old one. Template:
[`0000-template.md`](0000-template.md).

| ADR | Title | Status | Recommendation |
|---|---|---|---|
| [0001](0001-backend-stack.md) | Backend stack | accepted | Python 3.13 image / 3.12 minimum, FastAPI, Pydantic v2, aiohttp, SQLite, Alpine base |
| [0002](0002-frontend-framework-and-charts.md) | Frontend framework and charts | accepted | Svelte 5 + Vite + TS, Apache ECharts |
| [0003](0003-optimizer-solver.md) | Optimiser and solver | accepted | HiGHS via `highspy`, LP first, MILP where needed, heuristic fallback |
| [0004](0004-data-storage.md) | Data storage | accepted | SQLite, config DB + excludable time-series DB |
| [0005](0005-entities-in-home-assistant.md) | Entities in Home Assistant | accepted | MQTT discovery in v1, companion integration later |
| [0006](0006-template-and-plugin-system.md) | Template and plugin system | accepted | YAML templates + generated JSON Schema, Python plugins later |
| [0007](0007-i18n-and-translation-platform.md) | i18n and translation platform | accepted | ICU MessageFormat, JSON catalogues, Hosted Weblate |
| [0008](0008-documentation-tooling.md) | Documentation tooling | accepted | MkDocs Material + static-i18n, GitHub Pages |
| [0009](0009-release-and-versioning.md) | Release and versioning | accepted | release-please, SemVer, stable + beta app, GHCR |
| [0010](0010-target-platform.md) | Target platform | accepted | HA OS app on aarch64/amd64, HA connection abstracted |
| [0011](0011-two-control-layers.md) | Two control layers and safety boundary | accepted | Planner → Controller → Safety (single writer) → Actuators |
