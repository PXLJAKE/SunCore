# SPDX-License-Identifier: Apache-2.0
"""Role matcher: hard criteria, scoring order and ranking."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from suncore.core.entities import EntityMeta
from suncore.templates import EntityMatcher, match_role, matched_criteria, rank_entities
from suncore.templates.matcher import WEIGHTS

ENERGY = EntityMatcher(
    domain="sensor", device_class="energy", units=["Wh", "kWh"], platforms=["lg_thinq"]
)


def test_domain_must_match() -> None:
    assert match_role(EntityMatcher(domain="climate"), EntityMeta.of("sensor.x")) is None
    assert (
        match_role(EntityMatcher(domain="climate"), EntityMeta.of("climate.x")) == WEIGHTS["domain"]
    )


def test_all_given_criteria_must_hold() -> None:
    ok = EntityMeta.of("sensor.e", platform="lg_thinq", device_class="energy", unit="Wh")
    assert match_role(ENERGY, ok) == sum(
        WEIGHTS[k] for k in ("domain", "device_class", "platforms", "units")
    )
    for wrong in (
        EntityMeta.of("sensor.e", platform="other", device_class="energy", unit="Wh"),
        EntityMeta.of("sensor.e", platform="lg_thinq", device_class="power", unit="Wh"),
        EntityMeta.of("sensor.e", platform="lg_thinq", device_class="energy", unit="W"),
    ):
        assert match_role(ENERGY, wrong) is None


def test_unknown_attribute_never_matches_a_given_criterion() -> None:
    assert match_role(ENERGY, EntityMeta.of("sensor.e", device_class="energy", unit="Wh")) is None
    assert match_role(ENERGY, EntityMeta.of("sensor.e", platform="lg_thinq", unit="Wh")) is None
    assert (
        match_role(ENERGY, EntityMeta.of("sensor.e", platform="lg_thinq", device_class="energy"))
        is None
    )


def test_unit_is_exact_and_trimmed() -> None:
    matcher = EntityMatcher(domain="sensor", units=["kW"])
    assert match_role(matcher, EntityMeta.of("sensor.p", unit=" kW ")) is not None
    assert match_role(matcher, EntityMeta.of("sensor.p", unit="KW")) is None


def test_translation_key_outweighs_device_class() -> None:
    by_key = EntityMatcher(domain="sensor", translation_key="energy_today")
    by_class = EntityMatcher(domain="sensor", device_class="energy")
    meta = EntityMeta.of("sensor.e", translation_key="energy_today", device_class="energy")
    key_score = match_role(by_key, meta)
    class_score = match_role(by_class, meta)
    assert key_score is not None
    assert class_score is not None
    assert key_score > class_score


def test_state_class_criterion() -> None:
    matcher = EntityMatcher(domain="sensor", state_class="total_increasing")
    assert matched_criteria(matcher, EntityMeta.of("sensor.e", state_class="total_increasing")) == (
        "domain",
        "state_class",
    )
    assert matched_criteria(matcher, EntityMeta.of("sensor.e", state_class="measurement")) is None


def test_rank_entities_orders_by_score_then_id() -> None:
    matcher = EntityMatcher(domain="sensor", device_class="power", platforms=["kostal_plenticore"])
    metas = [
        EntityMeta.of("sensor.z", platform="kostal_plenticore", device_class="power"),
        EntityMeta.of("sensor.a", platform="kostal_plenticore", device_class="power"),
        EntityMeta.of("sensor.generic", device_class="power"),
        EntityMeta.of("switch.s", platform="kostal_plenticore", device_class="power"),
    ]
    ranked = rank_entities(matcher, metas)
    assert [m.entity_id for m, _ in ranked] == ["sensor.a", "sensor.z"]
    assert ranked[0][1] == ranked[1][1]


@given(
    device_class=st.none() | st.sampled_from(["power", "energy", "battery"]),
    unit=st.none() | st.sampled_from(["W", "kW", "Wh", "%"]),
    platform=st.none() | st.sampled_from(["a", "b"]),
)
def test_score_is_sum_of_given_criteria(
    device_class: str | None, unit: str | None, platform: str | None
) -> None:
    matcher = EntityMatcher(
        domain="sensor",
        device_class=device_class,
        platforms=None if platform is None else [platform],
    )
    meta = EntityMeta.of("sensor.x", device_class=device_class, unit=unit, platform=platform)
    expected = WEIGHTS["domain"]
    expected += WEIGHTS["device_class"] if device_class else 0
    expected += WEIGHTS["platforms"] if platform else 0
    assert match_role(matcher, meta) == expected


# --- ADR-0012: lists, regex, same device -----------------------------------------

LG_ENERGY = EntityMatcher(
    domain="sensor",
    platforms=["lg_thinq"],
    translation_key_pattern=r"energy_usage_(today|yesterday)(_for_location)?",
    device_class="energy",
    units=["Wh"],
)


def test_platform_list_matches_any() -> None:
    matcher = EntityMatcher(domain="sensor", platforms=["a", "b"])
    assert match_role(matcher, EntityMeta.of("sensor.x", platform="b")) is not None
    assert match_role(matcher, EntityMeta.of("sensor.x", platform="c")) is None
    assert match_role(matcher, EntityMeta.of("sensor.x")) is None


def test_translation_key_pattern_is_anchored() -> None:
    def meta(key: str | None) -> EntityMeta:
        return EntityMeta.of(
            "sensor.e", platform="lg_thinq", device_class="energy", unit="Wh", translation_key=key
        )

    for key in ("energy_usage_today", "energy_usage_yesterday_for_location"):
        assert match_role(LG_ENERGY, meta(key)) is not None
    for key in ("energy_usage_this_month", "xenergy_usage_today", "energy_usage_today_", None):
        assert match_role(LG_ENERGY, meta(key)) is None


def test_unit_pattern() -> None:
    matcher = EntityMatcher(domain="sensor", unit_pattern=r"[A-Z]{3}/kWh")
    assert matched_criteria(matcher, EntityMeta.of("sensor.p", unit=" EUR/kWh ")) == (
        "domain",
        "unit_pattern",
    )
    assert match_role(matcher, EntityMeta.of("sensor.p", unit="EUR/MWh")) is None
    assert match_role(matcher, EntityMeta.of("sensor.p")) is None
    assert WEIGHTS["unit_pattern"] == 20
    assert WEIGHTS["translation_key_pattern"] == WEIGHTS["translation_key"] == 50


def test_same_device_filters_and_boosts() -> None:
    matcher = EntityMatcher(domain="sensor")
    same = EntityMeta.of("sensor.a", device_id="d1")
    other = EntityMeta.of("sensor.b", device_id="d2")
    unknown = EntityMeta.of("sensor.c")
    ranked = rank_entities(matcher, [other, unknown, same], same_device_id="d1")
    assert [m.entity_id for m, _ in ranked] == ["sensor.a", "sensor.c"]
    assert ranked[0][1] == WEIGHTS["domain"] + WEIGHTS["same_device"]
    assert ranked[1][1] == WEIGHTS["domain"]  # unknown device: kept, unboosted
    assert WEIGHTS["same_device"] == 60
    assert len(rank_entities(matcher, [other, same])) == 2  # no constraint given
