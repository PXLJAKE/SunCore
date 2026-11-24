# SPDX-License-Identifier: Apache-2.0
"""A minimal ICU MessageFormat renderer.

Supported subset (what the catalogue lint allows, ADR-0016 option 4C):

* literal text with ICU apostrophe quoting (``''`` is an apostrophe, ``'{'`` quotes);
* simple arguments ``{name}``;
* ``{name, number}`` and ``{name, number, ::<skeleton>}`` with the skeleton tokens ``.0``,
  ``.0#``, ``.##`` (fraction places) and ``0``/``00`` (minimum integer places); anything else
  raises :class:`MessageSyntaxError`, so the generator catches it;
* ``{name, plural, =n {..} one {..} other {..}}`` with ``#`` and the ``offset:`` prefix;
* ``{name, select, key {..} other {..}}``.

The output is cross-checked against ``intl-messageformat`` by a golden test.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

from suncore.i18n.plural import format_number, plural_category

MessageArgs = Mapping[str, str | int | float | bool | None]


class MessageError(ValueError):
    """Base of renderer errors."""


class MessageSyntaxError(MessageError):
    """The message is not valid in the supported ICU subset."""


class MissingArgumentError(MessageError):
    """The message needs an argument that was not given."""


@dataclass(frozen=True, slots=True)
class _Text:
    text: str


@dataclass(frozen=True, slots=True)
class _Pound:
    """``#`` inside a plural branch: the (offset) number."""


@dataclass(frozen=True, slots=True)
class _Arg:
    name: str


@dataclass(frozen=True, slots=True)
class _Number:
    name: str
    skeleton: str | None


@dataclass(frozen=True, slots=True)
class _Choice:
    name: str
    plural: bool
    offset: float
    branches: tuple[tuple[str, tuple[_Node, ...]], ...]


_Node = _Text | _Pound | _Arg | _Number | _Choice

_NAME: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_SELECTOR: Final = re.compile(r"=\d+|[^\s{},]+")
_TYPE: Final = re.compile(r"[a-z]+")
_FRACTION: Final = re.compile(r"^\.(0*)(#*)$")


@dataclass(frozen=True, slots=True)
class _Skeleton:
    min_fraction: int = 0
    max_fraction: int = 3
    min_integer: int = 1


def _parse_skeleton(text: str) -> _Skeleton:
    result = _Skeleton()
    for token in text.split():
        fraction = _FRACTION.match(token)
        if fraction is not None:
            zeros, hashes = fraction.groups()
            result = _Skeleton(len(zeros), len(zeros) + len(hashes), result.min_integer)
        elif re.fullmatch(r"0+", token):
            result = _Skeleton(result.min_fraction, result.max_fraction, len(token))
        else:
            msg = f"unsupported number skeleton token '{token}'"
            raise MessageSyntaxError(msg)
    return result


