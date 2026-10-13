-- SPDX-License-Identifier: Apache-2.0
-- Previous bodies of config sections (last N per section, pruned by ConfigStore).

CREATE TABLE config_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    section        TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    body           TEXT NOT NULL CHECK (json_valid(body)),
    replaced_at    TEXT NOT NULL
);

CREATE INDEX config_history_section_idx ON config_history (section, id);
