# SPDX-License-Identifier: Apache-2.0
"""Composition root: wires settings, storage, Home Assistant, safety gate and the API.

One asyncio process. Uvicorn runs in-process; SIGTERM/SIGINT stop the server first,
then the HA connection and the database are closed (``Runtime.stop``).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import tempfile
from collections.abc import Callable, Generator, Mapping
from datetime import UTC, datetime
from pathlib import Path

import structlog
import uvicorn

from suncore import __version__
from suncore.api import create_app
from suncore.app.logconfig import configure_logging
from suncore.app.runtime import DB_FILENAME, Runtime
from suncore.app.settings import ENV_SUPERVISOR_TOKEN, Settings, load_settings, under_supervisor
from suncore.ha import (
    EntityCache,
    HAConnection,
    WebSocketConnection,
    transport_from_env,
)
from suncore.ha.redact import register_secret
from suncore.safety.gate import Clock, SafetyGate
from suncore.storage import ConfigStore, Database, DecisionLog

log = structlog.get_logger(__name__)

DEMO_SCENARIO = "summer_day"


def utc_now() -> datetime:
    return datetime.now(UTC)


async def build_runtime(
    settings: Settings,
    *,
    env: Mapping[str, str] | None = None,
    connection: HAConnection | None = None,
    clock: Clock = utc_now,
) -> Runtime:
    """Open the database and create the services. Nothing is connected yet.

    ``connection`` lets tests inject a connection (any ``HAConnection``); without it demo
    mode builds a ``FakeHAConnection`` and otherwise the transport is chosen from ``env``.
    """
    env = os.environ if env is None else env
    demo = settings.demo_mode
    tempdir: tempfile.TemporaryDirectory[str] | None = None
    if demo:
        # The demo never touches /data: a throw-away database holds the demo site.
        tempdir = tempfile.TemporaryDirectory(prefix="suncore-demo-")
        data_dir = Path(tempdir.name)
    else:
        data_dir = settings.data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
    db = await Database.open(data_dir / DB_FILENAME)
    try:
        config = ConfigStore(db)
        if demo:
            from suncore.sim.fake_ha import demo_site_config  # noqa: PLC0415

            await config.put_site(demo_site_config())
        if connection is None:
            connection = _make_connection(settings, env, clock)
        decision_log = DecisionLog(db)
        gate = SafetyGate(clock=clock, sink=decision_log.append)
        cache = EntityCache(clock=clock)
    except BaseException:
        await db.close()
        if tempdir is not None:
            tempdir.cleanup()
        raise
    runtime = Runtime(
        settings=settings,
        db=db,
        config=config,
        decision_log=decision_log,
        connection=connection,
        cache=cache,
        gate=gate,
        clock=clock,
        demo=demo,
        supervised=under_supervisor(env),
    )
    runtime._tempdir = tempdir
    return runtime


def _make_connection(settings: Settings, env: Mapping[str, str], clock: Clock) -> HAConnection:
    if settings.demo_mode:
        from suncore.sim.fake_ha import FakeHAConnection  # noqa: PLC0415
        from suncore.sim.scenario import load_scenario  # noqa: PLC0415

        scenario = load_scenario(DEMO_SCENARIO)
        start = scenario.start
        begin = start.replace(
            hour=settings.demo_start_time.hour,
            minute=settings.demo_start_time.minute,
            second=0,
            microsecond=0,
        )
        offset_s = (begin - start).total_seconds() % scenario.duration_s
        return FakeHAConnection(
            scenario, clock=clock, speed=settings.demo_speed, start_offset_s=offset_s
        )
    return WebSocketConnection(transport_from_env(env))


class _Server(uvicorn.Server):
    """Uvicorn without its own signal capture: the composition root owns signals."""

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        yield


def make_server(runtime: Runtime) -> uvicorn.Server:
    app = create_app(runtime)
    config = uvicorn.Config(
        app,
        host=runtime.settings.host,
        port=runtime.settings.port,
        log_config=None,  # our logging setup stays in charge
        access_log=False,
        server_header=False,
        lifespan="off",
    )
    return _Server(config)


async def serve(
    runtime: Runtime,
    *,
    stop: asyncio.Event | None = None,
    install_signals: bool = True,
    server_factory: Callable[[Runtime], uvicorn.Server] = make_server,
) -> None:
    """Run until ``stop`` is set or SIGTERM/SIGINT arrives, then shut down in order."""
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    if install_signals:
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, stop.set)
                installed.append(sig)
    server = server_factory(runtime)
    try:
        await runtime.start()
        server_task = asyncio.create_task(server.serve(), name="suncore-http")
        stop_task = asyncio.create_task(stop.wait(), name="suncore-stop")
        done, _ = await asyncio.wait({server_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        server.should_exit = True
        await server_task
        stop_task.cancel()
        if server_task in done and not stop.is_set():
            log.error("http.server_exited")
    finally:
        await runtime.stop()
        for sig in installed:
            loop.remove_signal_handler(sig)


async def _amain(settings: Settings) -> None:
    log.info(
        "app.starting",
        version=__version__,
        demo=settings.demo_mode,
        port=settings.port,
        supervised=under_supervisor(),
    )
    runtime = await build_runtime(settings)
    await serve(runtime)
    log.info("app.stopped")


def run(argv: list[str] | None = None) -> int:  # noqa: ARG001
    """Entry point of the long-running process; returns the exit code."""
    token = os.environ.get(ENV_SUPERVISOR_TOKEN, "").strip()
    if token:
        register_secret(token)
    try:
        settings = load_settings()
    except ValueError as exc:
        configure_logging("info")
        log.error("app.settings_invalid", error=str(exc))
        return 2
    configure_logging(settings.log_level)
    try:
        asyncio.run(_amain(settings))
    except Exception:
        log.exception("app.crashed")
        return 1
    return 0
