-- SPDX-License-Identifier: Apache-2.0
-- timeseries.db (ADR-0004, Phase 4 WP3). All timestamps are integer seconds since the UNIX
-- epoch (UTC); a gap is the absence of a row, never a stored zero.

-- Controller resolution, kept 48 h. The primary key is (ts, key): the writes of one flush are
-- consecutive in time, so they land on the last pages of the table instead of one page per key
-- (about 5x fewer WAL bytes per flush on an SD card). Per-key range reads scan the ts range.
CREATE TABLE measurement (
    key   TEXT    NOT NULL,
    ts    INTEGER NOT NULL,
    value REAL    NOT NULL,
    PRIMARY KEY (ts, key)
) WITHOUT ROWID;

-- 1-minute aggregates (ts = start of the minute), kept 30 days; only complete quarter hours.
CREATE TABLE measurement_1m (
    key  TEXT    NOT NULL,
    ts   INTEGER NOT NULL,
    mean REAL    NOT NULL,
    min  REAL    NOT NULL,
    max  REAL    NOT NULL,
    n    INTEGER NOT NULL CHECK (n > 0),
    PRIMARY KEY (ts, key)
) WITHOUT ROWID;

-- 15-minute aggregates (ts = start of the quarter hour), kept for ever. `derived` = 1 marks
-- rows rebuilt from Home Assistant's hourly statistics (a step-hold of the hourly mean).
CREATE TABLE measurement_15m (
    key     TEXT    NOT NULL,
    ts      INTEGER NOT NULL,
    mean    REAL    NOT NULL,
    min     REAL    NOT NULL,
    max     REAL    NOT NULL,
    n       INTEGER NOT NULL CHECK (n > 0),
    derived INTEGER NOT NULL DEFAULT 0 CHECK (derived IN (0, 1)),
    PRIMARY KEY (key, ts)
) WITHOUT ROWID;

-- Forecasts versioned by issue time. p10/p50/p90 are NULL where the value is unknown;
-- a price-like single value is stored in p50 (kind = 'price').
CREATE TABLE forecast (
    source_id  TEXT    NOT NULL,
    kind       TEXT    NOT NULL CHECK (kind IN ('pv', 'load', 'price')),
    issued_at  INTEGER NOT NULL,
    slot_start INTEGER NOT NULL,
    step_s     INTEGER NOT NULL CHECK (step_s > 0),
    unit       TEXT    NOT NULL,
    p10        REAL,
    p50        REAL,
    p90        REAL,
    PRIMARY KEY (source_id, kind, issued_at, slot_start)
) WITHOUT ROWID;
CREATE INDEX forecast_slot_idx ON forecast (source_id, kind, slot_start);

-- Prices by slot; a later store of the same time span replaces the earlier rows.
CREATE TABLE price (
    source_id    TEXT    NOT NULL,
    slot_start   INTEGER NOT NULL,
    step_s       INTEGER NOT NULL CHECK (step_s > 0),
    includes     TEXT    NOT NULL CHECK (includes IN ('spot_only', 'all_in')),
    import_price REAL,
    export_price REAL,
    PRIMARY KEY (source_id, slot_start)
) WITHOUT ROWID;
