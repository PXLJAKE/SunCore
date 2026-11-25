-- SPDX-License-Identifier: Apache-2.0
-- Phase 6 (PR 6-B, plan §0 decision 16, ADR-0017): runtime state of flexible loads - the EV
-- mode and target per charger and the program jobs. It changes many times a day, so it is not
-- configuration (no config history). Schema 6 cannot be opened by earlier versions (they refuse
-- newer databases): export before updating. Additive: dropping the table loses only the current
-- EV modes (back to the default `pv`) and armed jobs.
-- `payload` is the JSON of suncore.core.flex.EvState (kind 'ev', key 'ev:<device_id>') or
-- ProgramJob (kind 'job', key 'job:<job id>'). suncore.storage.flex prunes finished jobs.

CREATE TABLE flex_state (
    key        TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('ev', 'job')),
    device_id  TEXT NOT NULL,
    final      INTEGER NOT NULL DEFAULT 0 CHECK (final IN (0, 1)),
    payload    TEXT NOT NULL CHECK (json_valid(payload)),
    updated_at TEXT NOT NULL
) WITHOUT ROWID;
CREATE INDEX flex_state_device_idx ON flex_state (device_id, kind);
