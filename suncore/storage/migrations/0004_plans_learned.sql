-- SPDX-License-Identifier: Apache-2.0
-- Phase 4 (WP3): plan snapshots and learned parameters.
-- Schema 4 cannot be opened by earlier versions (they refuse newer databases): export before
-- updating. The `learned` table of 0001 stays untouched (unused since Phase 1).

-- Compressed JSON of a computed plan (zlib); retention in suncore.storage.plans.
CREATE TABLE plan_snapshot (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TEXT    NOT NULL,
    source         TEXT    NOT NULL,
    schema_version INTEGER NOT NULL,
    payload        BLOB    NOT NULL
);
CREATE INDEX plan_snapshot_created_idx ON plan_snapshot (created_at);

-- Learned parameters: forecast_bias, net_error, load_profile, blend_weights, ml_state, ...
-- The owner of a namespace defines its payload and migrates it by schema_version.
CREATE TABLE learned_param (
    namespace      TEXT    NOT NULL,
    key            TEXT    NOT NULL,
    payload        TEXT    NOT NULL CHECK (json_valid(payload)),
    schema_version INTEGER NOT NULL,
    updated_at     TEXT    NOT NULL,
    PRIMARY KEY (namespace, key)
);
