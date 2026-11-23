-- SPDX-License-Identifier: Apache-2.0
-- Phase 5 (WP3, ADR-0014): frozen daily summaries and the daily forecast accuracy.
-- Schema 2 of timeseries.db is read by an older version as "no time series" (it refuses a newer
-- file and leaves it untouched): export before updating. Both tables are additive and can be
-- rebuilt from measurement_15m, price and forecast as far as those are still kept.

-- One row per local day, computed once by the nightly job with suncore.analysis and frozen:
-- a later tariff change does not alter it (`tariff_fingerprint` names the configuration the
-- cost was computed under). Energies in Wh, costs in the region's currency. The cost columns
-- are NULL when no slot of the day had a stored price; they cover `priced_slots` of `slots`.
-- Savings are derived: vs. no PV = baseline_no_pv_cost - cost_net, vs. PV only (what SunCore
-- adds) = baseline_pv_only_cost - cost_net. `day` is the local date; start_ts/end_ts are the
-- absolute bounds [start, end) (23 or 25 hours on a DST day).
CREATE TABLE day_summary (
    day                    TEXT    PRIMARY KEY,
    start_ts               INTEGER NOT NULL,
    end_ts                 INTEGER NOT NULL CHECK (end_ts > start_ts),
    tz                     TEXT    NOT NULL,
    slots                  INTEGER NOT NULL CHECK (slots >= 0),
    coverage               REAL    NOT NULL CHECK (coverage >= 0 AND coverage <= 1),
    complete               INTEGER NOT NULL CHECK (complete IN (0, 1)),
    derived_slots          INTEGER NOT NULL DEFAULT 0 CHECK (derived_slots >= 0),
    pv_wh                  REAL    NOT NULL,
    house_wh               REAL    NOT NULL,
    import_wh              REAL    NOT NULL,
    export_wh              REAL    NOT NULL,
    charge_wh              REAL    NOT NULL,
    discharge_wh           REAL    NOT NULL,
    pv_to_house_wh         REAL    NOT NULL,
    pv_to_battery_wh       REAL    NOT NULL,
    pv_to_grid_wh          REAL    NOT NULL,
    battery_to_house_wh    REAL    NOT NULL,
    battery_to_grid_wh     REAL    NOT NULL,
    grid_to_house_wh       REAL    NOT NULL,
    grid_to_battery_wh     REAL    NOT NULL,
    residual_wh            REAL    NOT NULL,
    autarky                REAL,
    self_consumption       REAL,
    equivalent_full_cycles REAL,
    priced_slots           INTEGER NOT NULL DEFAULT 0 CHECK (priced_slots >= 0),
    import_cost            REAL,
    export_revenue         REAL,
    cost_net               REAL,
    baseline_no_pv_cost    REAL,
    baseline_pv_only_cost  REAL,
    currency               TEXT,
    tariff_fingerprint     TEXT,
    computed_at            INTEGER NOT NULL
) WITHOUT ROWID;

-- Forecast skill per source, kind, local day and horizon bucket (0_6h, 6_24h, 24_48h), written
-- by the nightly accuracy job (error = actual - forecast, W; PV counts daylight quarter hours
-- only, see suncore.forecast.accuracy). Kept for ever (a few rows per day and source).
CREATE TABLE accuracy_day (
    source_id   TEXT    NOT NULL,
    kind        TEXT    NOT NULL CHECK (kind IN ('pv', 'load')),
    day         TEXT    NOT NULL,
    horizon     TEXT    NOT NULL,
    mae_w       REAL    NOT NULL CHECK (mae_w >= 0),
    bias_w      REAL    NOT NULL,
    n           INTEGER NOT NULL CHECK (n > 0),
    computed_at INTEGER NOT NULL,
    PRIMARY KEY (source_id, kind, day, horizon)
) WITHOUT ROWID;
CREATE INDEX accuracy_day_day_idx ON accuracy_day (day);
