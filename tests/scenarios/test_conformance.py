# SPDX-License-Identifier: Apache-2.0
"""Template conformance: every built-in template resolves against the fake HA metadata.

For each template under ``templates/`` a scenario file ``tests/scenarios/<kind>/<id>.yaml``
states what the demo installation must resolve to. The test loads the real template and
the fake connection's registry and states (``FakeHAConnection.entity_metas``), then checks:

* every required role has candidates, and exactly one *top-ranked* candidate unless the
  scenario says the tie is intended (``tied``; a required role then names the user's choice in
  ``pick``) or the role has no candidate (``none``);
* the criteria that matched (the "reason" shown in the wizard) are the expected ones;
* ``same_device_as`` roles are resolved against the device of the chosen base entity;
* units of matched entities parse (power/energy) and equal the expected unit;
* kind specific facts: a device can be instantiated from the template defaults and the
  resolved mapping, the tariff price unit equals the currency of the HA configuration, the
  forecast attribute carries the declared fields in the declared unit and resolution, the
  region currency is consistent with the HA configuration.
"""

from __future__ import annotations

import asyncio
import itertools
import re
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from suncore.core.devices import DeviceConfig
from suncore.core.entities import EntityMeta
from suncore.core.site import EntityRef
from suncore.core.units import parse_unit
from suncore.ha.connection import EntityState
from suncore.sim.fake_ha import FakeHAConnection
from suncore.sim.scenario import load_scenario
from suncore.templates import (
    DeviceTemplate,
    ForecastTemplate,
    RegionTemplate,
    TariffTemplate,
    Template,
    load_templates,
    matched_criteria,
    rank_entities,
)
from suncore.templates.models import AttributeSeries

pytestmark = pytest.mark.simulator

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = Path(__file__).resolve().parent
CATALOG = load_templates(ROOT / "templates")


class RoleExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity: str | None = None
    tied: list[str] = Field(default_factory=list)
    pick: str | None = None
    """A required role may be tied if the user has to choose; ``pick`` is that choice."""
    none: bool = False
    unit: str | None = None
    criteria: list[str] | None = None


class ScenarioFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: str
    sim_scenario: str
    user_input: dict[str, Any] = Field(default_factory=dict)
    roles: dict[str, RoleExpectation]


def _scenario_files() -> dict[str, Path]:
    return {p.stem: p for p in sorted(SCENARIOS.glob("*/*.yaml"))}


FILES = _scenario_files()


def _load(path: Path) -> ScenarioFile:
    return ScenarioFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def test_every_builtin_template_has_a_scenario_and_vice_versa() -> None:
    assert set(CATALOG.templates) == set(FILES)
    for template_id, path in FILES.items():
        template = CATALOG.templates[template_id]
        assert path.parent.name == template.kind, f"{path} is in the wrong kind directory"
        assert _load(path).template == template_id


@pytest.fixture
async def fake() -> AsyncIterator[FakeHAConnection]:
    never = asyncio.Event()

    async def sleep(_: float) -> None:
        await never.wait()

    conn = FakeHAConnection(
        load_scenario("summer_day"),
        clock=lambda: datetime(2026, 6, 21, tzinfo=UTC),
        sleep=sleep,
        start_offset_s=13 * 3600,  # 13:00 local: AC and pool pump run, sun is up
    )
    await conn.connect()
    yield conn
    await conn.close()


def _resolve(
    template: Template, metas: list[EntityMeta], picks: Mapping[str, str] | None = None
) -> dict[str, list[tuple[EntityMeta, int]]]:
    """Rank candidates per role; roles with ``same_device_as`` follow their base role.

    A base role the scenario resolves by a user ``pick`` (tied candidates) hands the registry
    device of the picked entity to the roles that follow it; otherwise the first ranked one.
    """
    by_id = {m.entity_id: m for m in metas}
    roles = getattr(template, "roles", {})
    ranked: dict[str, list[tuple[EntityMeta, int]]] = {}
    for name, spec in roles.items():
        if spec.same_device_as is None:
            ranked[name] = rank_entities(spec.matcher, metas)
    for name, spec in roles.items():
        if spec.same_device_as is not None:
            base = ranked[spec.same_device_as]
            assert len(base) >= 1, f"base role {spec.same_device_as} of {name} has no candidate"
            picked = (picks or {}).get(spec.same_device_as)
            anchor = by_id[picked] if picked is not None else base[0][0]
            ranked[name] = rank_entities(spec.matcher, metas, same_device_id=anchor.device_id)
    return ranked


def _top(ranked: list[tuple[EntityMeta, int]]) -> list[EntityMeta]:
    if not ranked:
        return []
    best = ranked[0][1]
    return [m for m, s in ranked if s == best]


def _check_tied(
    name: str,
    expected: RoleExpectation,
    top: list[EntityMeta],
    *,
    required: bool,
    resolved: dict[str, EntityMeta],
    by_id: Mapping[str, EntityMeta],
) -> None:
    assert {m.entity_id for m in top} == set(expected.tied), name
    if required:  # the user has to choose between the tied candidates
        assert expected.pick in expected.tied, f"{name}: a required role needs a pick"
        resolved[name] = by_id[expected.pick]  # type: ignore[index]
    else:
        assert expected.pick is None, f"{name}: pick is only for required roles"


