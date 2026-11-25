# SPDX-License-Identifier: Apache-2.0
"""Action tokens: random, single use, bound to one proposal, expiring, sibling-revoking."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from suncore.notify.actions import ActionTokens, Consumed, Refusal, Verb

T0 = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
LATER = T0 + timedelta(minutes=10)


def test_action_ids_have_the_documented_shape_and_are_random() -> None:
    tokens = ActionTokens()
    first = tokens.issue("p1", LATER)
    second = tokens.issue("p2", LATER)
    for verb in Verb:
        assert first[verb].startswith(f"SUNCORE_{verb.value}_")
        assert len(first[verb].rsplit("_", 1)[1]) == 32
    assert len({*first.values(), *second.values()}) == 4


def test_a_token_works_once_and_is_bound_to_its_proposal() -> None:
    tokens = ActionTokens()
    ids = tokens.issue("p1", LATER)
    assert tokens.consume(ids[Verb.CONFIRM], T0) == Consumed(Verb.CONFIRM, "p1")
    assert tokens.consume(ids[Verb.CONFIRM], T0) is Refusal.USED  # replay: no second effect


def test_confirming_revokes_the_reject_button_and_vice_versa() -> None:
    tokens = ActionTokens()
    ids = tokens.issue("p1", LATER)
    assert isinstance(tokens.consume(ids[Verb.REJECT], T0), Consumed)
    assert tokens.consume(ids[Verb.CONFIRM], T0) is Refusal.USED
    assert tokens.open_count() == 0


def test_a_token_expires_with_the_proposal() -> None:
    tokens = ActionTokens()
    ids = tokens.issue("p1", LATER)
    assert tokens.consume(ids[Verb.CONFIRM], LATER) is Refusal.EXPIRED  # at the instant
    assert tokens.consume(ids[Verb.CONFIRM], LATER) is Refusal.USED


def test_prune_drops_expired_tokens() -> None:
    tokens = ActionTokens()
    tokens.issue("p1", LATER)
    tokens.issue("p2", LATER + timedelta(hours=1))
    tokens.prune(LATER + timedelta(minutes=1))
    assert tokens.open_count() == 2  # the two buttons of p2


@pytest.mark.parametrize(
    "action",
    [
        "",
        "CONFIRM",
        "SUNCORE_CONFIRM_",
        "SUNCORE_CONFIRM_xyz",
        "SUNCORE_APPROVE_" + "a" * 32,
        "suncore_confirm_" + "a" * 32,
        "SUNCORE_CONFIRM_" + "A" * 32,
        "SUNCORE_CONFIRM_" + "a" * 33,
        " SUNCORE_CONFIRM_" + "a" * 32,
        "URI_other_button",
    ],
)
def test_malformed_or_foreign_ids_are_not_ours(action: str) -> None:
    assert ActionTokens().consume(action, T0) is Refusal.NOT_OURS


def test_a_guessed_token_is_unknown_and_changes_nothing() -> None:
    tokens = ActionTokens()
    tokens.issue("p1", LATER)
    assert tokens.consume("SUNCORE_CONFIRM_" + "0" * 32, T0) is Refusal.UNKNOWN
    assert tokens.open_count() == 2


def test_the_token_of_one_verb_does_not_work_as_the_other() -> None:
    tokens = ActionTokens()
    ids = tokens.issue("p1", LATER)
    swapped = ids[Verb.CONFIRM].replace("CONFIRM", "REJECT", 1)
    assert tokens.consume(swapped, T0) is Refusal.UNKNOWN  # not burnt either
    assert tokens.consume(ids[Verb.CONFIRM], T0) == Consumed(Verb.CONFIRM, "p1")


def test_issuing_again_revokes_the_old_buttons_of_the_same_proposal() -> None:
    tokens = ActionTokens()
    old = tokens.issue("p1", LATER)
    new = tokens.issue("p1", LATER)
    assert tokens.consume(old[Verb.CONFIRM], T0) is Refusal.USED
    assert isinstance(tokens.consume(new[Verb.CONFIRM], T0), Consumed)


def test_the_number_of_open_proposals_is_bounded() -> None:
    tokens = ActionTokens()
    for n in range(100):
        tokens.issue(f"p{n}", LATER)
    assert tokens.open_count() <= 64
