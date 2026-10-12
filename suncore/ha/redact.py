# SPDX-License-Identifier: Apache-2.0
"""Keep tokens out of logs.

Two layers: registered secrets (exact values, e.g. the Supervisor token) are replaced
wherever they appear in a string, and well-known field names (``access_token``,
``authorization``, ...) are masked regardless of their value. Provided as a stdlib
``logging.Filter`` (attach to handlers, so records of all loggers are covered) and as a
``structlog`` processor.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Iterable, Mapping, MutableMapping
from typing import Any, Final

MASK: Final = "***"
SENSITIVE_KEYS: Final = frozenset(
    {"access_token", "api_password", "authorization", "password", "refresh_token", "token"}
)
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+")
_JSON_FIELD = re.compile(
    r"""(?ix)(["']?(?:access_token|api_password|authorization|refresh_token)["']?\s*[:=]\s*)
    ("[^"]*"|'[^']*'|[^\s,}&]+)"""
)
_MIN_SECRET_LEN: Final = 4


class SecretRegistry:
    """Set of exact secret values to mask. Thread-safe; values are never logged."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()
        self._lock = threading.Lock()

    def register(self, secret: str) -> None:
        if len(secret) >= _MIN_SECRET_LEN:
            with self._lock:
                self._secrets.add(secret)

    def clear(self) -> None:
        with self._lock:
            self._secrets.clear()

    def redact(self, text: str) -> str:
        with self._lock:
            secrets = sorted(self._secrets, key=len, reverse=True)
        for secret in secrets:
            text = text.replace(secret, MASK)
        text = _BEARER.sub(rf"\1 {MASK}", text)
        return _JSON_FIELD.sub(rf"\1{MASK}", text)


DEFAULT_REGISTRY: Final = SecretRegistry()


def register_secret(secret: str) -> None:
    """Register a secret with the default registry."""
    DEFAULT_REGISTRY.register(secret)


def redact_text(text: str, registry: SecretRegistry = DEFAULT_REGISTRY) -> str:
    return registry.redact(text)


def redact_value(value: Any, registry: SecretRegistry = DEFAULT_REGISTRY) -> Any:
    """Recursively redact strings; mask values under sensitive keys."""
    if isinstance(value, str):
        return registry.redact(value)
    if isinstance(value, Mapping):
        return {
            k: MASK
            if isinstance(k, str) and k.lower() in SENSITIVE_KEYS
            else redact_value(v, registry)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return type(value)(redact_value(v, registry) for v in value)
    return value


class RedactingFilter(logging.Filter):
    """``logging`` filter that redacts message, arguments and exception text."""

    def __init__(self, registry: SecretRegistry = DEFAULT_REGISTRY) -> None:
        super().__init__()
        self._registry = registry

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._registry.redact(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self._registry.redact(record.exc_text)
        if record.stack_info:
            record.stack_info = self._registry.redact(record.stack_info)
        return True


def make_structlog_processor(
    registry: SecretRegistry = DEFAULT_REGISTRY,
) -> Any:
    """Build a structlog processor bound to ``registry``."""

    def processor(
        _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        for key in list(event_dict):
            event_dict[key] = (
                MASK if key.lower() in SENSITIVE_KEYS else redact_value(event_dict[key], registry)
            )
        return event_dict

    return processor


redact_processor = make_structlog_processor()
"""structlog processor bound to the default registry; place it before the renderer."""


def install_on_handlers(
    handlers: Iterable[logging.Handler], registry: SecretRegistry = DEFAULT_REGISTRY
) -> None:
    """Attach a :class:`RedactingFilter` to each handler (idempotent per handler)."""
    for handler in handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter(registry))
