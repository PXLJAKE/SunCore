# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import io
import logging

import structlog

from suncore.ha.redact import (
    MASK,
    RedactingFilter,
    SecretRegistry,
    install_on_handlers,
    make_structlog_processor,
    redact_value,
)

SECRET = "s3cr3t-token-value"


def _registry() -> SecretRegistry:
    reg = SecretRegistry()
    reg.register(SECRET)
    return reg


def test_registered_secret_and_patterns_are_masked() -> None:
    reg = _registry()
    assert SECRET not in reg.redact(f"auth with {SECRET} ok")
    assert "abc.def-123" not in reg.redact("Authorization: Bearer abc.def-123")
    assert reg.redact("sent Bearer abc.def-123") == f"sent Bearer {MASK}"
    assert SECRET not in reg.redact('{"type":"auth","access_token":"other-value"}')
    assert "other-value" not in reg.redact('{"type":"auth","access_token":"other-value"}')
    assert "xyz" not in reg.redact("access_token=xyz&a=1")


def test_short_secrets_are_ignored() -> None:
    reg = SecretRegistry()
    reg.register("ab")
    assert reg.redact("about") == "about"


def test_redact_value_recurses() -> None:
    reg = _registry()
    out = redact_value({"access_token": "x", "nested": [{"msg": f"t={SECRET}"}], "n": 1}, reg)
    assert out["access_token"] == MASK
    assert SECRET not in str(out)
    assert out["n"] == 1


def test_stdlib_filter_masks_message_args_and_traceback() -> None:
    reg = _registry()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    install_on_handlers([handler], reg)
    install_on_handlers([handler], reg)  # idempotent
    assert sum(isinstance(f, RedactingFilter) for f in handler.filters) == 1
    logger = logging.getLogger("test.redact.stdlib")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.info("token %s used", SECRET)
        try:
            raise RuntimeError(f"boom {SECRET}")
        except RuntimeError:
            logger.exception("failed")
    finally:
        logger.removeHandler(handler)
    out = stream.getvalue()
    assert "token" in out
    assert "failed" in out
    assert SECRET not in out


def test_structlog_processor_masks_values_and_keys() -> None:
    reg = _registry()
    stream = io.StringIO()
    logger = structlog.wrap_logger(
        structlog.PrintLogger(stream),
        processors=[make_structlog_processor(reg), structlog.processors.KeyValueRenderer()],
    )
    logger.info("hello", detail=f"url?t={SECRET}", access_token="anything", n=3)
    out = stream.getvalue()
    assert "hello" in out
    assert SECRET not in out
    assert "anything" not in out
    assert "n=3" in out
