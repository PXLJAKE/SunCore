#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""HTTP probe for the 0.2.0 -> candidate upgrade job (scripts/upgrade_test.sh, WP13).

Standard library only: it runs inside the app containers (`docker exec`), where the API is
loopback-only. It talks the 0.2.0 API (`/health`, `/api/v1/info`, `/api/v1/config/site`,
`/api/v1/config/export`, `/api/v1/decisions`), which the candidate keeps.

  upgrade_probe.py wait   [--base URL] [--timeout S]
  upgrade_probe.py seed   [--base URL]                 # prints the snapshot as JSON
  upgrade_probe.py verify --seed FILE [--base URL]     # exit 1 with a message on any mismatch

`seed` writes the site through `PUT /api/v1/config/site` (nothing is hand-made on disk) and
records what the released app reports. `verify` runs against the candidate on the same volume
and compares: the stored site, the export, the decision log and the mode (still shadow).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE = "http://127.0.0.1:8099"

# A 0.2.0 site document (schema 1: `pv` is a list of references).
SITE_V1: dict[str, Any] = {
    "schema_version": 1,
    "grid": {"power": {"entity_id": "sensor.ci_grid_power", "invert_sign": False}},
    "pv": [{"entity_id": "sensor.ci_pv_power", "invert_sign": False}],
    "battery": {
        "power": {"entity_id": "sensor.ci_battery_power", "invert_sign": False},
        "soc": {"entity_id": "sensor.ci_battery_soc", "invert_sign": False},
    },
    "house": {"entity_id": "sensor.ci_house_power", "invert_sign": False},
}


def call(base: str, method: str, path: str, body: object | None = None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "X-SunCore-Request": "1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as err:  # /health answers 503 while HA is unreachable
        status, raw = err.code, err.read()
    return status, json.loads(raw) if raw else None


def contains(new: Any, old: Any) -> bool:
    """`new` holds everything in `old` (dicts may have gained keys, lists keep their length)."""
    if isinstance(old, dict):
        return isinstance(new, dict) and all(
            k in new and contains(new[k], v) for k, v in old.items()
        )
    if isinstance(old, list):
        return (
            isinstance(new, list)
            and len(new) == len(old)
            and all(contains(n, o) for n, o in zip(new, old, strict=True))
        )
    return bool(new == old)


def wait(base: str, timeout_s: float) -> dict[str, Any]:
    """Until `/health` answers with its JSON body (200, or 503 without Home Assistant)."""
    deadline = time.monotonic() + timeout_s
    last = "no answer"
    while time.monotonic() < deadline:
        try:
            status, body = call(base, "GET", "/health")
            if status in (200, 503) and isinstance(body, dict) and "db_ok" in body:
                return {"status": status, **body}
            last = f"{status} {body}"
        except (OSError, ValueError) as err:
            last = str(err)
        time.sleep(1)
    raise SystemExit(f"/health did not answer within {timeout_s:.0f} s ({last})")


def seed(base: str) -> dict[str, Any]:
    health = wait(base, 60)
    if not health["db_ok"]:
        raise SystemExit(f"database not ok: {health}")
    status, body = call(base, "PUT", "/api/v1/config/site", SITE_V1)
    if status != 200:
        raise SystemExit(f"PUT /api/v1/config/site -> {status} {body}")
    _, info = call(base, "GET", "/api/v1/info")
    _, site = call(base, "GET", "/api/v1/config/site")
    _, export = call(base, "GET", "/api/v1/config/export")
    _, decisions = call(base, "GET", "/api/v1/decisions?limit=1000")
    if info["mode"] != "shadow":
        raise SystemExit(f"the released app is not in shadow mode: {info}")
    return {
        "health": health,
        "info": info,
        "site": site,
        "export": export,
        "decisions": len(decisions),
    }


def site_problems(site: dict[str, Any] | None, old: dict[str, Any]) -> list[str]:
    """What changed in the stored site; schema 2 names the PV arrays, nothing else may move."""
    if site is None:
        return ["GET /config/site answered nothing"]
    problems = []
    if not contains(site["grid"], old["grid"]):
        problems.append(f"grid changed: {site['grid']}")
    if [a["power"]["entity_id"] for a in site["pv"]] != [r["entity_id"] for r in old["pv"]]:
        problems.append(f"pv changed: {site['pv']}")
    if not contains(site["battery"], old["battery"]):
        problems.append(f"battery changed: {site['battery']}")
    if not contains(site["house"], old["house"]):
        problems.append(f"house changed: {site['house']}")
    return problems


def verify(base: str, seed_file: str) -> dict[str, Any]:
    seeded = json.loads(Path(seed_file).read_text(encoding="utf-8"))
    health = wait(base, 90)
    problems: list[str] = []
    if not health["db_ok"]:
        problems.append(f"db_ok is false: {health}")
    _, info = call(base, "GET", "/api/v1/info")
    if info["mode"] != "shadow":
        problems.append(f"mode is {info['mode']!r}, expected shadow")
    if info["demo"]:
        problems.append("the candidate runs in demo mode")
    status, site = call(base, "GET", "/api/v1/config/site")
    problems += [f"GET /config/site -> {status}"] if status != 200 else []
    problems += site_problems(site, seeded["site"]) if status == 200 else []
    status, export = call(base, "GET", "/api/v1/config/export")
    if status != 200 or "site" not in export.get("sections", {}):
        problems.append(f"the export lost the site section: {status}")
    elif export["db_schema_version"] < seeded["export"]["db_schema_version"]:
        problems.append("the export reports an older database schema")
    status, decisions = call(base, "GET", "/api/v1/decisions?limit=1000")
    if status != 200 or len(decisions) < seeded["decisions"]:
        problems.append(f"decision log: {status}, {len(decisions or [])} < {seeded['decisions']}")
    if problems:
        raise SystemExit("upgrade verification failed:\n  " + "\n  ".join(problems))
    return {
        "health": health,
        "info": info,
        "decisions": len(decisions),
        "db_schema_version": export["db_schema_version"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["wait", "seed", "verify"])
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--seed")
    args = parser.parse_args(argv)
    if args.command == "wait":
        result = wait(args.base, args.timeout)
    elif args.command == "seed":
        result = seed(args.base)
    else:
        if not args.seed:
            parser.error("verify needs --seed FILE")
        result = verify(args.base, args.seed)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
