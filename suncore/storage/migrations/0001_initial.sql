-- SPDX-License-Identifier: Apache-2.0
-- Initial schema (docs/architecture.md section 7). Timestamps are ISO-8601 UTC text.

CREATE TABLE config_doc (
    section        TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    body           TEXT NOT NULL CHECK (json_valid(body)),
    updated_at     TEXT NOT NULL
);

CREATE TABLE decision (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    layer    TEXT NOT NULL,
    kind     TEXT NOT NULL,
    payload  TEXT NOT NULL CHECK (json_valid(payload)),
    reasons  TEXT NOT NULL CHECK (json_valid(reasons))
);

CREATE INDEX decision_ts_idx ON decision (ts);
CREATE INDEX decision_asset_idx ON decision (asset_id, id);

CREATE TABLE learned (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL CHECK (json_valid(value)),
    updated_at TEXT NOT NULL
);
