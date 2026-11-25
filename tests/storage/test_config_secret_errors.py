# SPDX-License-Identifier: Apache-2.0
"""Security review 1.0, SEC-01: a rejected or corrupt ``mqtt`` section never repeats the
password in an error message, a traceback or the log."""

from __future__ import annotations

import json
import traceback
from typing import Any

import pytest

from suncore.storage import ConfigStore, Database
from suncore.storage.errors import ConfigCorruptError, ImportRejectedError
from suncore.storage.migrations import latest_version

PASSWORD = "hunter2-mqtt-secret"


def _bad_mqtt_bodies() -> list[dict[str, Any]]:
    return [
        # model validator: a password without a user name (the whole body is the input)
        {"schema_version": 1, "broker": "manual", "host": "h", "password": PASSWORD},
        # field validator: too long
        {
            "schema_version": 1,
            "broker": "manual",
            "host": "h",
            "username": "u",
            "password": PASSWORD * 20,
        },
    ]


def _everything(exc: BaseException) -> str:
    return "".join(traceback.format_exception(exc))


@pytest.mark.parametrize("body", _bad_mqtt_bodies())
async def test_rejected_import_does_not_repeat_the_password(
    db: Database, body: dict[str, Any]
) -> None:
    export = {
        "suncore_export": 1,
        "db_schema_version": latest_version(),
        "sections": {"mqtt": {"schema_version": 1, "body": body}},
    }
    with pytest.raises(ImportRejectedError) as caught:
        await ConfigStore(db).import_json(export)
    text = _everything(caught.value)
    assert PASSWORD not in text
    assert "mqtt" in text  # still says where the problem is
    assert "password" in str(caught.value)


@pytest.mark.parametrize("body", _bad_mqtt_bodies())
async def test_corrupt_stored_section_does_not_repeat_the_password(
    db: Database, body: dict[str, Any]
) -> None:
    await db.execute(
        "INSERT INTO config_doc (section, schema_version, body, updated_at) VALUES (?, ?, ?, ?)",
        ("mqtt", 1, json.dumps(body), "2026-11-25T00:00:00+00:00"),
    )
    with pytest.raises(ConfigCorruptError) as caught:
        await ConfigStore(db).get("mqtt")
    assert PASSWORD not in _everything(caught.value)
