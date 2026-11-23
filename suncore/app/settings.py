# SPDX-License-Identifier: Apache-2.0
"""Application settings: Supervisor options file, then environment, then defaults.

Precedence (highest first): explicit keyword arguments, environment variables
(``SUNCORE_<FIELD>``; ``SUNCORE_DEMO`` is a short alias of ``SUNCORE_DEMO_MODE``),
the app options file (``/data/options.json``, written by the Supervisor from the
user's app configuration), field defaults.

The Supervisor token is deliberately *not* a setting: it is read from the
environment at the point of use (``suncore.ha.transport``) and never stored in an
object that could be logged.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import time
from pathlib import Path
from typing import Any, ClassVar, Final, Literal

from pydantic import Field
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

DEFAULT_OPTIONS_PATH: Final = Path("/data/options.json")
ENV_OPTIONS_PATH: Final = "SUNCORE_OPTIONS_PATH"
ENV_DEMO_ALIAS: Final = "SUNCORE_DEMO"
ENV_SUPERVISOR_TOKEN: Final = "SUPERVISOR_TOKEN"
SUPERVISOR_INGRESS_PEER: Final = "172.30.32.2"
"""Only peer Supervisor's ingress proxy uses (docs/research/ha-apps.md, Ingress)."""


class SettingsError(ValueError):
    """The options file exists but cannot be used."""


def default_static_dir() -> Path:
    """``frontend/dist`` next to the package (source checkout and image layout)."""
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


def default_templates_dir() -> Path:
    """``templates/`` next to the package (source checkout and image layout)."""
    return Path(__file__).resolve().parents[2] / "templates"


class Settings(BaseSettings):
    """Runtime settings of the SunCore process."""

    model_config = SettingsConfigDict(env_prefix="SUNCORE_", extra="ignore", frozen=True)

    log_level: Literal["debug", "info", "warning", "error"] = "info"
    controller_interval_s: int = Field(default=5, ge=2, le=60)
    demo_mode: bool = False
    demo_speed: float = Field(default=60.0, gt=0)
    """Scenario acceleration in demo mode (60 = one 60 s step per real second)."""
    demo_start_time: time = time(10, 0)
    """Time of day (scenario-local) at which the demo starts (env SUNCORE_DEMO_START_TIME)."""
    demo_battery_control: bool = False
    """Demo only (env SUNCORE_DEMO_BATTERY_CONTROL): the simulated house gets a controllable
    inverter and the demo stores a ``generic.battery`` adapter, so the UI can show the battery
    card (used by the Playwright run and the screenshots). Off = the battery is observe only."""
    data_dir: Path = Path("/data")
    port: int = Field(default=8099, ge=1, le=65535)
    host: str = "0.0.0.0"
    allow_any_peer: bool = False
    """Development only: disable the Supervisor peer check."""
    max_import_bytes: int = Field(default=1024 * 1024, gt=0)
    static_dir: Path = Field(default_factory=default_static_dir)
    templates_dir: Path = Field(default_factory=default_templates_dir)
    """Built-in templates; user templates are read from ``<data_dir>/templates`` (not in demo)."""

    tariff_refresh_interval_s: int = Field(default=3600, ge=60, le=86_400)
    """Spacing of the regular price refresh (Tibber, Nord Pool: once per hour)."""
    tariff_tomorrow_after: time = time(13, 0)
    """Local time from which tomorrow's day-ahead prices are expected (UNVERIFIED)."""
    tariff_tomorrow_poll_s: int = Field(default=900, ge=60, le=86_400)
    """Spacing of the asks between that time and the arrival of tomorrow's prices."""

    planner_budget_s: float = Field(default=8.0, ge=0.5, le=120.0)
    """Time budget of one planner run (ADR-0003); the heuristic plan covers a miss."""
    planner_mip_gap: float = Field(default=0.01, ge=0.0, le=0.5)

    _options_path: ClassVar[Path] = DEFAULT_OPTIONS_PATH

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            _DemoAliasSource(settings_cls),
            _OptionsFileSource(settings_cls, cls._options_path),
        )


class _MappingSource(PydanticBaseSettingsSource):
    """Base for sources that produce a plain dict keyed by field name."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:  # noqa: ARG002
        return None, field_name, False

    def _data(self) -> dict[str, Any]:
        raise NotImplementedError

    def __call__(self) -> dict[str, Any]:
        return self._data()


class _DemoAliasSource(_MappingSource):
    def _data(self) -> dict[str, Any]:
        raw = os.environ.get(ENV_DEMO_ALIAS)
        return {} if raw is None or raw == "" else {"demo_mode": raw}


class _OptionsFileSource(_MappingSource):
    def __init__(self, settings_cls: type[BaseSettings], path: Path) -> None:
        super().__init__(settings_cls)
        self._path = path

    def _data(self) -> dict[str, Any]:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            msg = f"cannot read options file {self._path}: {exc.strerror}"
            raise SettingsError(msg) from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            msg = f"options file {self._path} is not valid JSON: {exc.msg}"
            raise SettingsError(msg) from exc
        if not isinstance(data, dict):
            msg = f"options file {self._path} must contain a JSON object"
            raise SettingsError(msg)
        known = set(Settings.model_fields)
        return {k: v for k, v in data.items() if k in known}


def load_settings(options_path: Path | None = None, **overrides: Any) -> Settings:
    """Build :class:`Settings`.

    ``options_path`` defaults to ``$SUNCORE_OPTIONS_PATH`` or ``/data/options.json``.
    A missing file is fine (development, demo); an unreadable or invalid one raises
    :class:`SettingsError`.
    """
    path = options_path or Path(os.environ.get(ENV_OPTIONS_PATH) or DEFAULT_OPTIONS_PATH)

    class _Loaded(Settings):
        _options_path: ClassVar[Path] = path

    return _Loaded(**overrides)


def under_supervisor(env: Mapping[str, str] | None = None) -> bool:
    """Whether the process runs as a Supervisor app (``SUPERVISOR_TOKEN`` present)."""
    source = os.environ if env is None else env
    return bool(source.get(ENV_SUPERVISOR_TOKEN, "").strip())
