# SPDX-License-Identifier: Apache-2.0
"""System-check item "Home Assistant entities" (wizard ``POST /wizard/check``)."""

from __future__ import annotations

from suncore.core.decisions import Reason
from suncore.core.haentities import BrokerState, HaEntitiesStatus
from suncore.core.wizard import CheckItem

SUBJECT = "ha_entities"


def check_ha_entities(status: HaEntitiesStatus) -> list[CheckItem]:
    """Findings about the MQTT entity surface; none while it is off or still connecting.

    Never an ``error``: nothing but the HA entities depends on it.
    """
    if status.state is BrokerState.CONNECTED:
        return [
            CheckItem(
                "ok",
                Reason(key="wizard.check.mqtt_ok", args={"count": status.entity_count}),
                SUBJECT,
            )
        ]
    if status.state is BrokerState.NO_BROKER:
        return [CheckItem("warn", Reason(key="wizard.check.mqtt_no_broker"), SUBJECT)]
    if status.state is BrokerState.ERROR:
        return [CheckItem("warn", Reason(key="wizard.check.mqtt_error"), SUBJECT)]
    return []
