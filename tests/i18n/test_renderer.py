# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest

from suncore.i18n import (
    Catalogue,
    MessageSyntaxError,
    MissingArgumentError,
    load_catalogue,
    parse_message,
    render_message,
    resolve_language,
)


def test_simple_argument_and_text() -> None:
    assert render_message("Hello {name}!", "en", {"name": "Ada"}) == "Hello Ada!"


def test_missing_argument_raises() -> None:
    with pytest.raises(MissingArgumentError):
        render_message("Hello {name}", "en", {})


@pytest.mark.parametrize(
    ("lang", "value", "expected"),
    [
        ("en", 1234.5, "1,234.5"),
        ("de", 1234.5, "1.234,5"),
        ("en", 0, "0"),
        ("de", 0.125, "0,125"),
        ("en", 1234567, "1,234,567"),
    ],
)
def test_number_default(lang: str, value: float, expected: str) -> None:
    assert render_message("{v, number}", lang, {"v": value}) == expected


@pytest.mark.parametrize(
    ("style", "lang", "value", "expected"),
    [
        (".0", "en", 2.25, "2.3"),
        (".0", "de", 2.25, "2,3"),
        (".00", "en", 1, "1.00"),
        (".000", "de", 3, "3,000"),
        (".0#", "en", 1.234, "1.23"),
        ("0", "en", 5, "5"),
        ("00", "en", 5, "05"),
    ],
)
def test_number_skeleton(style: str, lang: str, value: float, expected: str) -> None:
    assert render_message(f"{{v, number, ::{style}}}", lang, {"v": value}) == expected


def test_plural_exact_one_other_and_pound() -> None:
    msg = "{n, plural, =0 {none} one {# item} other {# items}}"
    assert render_message(msg, "en", {"n": 0}) == "none"
    assert render_message(msg, "en", {"n": 1}) == "1 item"
    assert render_message(msg, "de", {"n": 1500}) == "1.500 items"


def test_plural_offset() -> None:
    msg = "{n, plural, offset:1 =0 {nobody} =1 {you} one {you and #} other {you and # others}}"
    assert render_message(msg, "en", {"n": 2}) == "you and 1"
    assert render_message(msg, "en", {"n": 4}) == "you and 3 others"


def test_unknown_language_plural_uses_other() -> None:
    assert render_message("{n, plural, one {# a} other {# b}}", "xx", {"n": 1}) == "1 b"


def test_select_with_other_fallback() -> None:
    msg = "{k, select, a {A} other {?}}"
    assert render_message(msg, "en", {"k": "a"}) == "A"
    assert render_message(msg, "en", {"k": "zzz"}) == "?"


def test_apostrophes() -> None:
    assert render_message("It's fine", "en") == "It's fine"
    assert render_message("It''s '{'quoted'}'", "en") == "It's {quoted}"


@pytest.mark.parametrize(
    "bad",
    [
        "{x, date}",
        "{x, number, percent}",
        "{n, plural, one {a}}",
        "oops }",
        "{x",
        "{n, selectordinal, other {a}}",
    ],
)
def test_unsupported_messages_are_rejected(bad: str) -> None:
    with pytest.raises(MessageSyntaxError):
        parse_message(bad)


def test_catalogue_falls_back_and_never_raises(tmp_path: Path) -> None:
    (tmp_path / "en.json").write_text('{"a": "A {x}", "b": "B", "c": "C {y}"}', encoding="utf-8")
    (tmp_path / "de.json").write_text('{"a": "Ä {x}", "c": "Z {y}"}', encoding="utf-8")
    de = load_catalogue("de", tmp_path)
    assert de.render("a", {"x": 1}) == "Ä 1"
    assert de.render("b") == "B"
    assert de.render("c") == "c"  # argument missing in both languages: the key
    assert de.render("missing") == "missing"
    assert de.has("b")
    assert Catalogue("en", {}).render("k") == "k"


def test_resolve_language() -> None:
    assert resolve_language("de_CH") == "de"
    assert resolve_language("fr") == "en"
    assert resolve_language(None) == "en"
