# SPDX-License-Identifier: Apache-2.0
"""Pure parsing of HA contexts (``c`` of ``subscribe_entities``, ``context`` of ``get_states``)."""

from __future__ import annotations

import pytest

from suncore.ha.connection import EntityState, StateContext
from suncore.ha.websocket import (
    apply_diff,
    context_from_wire,
    merge_context,
    state_from_compressed,
    state_from_full,
)


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        ("abc", StateContext(id="abc")),
        ({"id": "abc", "parent_id": None, "user_id": None}, StateContext(id="abc")),
        (
            {"id": "abc", "parent_id": "p", "user_id": "u"},
            StateContext(id="abc", parent_id="p", user_id="u"),
        ),
        ("", None),
        ({"user_id": "u"}, None),
        (None, None),
        (7, None),
    ],
)
def test_context_from_wire(wire: object, expected: StateContext | None) -> None:
    assert context_from_wire(wire) == expected


def test_a_string_diff_replaces_the_id_only() -> None:
    before = StateContext(id="a", user_id="u", parent_id="p")
    assert merge_context(before, "b") == StateContext(id="b", user_id="u", parent_id="p")
    assert merge_context(None, "b") == StateContext(id="b")
    assert merge_context(before, "") == before


def test_a_dict_diff_overrides_the_keys_it_has() -> None:
    before = StateContext(id="a", user_id="u", parent_id="p")
    assert merge_context(before, {"user_id": None, "id": "b"}) == StateContext(
        id="b", parent_id="p"
    )
    assert merge_context(before, {"parent_id": None}) == StateContext(id="a", user_id="u")
    # no id and nothing to merge onto: unusable
    assert merge_context(None, {"user_id": "u"}) is None
    assert merge_context(before, 3) == before


def test_apply_diff_keeps_the_context_when_the_diff_has_none() -> None:
    previous = state_from_compressed(
        "sensor.x", {"s": "1", "a": {}, "c": {"id": "a", "user_id": "u"}}
    )
    assert previous.context == StateContext(id="a", user_id="u")
    same = apply_diff(previous, {"+": {"s": "2"}})
    assert same.context == previous.context
    changed = apply_diff(previous, {"+": {"s": "3", "c": "b"}})
    assert changed.context == StateContext(id="b", user_id="u")


def test_get_states_context_dict() -> None:
    state = state_from_full(
        {
            "entity_id": "climate.x",
            "state": "cool",
            "attributes": {},
            "context": {"id": "c1", "parent_id": None, "user_id": "u"},
        }
    )
    assert isinstance(state, EntityState)
    assert state.context == StateContext(id="c1", user_id="u")
    bare = state_from_full({"entity_id": "climate.x", "state": "cool"})
    assert bare is not None
    assert bare.context is None
