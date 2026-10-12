# ADR-0004: Data storage

- **Status:** accepted (maintainer delegated the decision on 2026-10-12)
- **Date:** 2026-10-12
- **Deciders:** maintainer, lead engineer
- **Research:** [`ha-apps.md`](../research/ha-apps.md) (`/data`, `backup_exclude`), [`ha-websocket-api.md`](../research/ha-websocket-api.md) (statistics)

## Context

SunCore stores configuration (versioned, migratable, exportable), time
series (measurements, forecasts, plans, controller actions), a decision log
and learned model parameters. Everything under `/data` is included in HA
backups; large time series should be excludable and reconstructible from HA
long-term statistics (BRIEF §2).

## Options considered

### Option A – SQLite, two database files

- Pros: zero-ops, in HA backups, WAL mode handles concurrent reads from the
  API while the controller writes; a separate time-series file can be
  excluded from backups.
- Cons: no native time-series compression; we downsample ourselves.

### Option B – SQLite for config + Parquet/Arrow files for time series

- Pros: compact columnar storage.
- Cons: pulls in pyarrow (large wheels), awkward for incremental appends.

### Option C – Embedded time-series DB (e.g. DuckDB, QuestDB)

- Pros: analytics performance.
- Cons: memory footprint and wheel availability on aarch64/musl not
  justified for a household's data volume.

### Config as YAML/JSON files instead of a database

- Pros: human-editable.
- Cons: concurrent edits from UI and migrations are harder; export/import
  as JSON is offered anyway from the database.

## Decision

Option A.

- `/data/suncore.db`: configuration documents (JSON per section:
  `site`, `devices`, `tariff`, `forecast`, `battery`, `ui`), each with
  `schema_version`; migration runner applies ordered Python migrations;
  export/import as a single JSON file with version header. Also: decision
  log, learned parameters (forecast bias tables, device power models),
  plan snapshots (compressed JSON), API tokens.
- `/data/timeseries.db` (listed in `backup_exclude`): measurements at
  controller resolution kept 48 h, 1-minute aggregates 30 days, 15-minute
  aggregates unlimited; forecasts versioned by issue time for accuracy
  statistics; a nightly job downsamples and vacuums. On a restore without
  this file SunCore rebuilds 15-minute history from HA
  `recorder/statistics_during_period`.
- Access through `aiosqlite` with a thin repository layer; schema in plain
  SQL files; no ORM.
- Savings/baseline series may additionally be written to HA as external
  statistics (`recorder/import_statistics`, `suncore:*`) so they appear in
  HA's history – optional, Phase 5.

## Consequences

- Positive: simple, portable, backup-friendly; config export is a feature,
  not an afterthought.
- Negative: downsampling logic is ours – covered by tests; database size
  must be monitored (diagnostics page shows it).
- Follow-up: Phase 1 `backend-engineer` implements the config store and
  migrations; Phase 3 adds time series; Phase 5 adds statistics export.

## Recommendation to the maintainer

Accept.