@pytest.mark.parametrize("template_id", sorted(FILES))
async def test_template_conforms(template_id: str, fake: FakeHAConnection) -> None:
    scenario = _load(FILES[template_id])
    template = CATALOG.templates[template_id]
    metas = fake.entity_metas()
    by_id = {m.entity_id: m for m in metas}
    states = {s.entity_id: s for s in await fake.get_states()}
    ranked = _resolve(
        template, metas, {r: e.pick for r, e in scenario.roles.items() if e.pick is not None}
    )

    assert set(scenario.roles) == set(getattr(template, "roles", {})), "scenario must cover roles"
    resolved: dict[str, EntityMeta] = {}
    for name, expected in scenario.roles.items():
        spec = template.roles[name]  # type: ignore[union-attr]
        top = _top(ranked[name])
        if expected.none:
            assert not ranked[name], f"{name}: expected no candidate, got {ranked[name]}"
            assert not spec.required, f"{name}: a required role cannot be unresolved"
            continue
        if expected.tied:
            _check_tied(name, expected, top, required=spec.required, resolved=resolved, by_id=by_id)
        else:
            assert [m.entity_id for m in top] == [expected.entity], f"{name}: {ranked[name]}"
            resolved[name] = top[0]
            if expected.criteria is not None:
                same = by_id[expected.entity].device_id if spec.same_device_as else None  # type: ignore[index]
                hits = matched_criteria(spec.matcher, top[0], same_device_id=same)
                assert hits is not None
                assert set(hits) == set(expected.criteria), name
        if expected.unit is not None:
            for meta in top:
                assert meta.unit == expected.unit, f"{name}: {meta.unit}"
                if meta.device_class in ("power", "energy"):
                    assert parse_unit(meta.unit) is not None

    if isinstance(template, DeviceTemplate):
        _check_device(template, scenario, resolved)
    elif isinstance(template, TariffTemplate):
        await _check_tariff(template, fake, resolved, states)
    elif isinstance(template, ForecastTemplate):
        _check_forecast(template, resolved, states)
    elif isinstance(template, RegionTemplate):
        await _check_region(template, fake)


def _check_device(
    template: DeviceTemplate, scenario: ScenarioFile, resolved: Mapping[str, EntityMeta]
) -> None:
    for name, spec in template.roles.items():
        if spec.required:
            assert name in resolved, f"required role {name} unresolved"
    defaults = template.defaults
    power_model: dict[str, Any] = defaults.power_model.model_dump(exclude_none=True)
    power_model.pop("nominal_w", None)
    if "nominal_w" in scenario.user_input:
        power_model["nominal_w"] = scenario.user_input["nominal_w"]
    config = DeviceConfig.model_validate(
        {
            "id": re.sub(r"\W", "_", template.id),
            "template_id": template.id,
            "name": template.id,
            "kind": template.device_kind.value,
            "entities": {r: EntityRef(entity_id=m.entity_id) for r, m in resolved.items()},
            "power_model": power_model,
            "control": defaults.control.model_dump(exclude_none=True),
            "min_run_s": defaults.min_run_s,
            "min_pause_s": defaults.min_pause_s,
            "settle_s": defaults.settle_s,
            "comfort": scenario.user_input.get("comfort"),
        }
    )
    assert config.control.role in config.entities
    assert config.max_switches_per_hour is None  # None = safety default, not unlimited


async def _check_tariff(
    template: TariffTemplate,
    fake: FakeHAConnection,
    resolved: Mapping[str, EntityMeta],
    states: Mapping[str, EntityState],
) -> None:
    currency = (await fake.get_config())["currency"]
    price = resolved["price"]
    assert price.unit == template.price_unit.replace("{currency}", currency)
    assert float(states[price.entity_id].state) > 0  # state = all-in price of the current slot
    assert template.price_series is not None
    assert template.price_series.fields.value_unit == "{currency}/kWh"


def _check_forecast(
    template: ForecastTemplate,
    resolved: Mapping[str, EntityMeta],
    states: Mapping[str, EntityState],
) -> None:
    series = template.series
    assert isinstance(series, AttributeSeries)
    fields = series.fields
    unit = parse_unit(fields.value_unit)
    assert unit is not None
    points: list[dict[str, Any]] = []
    for role in series.roles:
        entity = states[resolved[role].entity_id]
        attribute = entity.attributes[series.attribute]
        assert isinstance(attribute, list)
        assert attribute, role
        points.extend(attribute)
    required = {fields.timestamp_field, fields.value_field, fields.p10_field, fields.p90_field}
    assert all(required <= set(p) for p in points)
    stamps = [datetime.fromisoformat(p[fields.timestamp_field]) for p in points]
    assert stamps == sorted(stamps)
    deltas = {(b - a).total_seconds() / 60 for a, b in itertools.pairwise(stamps)}
    assert deltas == {template.resolution_min}
    assert all(p[fields.p10_field] <= p[fields.value_field] <= p[fields.p90_field] for p in points)
    # kW is average power over the slot: the day sensor in kWh equals sum(kW) * slot hours.
    today = states[resolved[series.roles[0]].entity_id]
    day = [
        p
        for p in points
        if p[fields.timestamp_field][:10] == points[0][fields.timestamp_field][:10]
    ]
    kwh = sum(p[fields.value_field] for p in day) * template.resolution_min / 60
    assert float(today.state) == pytest.approx(kwh, abs=0.05)


async def _check_region(template: RegionTemplate, fake: FakeHAConnection) -> None:
    config = await fake.get_config()
    assert re.fullmatch(r"[A-Z]{3}", config["currency"])
    if template.currency is None:
        assert template.country == "generic"  # taken from the HA configuration at runtime
    else:
        assert template.currency == config["currency"]
    if template.timezone is not None:
        ZoneInfo(template.timezone)
    if template.feed_in_tiers:
        assert template.source
    if template.country == "DE":
        assert template.negative_price_rule.value == "no_remuneration"
        assert template.unverified  # law text and tier table are not verified


def test_scenario_files_reject_unknown_keys(tmp_path: Path) -> None:
    bad = tmp_path / "x.yaml"
    bad.write_text("template: a.b\nsim_scenario: s\nroles: {}\noops: 1\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        _load(bad)
