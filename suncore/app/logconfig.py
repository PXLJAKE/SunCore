# SPDX-License-Identifier: Apache-2.0
"""Logging setup: structlog JSON on stdout, stdlib records routed through it, secrets redacted.

Redaction is a structlog processor in both chains (native structlog events and foreign
stdlib records, including rendered exceptions). The stdlib ``RedactingFilter`` is not
installed: it rewrites ``record.msg`` to a string, which breaks structlog's formatter.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from suncore.ha.redact import redact_processor

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def configure_logging(level: str = "info", *, stream: Any = None) -> None:
    """Configure structlog and the root logger (idempotent)."""
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    structlog.configure(
        processors=[
            *shared,
            redact_processor,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                redact_processor,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(_LEVELS.get(level, logging.INFO))
