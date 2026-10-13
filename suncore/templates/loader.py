# SPDX-License-Identifier: Apache-2.0
"""Template loader: ``templates/<kind>/<id>.yaml`` plus user overrides by id.

Built-in templates ship with the image; an invalid one is a bug and raises
:class:`BuiltinTemplateError`. User templates (``/data/templates``) are untrusted input:
errors are collected per file in :attr:`TemplateCatalog.errors` and the file is skipped,
so a bad user template never takes the app down.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import ValidationError

from suncore.templates.models import (
    TEMPLATE_ADAPTER,
    TEMPLATE_KINDS,
    VERSION_PATTERN,
    DeviceTemplate,
    ForecastTemplate,
    RegionTemplate,
    TariffTemplate,
    Template,
)

MAX_FILE_BYTES: Final = 256 * 1024
KINDS_TEXT: Final = ", ".join(TEMPLATE_KINDS)

Origin = Literal["builtin", "user"]


@dataclass(frozen=True, slots=True)
class TemplateFileError:
    path: Path
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


class BuiltinTemplateError(Exception):
    """A built-in template is invalid (packaging bug)."""

    def __init__(self, errors: tuple[TemplateFileError, ...]) -> None:
        self.errors = errors
        super().__init__("invalid built-in templates:\n" + "\n".join(f"  {e}" for e in errors))


@dataclass(frozen=True, slots=True)
class TemplateCatalog:
    """Loaded templates by id. ``overridden`` lists built-in ids replaced by a user template."""

    templates: Mapping[str, Template] = field(default_factory=dict)
    origins: Mapping[str, Origin] = field(default_factory=dict)
    paths: Mapping[str, Path] = field(default_factory=dict)
    overridden: tuple[str, ...] = ()
    errors: tuple[TemplateFileError, ...] = ()

    def get(self, template_id: str) -> Template | None:
        return self.templates.get(template_id)

    def of_kind(self, kind: str) -> list[Template]:
        return sorted((t for t in self.templates.values() if t.kind == kind), key=lambda t: t.id)

    def devices(self) -> list[DeviceTemplate]:
        return [t for t in self.of_kind("device") if isinstance(t, DeviceTemplate)]

    def tariffs(self) -> list[TariffTemplate]:
        return [t for t in self.of_kind("tariff") if isinstance(t, TariffTemplate)]

    def forecasts(self) -> list[ForecastTemplate]:
        return [t for t in self.of_kind("forecast") if isinstance(t, ForecastTemplate)]

    def regions(self) -> list[RegionTemplate]:
        return [t for t in self.of_kind("region") if isinstance(t, RegionTemplate)]


def _format_validation(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors(include_url=False, include_context=False, include_input=False):
        loc = ".".join(str(p) for p in err["loc"] if not _is_union_tag(p))
        msg = err["msg"].removeprefix("Value error, ")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts)


def _is_union_tag(part: str | int) -> bool:
    return isinstance(part, str) and part in {
        "DeviceTemplate",
        "TariffTemplate",
        "ForecastTemplate",
        "RegionTemplate",
    }


_VERSION_RE: Final = re.compile(VERSION_PATTERN)
_PRE_RANK: Final = {"dev": 0, "a": 1, "b": 2, "rc": 3}


def version_key(text: str) -> tuple[tuple[int, ...], int, int]:
    """Sort key for the PEP 440 subset of ``VERSION_PATTERN`` (release, pre-release rank, number).

    Release segments are zero-padded to four; ``1.2a1 < 1.2rc1 < 1.2``; post releases are
    treated as the release itself.
    """
    match = re.match(r"^(\d+(?:\.\d+)*)(?:(a|b|rc)(\d+))?", text)
    if match is None:
        msg = f"not a version: {text!r}"
        raise ValueError(msg)
    release = tuple(int(p) for p in match.group(1).split("."))
    release += (0,) * (4 - len(release))
    pre = _PRE_RANK[match.group(2)] if match.group(2) else 9
    dev = ".dev" in text and match.group(2) is None
    return release, 0 if dev else pre, int(match.group(3) or 0)


class _NoAliasLoader(yaml.SafeLoader):
    """SafeLoader that rejects anchors/aliases (alias expansion could blow up a small file)."""

    def compose_node(self, parent: yaml.Node | None, index: object) -> yaml.Node | None:
        if self.check_event(yaml.AliasEvent):
            msg = "aliases are not allowed in templates"
            raise yaml.YAMLError(msg)
        return super().compose_node(parent, index)  # type: ignore[arg-type]


def _check_min_version(raw: dict[str, object], app_version: str | None) -> None:
    required = raw.get("min_app_version")
    if app_version is None or not isinstance(required, str) or not _VERSION_RE.match(required):
        return  # a malformed value is reported by the schema validation
    if not _VERSION_RE.match(app_version):
        return  # unknown app version (source checkout): do not block
    if version_key(app_version) < version_key(required):
        msg = f"needs SunCore >= {required} (this is {app_version})"
        raise ValueError(msg)


def parse_template(
    path: Path, expected_kind: str | None = None, *, app_version: str | None = None
) -> Template:
    """Parse and validate one YAML file.

    ``app_version`` enables the ``min_app_version`` check, reported before any field error.

    Raises:
        ValueError: with a readable message for every problem (I/O, YAML, schema, naming).
    """
    try:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            msg = f"file too large ({size} bytes, limit {MAX_FILE_BYTES})"
            raise ValueError(msg)
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_NoAliasLoader)
    except OSError as exc:
        msg = f"cannot read: {exc.strerror or exc}"
        raise ValueError(msg) from exc
    except UnicodeDecodeError as exc:
        msg = "not valid UTF-8"
        raise ValueError(msg) from exc
    except yaml.YAMLError as exc:
        msg = f"invalid YAML: {str(exc).splitlines()[0] if str(exc) else 'parse error'}"
        raise ValueError(msg) from exc
    if not isinstance(raw, dict):
        msg = "top level must be a mapping"
        raise ValueError(msg)
    _check_min_version(raw, app_version)
    try:
        template = TEMPLATE_ADAPTER.validate_python(raw)
    except ValidationError as exc:
        raise ValueError(_format_validation(exc)) from exc
    if expected_kind is not None and template.kind != expected_kind:
        msg = f"kind `{template.kind}` does not match directory `{expected_kind}`"
        raise ValueError(msg)
    if path.stem != template.id:
        msg = f"file name must be `{template.id}.yaml`"
        raise ValueError(msg)
    return template


def _scan(
    root: Path, app_version: str | None
) -> tuple[list[tuple[Path, Template]], list[TemplateFileError]]:
    found: list[tuple[Path, Template]] = []
    errors: list[TemplateFileError] = []
    if root.is_dir():
        for sub in sorted(p for p in root.iterdir() if p.is_dir() and p.name not in TEMPLATE_KINDS):
            if sub.name == "schema":
                continue
            errors.extend(
                TemplateFileError(
                    path, f"unknown template kind directory `{sub.name}` (known: {KINDS_TEXT})"
                )
                for path in sorted(sub.glob("*.yaml"))
            )
    for kind in TEMPLATE_KINDS:
        directory = root / kind
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            try:
                found.append((path, parse_template(path, kind, app_version=app_version)))
            except ValueError as exc:
                errors.append(TemplateFileError(path, str(exc)))
    return found, errors


def scan_directory(
    root: Path, *, app_version: str | None = None
) -> tuple[list[tuple[Path, Template]], list[TemplateFileError]]:
    """Validate all templates under ``root`` (used by the CI lint), including duplicates.

    ``*.yaml`` files in sub directories that are no known kind (e.g. ``battery/`` before
    that kind exists) are errors, not silently ignored (ADR-0012 item 11).
    """
    found, errors = _scan(root, app_version)
    seen: dict[str, Path] = {}
    unique: list[tuple[Path, Template]] = []
    for path, template in found:
        if template.id in seen:
            errors.append(
                TemplateFileError(path, f"duplicate id `{template.id}` (also {seen[template.id]})")
            )
            continue
        seen[template.id] = path
        unique.append((path, template))
    return unique, errors


def load_templates(
    builtin_dir: Path, user_dir: Path | None = None, *, app_version: str | None = None
) -> TemplateCatalog:
    """Load built-in templates, then apply user templates (same id replaces the built-in).

    Raises:
        BuiltinTemplateError: if any built-in template is invalid or ids collide.
    """
    builtin, builtin_errors = scan_directory(builtin_dir, app_version=app_version)
    if builtin_errors:
        raise BuiltinTemplateError(tuple(builtin_errors))
    templates: dict[str, Template] = {t.id: t for _, t in builtin}
    origins: dict[str, Origin] = dict.fromkeys(templates, "builtin")
    paths: dict[str, Path] = {t.id: p for p, t in builtin}
    overridden: list[str] = []
    errors: list[TemplateFileError] = []

    if user_dir is not None and user_dir.is_dir():
        user, user_errors = scan_directory(user_dir, app_version=app_version)
        errors.extend(user_errors)
        for path, template in user:
            current = templates.get(template.id)
            if current is not None and current.kind != template.kind:
                errors.append(
                    TemplateFileError(
                        path,
                        f"id `{template.id}` is a built-in {current.kind} template, kind fixed",
                    )
                )
                continue
            if origins.get(template.id) == "builtin":
                overridden.append(template.id)
            templates[template.id] = template
            origins[template.id] = "user"
            paths[template.id] = path
    return TemplateCatalog(
        templates=templates,
        origins=origins,
        paths=paths,
        overridden=tuple(sorted(overridden)),
        errors=tuple(errors),
    )
