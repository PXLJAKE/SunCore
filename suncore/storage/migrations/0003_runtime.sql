-- SPDX-License-Identifier: Apache-2.0
-- Phase 3 (WP4): safety runtime, emergency stop / identity record, learned power models.
-- Schema 3 cannot be opened by 0.3.x (refuses newer databases): export before updating.
-- decision(ts) is already indexed by decision_ts_idx (0001); retention uses it.

CREATE TABLE device_runtime (
    device_id  TEXT PRIMARY KEY,
    payload    TEXT NOT NULL CHECK (json_valid(payload)),
    updated_at TEXT NOT NULL
);

CREATE TABLE safety_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE power_model_state (
    device_id  TEXT PRIMARY KEY,
    payload    TEXT NOT NULL CHECK (json_valid(payload)),
    updated_at TEXT NOT NULL
);
