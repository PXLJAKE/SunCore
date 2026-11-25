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
| [0005](0005-entities-in-home-assistant.md) | Entities in Home Assistant | accepted (companion timing superseded by ADR-0021) | MQTT discovery in v1, companion integration later |
| [0006](0006-template-and-plugin-system.md) | Template and plugin system | accepted | YAML templates + generated JSON Schema, Python plugins later |
| [0007](0007-i18n-and-translation-platform.md) | i18n and translation platform | accepted (Weblate component list superseded by ADR-0020) | ICU MessageFormat, JSON catalogues, Hosted Weblate |
| [0008](0008-documentation-tooling.md) | Documentation tooling | superseded by ADR-0020 | MkDocs Material + static-i18n, GitHub Pages |
| [0009](0009-release-and-versioning.md) | Release and versioning | accepted | release-please, SemVer, stable + beta app, GHCR |
| [0010](0010-target-platform.md) | Target platform | accepted | HA OS app on aarch64/amd64, HA connection abstracted |
| [0011](0011-two-control-layers.md) | Two control layers and safety boundary | accepted (addendum 2026-10-16: new action kinds rejected until their invariants exist) | Planner → Controller → Safety (single writer) → Actuators |
| [0012](0012-template-schema-v1.md) | Template schema v1 – review before freeze | accepted | Required changes (matcher regex/lists, same-device role, `ControlSpec`, series source, capability enum) before freeze; `battery` kind added later under the v1 compatibility policy |
| [0013](0013-battery-as-controlled-asset.md) | Battery as a controlled asset | accepted | `BatteryAction` as limits (not set-points); one reserve source of truth; planned grid charging as the only bounded exception to "no SunCore-caused import"; adapter heartbeat with hardware fallback; battery identity in the fingerprint; prerequisite of Phase 4 WP10 |
| [0014](0014-analytics-baselines-backtesting.md) | Analytics, baselines and backtesting | accepted | Pure `analysis/` package, two baselines, frozen daily `day_summary`, backtest replays the real planner without controller/gate, chart bundle budget |
| [0015](0015-parameter-registry-expert-mode.md) | Parameter registry and expert mode | accepted | `ParamSpec` registry feeds expert UI, validation and generated docs; expert mode never weakens safety, hard limits, config history |
| [0016](0016-ha-surface-mqtt-and-notifications.md) | HA surface – MQTT entities, events and notifications | proposed (default applies) | Device-based discovery with install id, configs on birth, commands only through the services layer, mode raise from HA opt-in, backend ICU renderer, notifications via allowlisted HA actions; works without a broker |
| [0017](0017-flexible-loads-ev-heat-pump-programs.md) | Flexible loads – EV, heat pump, shiftable programs | proposed (default applies) | New `charge` and `loadpoint` (evcc) actions; heat pump reuses `step`/`setpoint` with an `sg_ready` control (state 1 never); program jobs never interrupted, plug baseline on; bounded import exception for user-demanded or plan-permitted demand |
| [0018](0018-grid-limits-14a-export-backup.md) | Grid limits – peak shaving, export limit, §14a, backup power | proposed (default applies; addendum 2026-11-25: verification procedure) | One resolver in `core/grid.py`; shedding order; export chain devices → battery → `inverter_limit` (assisted until verified); §14a respected, not certified; island from a mapped entity only; verification by a user-confirmed procedure that waives only X3 inside a short window |
| [0019](0019-ui-design-system.md) | UI design system – tokens over the HA theme, components, navigation | proposed (default applies) | Semantic `--sc-*` tokens derived from HA theme variables, own small component set, four destinations (Now, Plan, Analysis, Settings), chart theme from tokens, no new dependency |
| [0020](0020-docs-site-and-translation.md) | Documentation site and translation workflow | proposed (default applies) | MkDocs 1.6 + Material 9.8 + static-i18n pinned, Zensical review before 2027-05-05; site root `docs/user`; Weblate for UI and app options only, backend generated, docs by PR; supersedes ADR-0008 and the component list of ADR-0007 |
| [0021](0021-ha-dashboard-surface-for-1-0.md) | HA dashboard surface for 1.0 | proposed (default applies) | Dashboard recipe from built-in cards on the MQTT entities; custom card and companion integration after 1.0 |
