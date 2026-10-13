# SPDX-License-Identifier: Apache-2.0
"""Process level: graceful shutdown, exit code, logging hygiene."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
import structlog

from suncore.app.logconfig import configure_logging
from suncore.app.main import build_runtime, serve
from suncore.ha.redact import DEFAULT_REGISTRY, register_secret
from tests.app.conftest import RecordingConnection, demo_settings


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def test_serve_stops_cleanly_on_event(tmp_path: Path) -> None:
    port = _free_port()
    settings = demo_settings(tmp_path, port=port, host="127.0.0.1")
    conn = RecordingConnection(speed=6000.0)
    runtime = await build_runtime(settings, env={}, connection=conn)
    stop = asyncio.Event()
    task = asyncio.create_task(serve(runtime, stop=stop, install_signals=False))
    loop = asyncio.get_running_loop()

    def fetch() -> int:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            return int(r.status)

    status = None
    for _ in range(100):
        try:
            status = await loop.run_in_executor(None, fetch)
            break
        except OSError:
            await asyncio.sleep(0.05)
    assert status == 200
    stop.set()
    await asyncio.wait_for(task, 5)
    assert conn.state.value == "closed"
    with pytest.raises(Exception, match=r"closed|Cannot operate"):
        await runtime.db.fetchone("SELECT 1")


def test_sigterm_exits_zero(tmp_path: Path) -> None:
    """``python -m suncore`` in demo mode: /health answers, SIGTERM -> exit code 0."""
    port = _free_port()
    env = {
        **os.environ,
        "SUNCORE_DEMO": "1",
        "SUNCORE_PORT": str(port),
        "SUNCORE_HOST": "127.0.0.1",
        "SUNCORE_OPTIONS_PATH": str(tmp_path / "none.json"),
        "SUNCORE_STATIC_DIR": str(tmp_path / "no-dist"),
        "SUPERVISOR_TOKEN": "",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "suncore"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        ok = False
        while time.monotonic() < deadline and proc.poll() is None:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
                    ok = r.status == 200
                    break
            except OSError:
                time.sleep(0.1)
        assert ok, "app did not become healthy"
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    assert proc.returncode == 0
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    events = [line["event"] for line in lines]
    assert "app.starting" in events
    assert "app.stopped" in events
    assert all("timestamp" in line and "level" in line for line in lines)


def test_logging_is_json_and_redacts_secrets() -> None:
    stream = io.StringIO()
    register_secret("super-secret-token-123")
    try:
        configure_logging("info", stream=stream)
        structlog.get_logger("t").info("evt", url="ws://x?t=super-secret-token-123", token="abc")
        logging.getLogger("stdlib").warning("leak super-secret-token-123 here")
    finally:
        DEFAULT_REGISTRY.clear()
        logging.getLogger().handlers.clear()
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(records) == 2
    assert "super-secret-token-123" not in stream.getvalue()
    assert records[0]["event"] == "evt"
    assert records[0]["token"] == "***"
    assert records[1]["level"] == "warning"


def test_run_returns_2_on_invalid_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from suncore.app.main import run  # noqa: PLC0415

    bad = tmp_path / "options.json"
    bad.write_text("{nope")
    monkeypatch.setenv("SUNCORE_OPTIONS_PATH", str(bad))
    try:
        assert run() == 2
    finally:
        logging.getLogger().handlers.clear()
