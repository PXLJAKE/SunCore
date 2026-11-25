#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Produce the frozen release fixtures from the *tagged* code (WP13).

    scripts/gen_release_fixtures.py [--tag v0.2.0] [--out tests/fixtures]

The tag's `suncore/` tree is extracted with `git archive` into a temporary directory and
imported by a child interpreter (PYTHONPATH first, so it shadows the working tree). The child
seeds a data directory through the *released* HTTP API (`PUT /api/v1/config/site`, the safety
gate for decision rows, `GET /api/v1/config/export`) and dumps the database. Nothing is
written by hand: the files are what the released code writes.

Outputs (relative to `--out`):

- `db/suncore_v<N>.sql`: `iterdump` of `suncore.db` plus `PRAGMA user_version`, N from the DB;
- `release/export_<version>.json`: the body of `GET /api/v1/config/export`;
- `release/MANIFEST_<tag>.json`: tag, commit, SHA-256 of every migration file of the tag.

Only released tags can be frozen. A schema version that no tag ever shipped (suncore.db v2..v6,
timeseries.db v1..v3 are unreleased on 2026-11-25) has no fixture here; the tests build those
from the shipped migration files instead and say so.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Runs inside the child interpreter against the extracted tag. Kept as text on purpose: it must
# not import anything from the working tree.
_SEEDER = r"""
import asyncio, json, sqlite3, sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

data_dir, version, tree = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[4])
sys.path.insert(0, str(tree))  # isolated mode ignores PYTHONPATH
import suncore
assert Path(suncore.__file__).resolve().is_relative_to(tree.resolve()), suncore.__file__
suncore.__version__ = version  # the tag's pyproject version (the package is not installed)
import suncore.storage.config_store as cs
cs.__version__ = version
FIXED = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)

import httpx
from suncore.api import create_app
from suncore.app.main import build_runtime
from suncore.app.settings import Settings
from suncore.core.decisions import Intent, Origin, Reason, SwitchAction
from suncore.sim.fake_ha import FakeHAConnection, demo_site_config
from suncore.sim.scenario import load_scenario
from suncore.storage import Database

class Clock:
    def __init__(self): self.n = 0
    def __call__(self):
        self.n += 1
        return FIXED + timedelta(seconds=self.n)

async def main():
    clock = Clock()
    conn = FakeHAConnection(load_scenario("summer_day"), clock=clock, speed=1.0)
    settings = Settings(demo_mode=False, data_dir=data_dir, static_dir=data_dir / "no-dist")
    runtime = await build_runtime(settings, env={}, connection=conn, clock=clock)
    runtime.config._clock = lambda: FIXED  # fixed updated_at: the dump is reproducible
    await runtime.start()
    app = create_app(runtime)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        body = demo_site_config().model_dump(mode="json")
        r = await client.put("/api/v1/config/site", json=body)
        assert r.status_code == 200, r.text
        for n in range(3):
            await runtime.gate.submit(Intent(
                id=f"seed-{n}", ts=FIXED + timedelta(seconds=n), asset_id="device.seed",
                action=SwitchAction(on=bool(n % 2)),
                reasons=(Reason(key="controller.surplus", args={"watts": 1200.5 + n}),),
                origin=Origin.CONTROLLER))
        r = await client.get("/api/v1/config/export")
        assert r.status_code == 200, r.text
        export = r.json()
        r = await client.get("/api/v1/decisions", params={"limit": 10})
        assert r.status_code == 200 and len(r.json()) == 3, r.text
        r = await client.get("/health")
        assert r.status_code in (200, 503)
    await runtime.db.execute(
        "INSERT INTO learned (key, value, updated_at) VALUES (?, ?, ?)",
        ("seed.note", json.dumps({"n": 1}), FIXED.isoformat()))  # no released writer for this table
    await runtime.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    await runtime.stop()
    return export

export = asyncio.run(main())
con = sqlite3.connect(data_dir / "suncore.db")
user_version = con.execute("PRAGMA user_version").fetchone()[0]
lines = [line for line in con.iterdump()]
con.close()
Path(sys.argv[3]).write_text(json.dumps(
    {"user_version": user_version, "dump": "\n".join(lines) + "\n", "export": export}))
"""


def _run(cmd: list[str], **kw: object) -> str:
    done = subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)  # type: ignore[call-overload]
    return str(done.stdout).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", default="v0.2.0")
    parser.add_argument("--out", type=Path, default=ROOT / "tests" / "fixtures")
    args = parser.parse_args(argv)

    try:
        commit = _run(["git", "-C", str(ROOT), "rev-parse", f"{args.tag}^{{commit}}"])
    except subprocess.CalledProcessError:
        print(f"error: tag {args.tag!r} does not exist; nothing is faked", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="suncore-tag-") as tmp:
        tree = Path(tmp) / "tree"
        tree.mkdir()
        archive = Path(tmp) / "src.tar"
        subprocess.run(
            [
                "git",
                "-C",
                str(ROOT),
                "archive",
                "-o",
                str(archive),
                commit,
                "suncore",
                "pyproject.toml",
            ],
            check=True,
        )
        with tarfile.open(archive) as tar:
            tar.extractall(tree, filter="data")
        version = next(
            line.split('"')[1]
            for line in (tree / "pyproject.toml").read_text("utf-8").splitlines()
            if line.startswith("version = ")
        )
        seeder = Path(tmp) / "seeder.py"
        seeder.write_text(_SEEDER, encoding="utf-8")
        result = Path(tmp) / "result.json"
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        proc = subprocess.run(
            [sys.executable, "-I", str(seeder), str(data_dir), version, str(result), str(tree)],
            cwd=tmp,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            print(proc.stdout, proc.stderr, file=sys.stderr)
            return 1
        out = json.loads(result.read_text("utf-8"))
        migrations = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((tree / "suncore" / "storage" / "migrations").glob("0*.sql"))
        }

    (args.out / "db").mkdir(parents=True, exist_ok=True)
    (args.out / "release").mkdir(parents=True, exist_ok=True)
    header = (
        f"-- Frozen suncore.db of release {args.tag} ({commit[:12]}), "
        f"schema {out['user_version']}.\n"
        f"-- Generated by scripts/gen_release_fixtures.py from the tagged code; do not edit.\n"
    )
    dump = args.out / "db" / f"suncore_v{out['user_version']}.sql"
    dump.write_text(header + out["dump"] + f"PRAGMA user_version = {out['user_version']};\n")
    export = args.out / "release" / f"export_{version}.json"
    export.write_text(json.dumps(out["export"], indent=2, sort_keys=True) + "\n")
    manifest = {
        "tag": args.tag,
        "commit": commit,
        "app_version": version,
        "suncore_db_schema": out["user_version"],
        "migrations_sha256": migrations,
    }
    (args.out / "release" / f"MANIFEST_{args.tag}.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print(f"wrote {dump.relative_to(ROOT)}, {export.relative_to(ROOT)}, manifest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
