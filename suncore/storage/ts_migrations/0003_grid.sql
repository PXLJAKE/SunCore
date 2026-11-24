-- SPDX-License-Identifier: Apache-2.0
-- Phase 6 (PR 6-B, ADR-0018): grid events and monthly import peaks.
-- Schema 3 of timeseries.db is read by an older version as "no time series" (it refuses a newer
-- file and leaves it untouched): export before updating. Both tables are additive.

-- Island and §14a start/end, new monthly peaks, limit breaches (written from PR 6-F on).
-- `ts` in UNIX seconds (UTC), `value_w` the power that belongs to the event (budget, peak,
-- import), `detail` optional JSON with reason arguments. Kept 400 days.
CREATE TABLE grid_event (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      INTEGER NOT NULL,
    kind    TEXT    NOT NULL CHECK (kind IN (
                'island_start', 'island_end', 'para14a_start', 'para14a_end',
                'para14a_unknown', 'peak_new', 'import_limit_exceeded',
                'export_limit_exceeded')),
    value_w REAL,
    detail  TEXT CHECK (detail IS NULL OR json_valid(detail))
);
CREATE INDEX grid_event_ts_idx ON grid_event (ts);

-- The highest 15-minute mean import per local month (`month` = YYYY-MM in the region's time
-- zone), the basis of peak shaving. Kept for ever (one row per month).
CREATE TABLE grid_peak (
    month      TEXT    PRIMARY KEY,
    peak_w     REAL    NOT NULL CHECK (peak_w >= 0),
    slot_ts    INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
) WITHOUT ROWID;