class _Parser:
    def __init__(self, source: str) -> None:
        self._s = source
        self._i = 0

    def parse(self) -> tuple[_Node, ...]:
        nodes = self._message(top=True, in_plural=False)
        if self._i < len(self._s):
            msg = f"unexpected '}}' at {self._i}"
            raise MessageSyntaxError(msg)
        return nodes

    def _fail(self, expected: str) -> MessageSyntaxError:
        return MessageSyntaxError(f"expected {expected} at {self._i}")

    def _message(self, *, top: bool, in_plural: bool) -> tuple[_Node, ...]:
        nodes: list[_Node] = []
        buf: list[str] = []

        def flush() -> None:
            if buf:
                nodes.append(_Text("".join(buf)))
                buf.clear()

        s = self._s
        while self._i < len(s):
            ch = s[self._i]
            if ch == "}":
                if top:
                    raise self._fail("text")
                break
            if ch == "{":
                flush()
                self._i += 1
                nodes.append(self._argument())
            elif ch == "#" and in_plural:
                flush()
                nodes.append(_Pound())
                self._i += 1
            elif ch == "'":
                self._apostrophe(buf, in_plural=in_plural)
            else:
                buf.append(ch)
                self._i += 1
        flush()
        return tuple(nodes)

    def _apostrophe(self, buf: list[str], *, in_plural: bool) -> None:
        s = self._s
        nxt = s[self._i + 1] if self._i + 1 < len(s) else ""
        if nxt == "'":
            buf.append("'")
            self._i += 2
            return
        special = "{}#" if in_plural else "{}"
        if nxt and nxt in special:
            end = self._i + 1
            while end < len(s):
                if s[end] == "'":
                    if end + 1 < len(s) and s[end + 1] == "'":
                        buf.append("'")
                        end += 2
                        continue
                    break
                buf.append(s[end])
                end += 1
            self._i = end + 1
            return
        buf.append("'")
        self._i += 1

    def _skip_ws(self) -> None:
        while self._i < len(self._s) and self._s[self._i].isspace():
            self._i += 1

    def _peek(self) -> str:
        return self._s[self._i] if self._i < len(self._s) else ""

    def _take(self, pattern: re.Pattern[str], what: str) -> str:
        self._skip_ws()
        match = pattern.match(self._s, self._i)
        if match is None:
            raise self._fail(what)
        self._i = match.end()
        return match.group(0)

    def _argument(self) -> _Node:
        name = self._take(_NAME, "an argument name")
        self._skip_ws()
        if self._peek() == "}":
            self._i += 1
            return _Arg(name)
        if self._peek() != ",":
            raise self._fail("',' or '}'")
        self._i += 1
        kind = self._take(_TYPE, "an argument type")
        self._skip_ws()
        if kind == "number":
            return self._number(name)
        if kind in ("plural", "select"):
            return self._choice(name, plural=kind == "plural")
        msg = f"unsupported argument type '{kind}'"
        raise MessageSyntaxError(msg)

    def _number(self, name: str) -> _Number:
        skeleton: str | None = None
        if self._peek() == ",":
            self._i += 1
            self._skip_ws()
            end = self._s.find("}", self._i)
            if end < 0:
                raise self._fail("'}'")
            style = self._s[self._i : end].strip()
            if not style.startswith("::"):
                msg = f"unsupported number style '{style}'"
                raise MessageSyntaxError(msg)
            skeleton = style[2:]
            _parse_skeleton(skeleton)
            self._i = end
        self._skip_ws()
        if self._peek() != "}":
            raise self._fail("'}'")
        self._i += 1
        return _Number(name, skeleton)

    def _choice(self, name: str, *, plural: bool) -> _Choice:
        if self._peek() != ",":
            raise self._fail("','")
        self._i += 1
        offset = 0.0
        self._skip_ws()
        if plural and self._s.startswith("offset:", self._i):
            self._i += len("offset:")
            offset = float(self._take(re.compile(r"\d+"), "an offset"))
        branches: list[tuple[str, tuple[_Node, ...]]] = []
        while True:
            self._skip_ws()
            if self._peek() == "}":
                self._i += 1
                break
            selector = self._take(_SELECTOR, "a selector")
            self._skip_ws()
            if self._peek() != "{":
                raise self._fail("'{'")
            self._i += 1
            body = self._message(top=False, in_plural=plural)
            if self._peek() != "}":
                raise self._fail("'}'")
            self._i += 1
            branches.append((selector, body))
        if not any(sel == "other" for sel, _ in branches):
            msg = f"'{name}' needs an 'other' branch"
            raise MessageSyntaxError(msg)
        return _Choice(name, plural, offset, tuple(branches))


@lru_cache(maxsize=2048)
def parse_message(source: str) -> tuple[_Node, ...]:
    """Parse ``source``; raises :class:`MessageSyntaxError` outside the supported subset."""
    return _Parser(source).parse()


def _present(args: MessageArgs, name: str) -> str | int | float | bool:
    value = args.get(name)
    if value is None:
        raise MissingArgumentError(name)
    return value


def _number_value(args: MessageArgs, name: str) -> float:
    raw = _present(args, name)
    if isinstance(raw, bool):
        msg = f"argument '{name}' is not a number"
        raise MessageError(msg)
    try:
        return float(raw)
    except ValueError as exc:
        msg = f"argument '{name}' is not a number"
        raise MessageError(msg) from exc


def _render(nodes: tuple[_Node, ...], language: str, args: MessageArgs, pound: float) -> str:
    out: list[str] = []
    for node in nodes:
        if isinstance(node, _Text):
            out.append(node.text)
        elif isinstance(node, _Pound):
            out.append(format_number(language, pound))
        elif isinstance(node, _Arg):
            value = _present(args, node.name)
            out.append(str(value).lower() if isinstance(value, bool) else str(value))
        elif isinstance(node, _Number):
            number = _number_value(args, node.name)
            sk = _parse_skeleton(node.skeleton) if node.skeleton is not None else _Skeleton()
            out.append(
                format_number(
                    language,
                    number,
                    min_fraction=sk.min_fraction,
                    max_fraction=sk.max_fraction,
                    min_integer=sk.min_integer,
                )
            )
        else:
            out.append(_render_choice(node, language, args))
    return "".join(out)


def _render_choice(node: _Choice, language: str, args: MessageArgs) -> str:
    branches = dict(node.branches)
    if not node.plural:
        key = str(_present(args, node.name))
        return _render(branches.get(key, branches["other"]), language, args, 0.0)
    number = _number_value(args, node.name)
    exact = f"={int(number)}" if number == int(number) else ""
    if exact in branches:
        return _render(branches[exact], language, args, number - node.offset)
    shifted = number - node.offset
    body = branches.get(plural_category(language, shifted), branches["other"])
    return _render(body, language, args, shifted)


def render_message(source: str, language: str, args: MessageArgs | None = None) -> str:
    """Render the ICU message ``source`` for ``language``.

    Raises :class:`MessageSyntaxError` for an unsupported message and
    :class:`MissingArgumentError` when an argument the message uses is not in ``args``.
    """
    return _render(parse_message(source), language, args or {}, 0.0)
