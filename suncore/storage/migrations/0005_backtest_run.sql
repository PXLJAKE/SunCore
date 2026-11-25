-- SPDX-License-Identifier: Apache-2.0
-- Phase 5 (WP3): results of backtest runs (the engine arrives in PR 5-J; this is the table).
-- Schema 5 cannot be opened by earlier versions (they refuse newer databases): export before
-- updating. suncore.storage.backtests keeps the last BacktestRunStore.KEEP results.

CREATE TABLE backtest_run (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TEXT    NOT NULL,
    finished_at    TEXT,
    status         TEXT    NOT NULL CHECK (status IN ('running', 'done', 'failed', 'cancelled')),
    window_start   TEXT    NOT NULL,
    window_end     TEXT    NOT NULL,
    request        TEXT    NOT NULL CHECK (json_valid(request)),
    schema_version INTEGER NOT NULL,
    result         BLOB
);
CREATE INDEX backtest_run_created_idx ON backtest_run (created_at);
