# SPDX-License-Identifier: Apache-2.0
"""In-memory ring of the newest warning-and-above log records, for the diagnostics export.

A stdlib handler on the root logger keeps the last :data:`LOG_RING_SIZE` records of level
``WARNING`` and above as small dictionaries (timestamp, level, logger, event, the keyword fields
and the last line of an exception). Nothing is written to disk. The ring stores what the logging
chain already redacted (structlog events pass ``redact_processor`` first); the diagnostics
builder redacts and pseudonymises again on export, so a record from a foreign library is
covered as well.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

LOG_RING_SIZE: Final = 500
"""How many warning+ records are kept (BRIEF §5 diagnostics: the last 500 lines)."""
_MAX_FIELD_CHARS: Final = 400
_SKIP_KEYS: Final = frozenset(
    {"event", "level", "logger", "timestamp", "exception", "stack", "exc_info", "_record"}
)


@dataclass(frozen=True, slots=True)
class LogEntry:
    """One kept log record."""

    ts: datetime
    level: str
    logger: str
    event: str
    fields: Mapping[str, str | int | float | bool | None]
    exception: str | None
    """The last line of the traceback (type and message), if there was one."""


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_FIELD_CHARS else text[:_MAX_FIELD_CHARS] + "..."


def _scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, bool | int | float):
        return value
    return _clip(str(value))


def _last_line(text: str) -> str | None:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return _clip(lines[-1].strip()) if lines else None


class LogRing(logging.Handler):
    """Keeps the newest records of level WARNING and above."""

    def __init__(self, size: int = LOG_RING_SIZE) -> None:
        super().__init__(level=logging.WARNING)
        self._ring: deque[LogEntry] = deque(maxlen=size)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._ring.append(self._entry(record))
        except Exception:  # a logging handler must never raise
            self.handleError(record)

    def _entry(self, record: logging.LogRecord) -> LogEntry:
        ts = datetime.fromtimestamp(record.created, UTC)
        level = record.levelname.lower()
        if isinstance(record.msg, dict):  # structlog's ProcessorFormatter hand-over
            event_dict: Mapping[str, Any] = record.msg
            event = _clip(str(event_dict.get("event", "")))
            fields = {k: _scalar(v) for k, v in event_dict.items() if k not in _SKIP_KEYS}
            raw_exc = event_dict.get("exception")
            exception = _last_line(raw_exc) if isinstance(raw_exc, str) else None
            logger = str(event_dict.get("logger", record.name))
        else:
            event = _clip(record.getMessage())
            fields = {}
            exception = None
            logger = record.name
        if exception is None and record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            exception = _clip(f"{type(exc).__name__}: {exc}")
        return LogEntry(ts, level, logger, event, fields, exception)

    def entries(self, limit: int = LOG_RING_SIZE) -> list[LogEntry]:
        """The kept records, oldest first, at most ``limit`` (the newest ones)."""
        items = list(self._ring)
        return items[-limit:] if limit < len(items) else items

    def clear(self) -> None:
        self._ring.clear()


LOG_RING: Final = LogRing()
"""The process-wide ring; :func:`suncore.app.logconfig.configure_logging` attaches it."""
