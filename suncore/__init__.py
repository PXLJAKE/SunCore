# SPDX-License-Identifier: Apache-2.0
"""SunCore - energy management for Home Assistant.

Package layout and responsibilities are described in ``docs/architecture.md``.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("suncore")
except PackageNotFoundError:  # pragma: no cover - source checkout without install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
