# SPDX-License-Identifier: Apache-2.0
"""Architecture tests for the safety boundary (plan phase-1 WP2, risk R8).

AST-based scan over ``suncore/``; no module is imported. The rules:

1. Only ``suncore.safety`` may call ``.apply(...)`` - the actuator write.
2. Only ``suncore.safety`` may import ``suncore.safety.actuator``.
3. Only ``suncore.ha`` and ``suncore.safety`` may reference an attribute
   named ``call_service`` (the connector exposes it only through a
   capability object handed to safety).
   The same holds for the name ``service_caller`` (the capability accessor) and
   for imports or references of ``ServiceCaller`` / ``ServiceCallerProvider``.
4. Dependency rule (docs/architecture.md §2): ``suncore.core`` imports no
   sibling package; ``suncore.safety`` imports only ``suncore.core`` and
   itself.
5. ``suncore.api`` imports none of ``suncore.safety``, ``suncore.ha``,
   ``suncore.devices`` (it talks to the ``Services`` protocol only).

The scan is robust to packages that are still empty (``suncore/ha`` until
WP3) because it only looks at the files that exist.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "suncore"

SAFETY = "suncore.safety"
HA = "suncore.ha"
CORE = "suncore.core"
ACTUATOR_MODULE = "suncore.safety.actuator"
API = "suncore.api"
API_FORBIDDEN = (SAFETY, HA, "suncore.devices")
WRITE_CAPABILITY_NAMES = frozenset({"ServiceCaller", "ServiceCallerProvider"})


def module_name(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def in_package(module: str, package: str) -> bool:
    return module == package or module.startswith(package + ".")


def source_files() -> list[Path]:
    files = sorted(PACKAGE.rglob("*.py"))
    assert files, "no sources found - wrong ROOT?"
    return files


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def resolve_import(module: str, node: ast.ImportFrom, is_package: bool) -> str:
    """Return the absolute module an ``ImportFrom`` refers to."""
    if node.level == 0:
        return node.module or ""
    base_parts = module.split(".")
    # Relative level 1 from a package __init__ is the package itself; from a
    # plain module it is the containing package.
    if not is_package:
        base_parts = base_parts[:-1]
    drop = node.level - 1
    if drop:
        base_parts = base_parts[: len(base_parts) - drop]
    base = ".".join(base_parts)
    return f"{base}.{node.module}" if node.module else base


def imported_modules(path: Path, tree: ast.Module) -> Iterator[tuple[int, str]]:
    """Yield ``(lineno, absolute module)`` for every import in the module.

    ``from pkg import name`` yields both ``pkg`` and ``pkg.name`` so that
    ``from suncore.safety import actuator`` is caught as well.
    """
    module = module_name(path)
    is_package = path.name == "__init__.py"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            base = resolve_import(module, node, is_package)
            yield node.lineno, base
            for alias in node.names:
                yield node.lineno, f"{base}.{alias.name}"


def attribute_calls(tree: ast.Module, attr: str) -> Iterator[int]:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == attr
        ):
            yield node.lineno


def attribute_refs(tree: ast.Module, attr: str) -> Iterator[int]:
    for node in ast.walk(tree):
        is_attr = isinstance(node, ast.Attribute) and node.attr == attr
        is_name = isinstance(node, ast.Name) and node.id == attr
        is_def = isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == attr
        if is_attr or is_name or is_def:
            yield node.lineno


def imported_names(tree: ast.Module) -> Iterator[tuple[int, str]]:
    """Yield ``(lineno, name)`` of every name bound by ``from x import name [as y]``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                yield node.lineno, alias.name


def fmt(path: Path, lines: Iterable[int]) -> list[str]:
    return [f"{path.relative_to(ROOT)}:{line}" for line in sorted(set(lines))]


SOURCES = source_files()


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_only_safety_calls_apply(path: Path) -> None:
    """Rule 1: ``.apply(`` is the actuator write and belongs to safety alone."""
    module = module_name(path)
    if in_package(module, SAFETY):
        return
    offenders = fmt(path, attribute_calls(parse(path), "apply"))
    assert not offenders, f"actuator write outside suncore.safety: {offenders}"


def test_phase_1_has_no_apply_call_anywhere() -> None:
    """Phase 1 ships no write path at all - not even inside safety.

    Remove this test when Phase 3 adds the automatic path behind the
    invariants; ``test_only_safety_calls_apply`` keeps guarding the boundary.
    """
    offenders = [
        line for path in SOURCES for line in fmt(path, attribute_calls(parse(path), "apply"))
    ]
    assert not offenders, f"Phase 1 must not contain an actuator write: {offenders}"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_only_safety_imports_the_actuator_module(path: Path) -> None:
    """Rule 2: the actuator protocol stays behind the gate.

    Actuator implementations (``suncore.devices``, Phase 3) satisfy the
    protocol structurally and need not import it. Phase 3 must extend this
    rule: driver modules may only be imported by ``safety`` and the
    composition root (``suncore.app``).
    """
    module = module_name(path)
    if in_package(module, SAFETY):
        return
    offenders = fmt(
        path,
        (
            line
            for line, imported in imported_modules(path, parse(path))
            if in_package(imported, ACTUATOR_MODULE)
        ),
    )
    assert not offenders, f"suncore.safety.actuator imported outside safety: {offenders}"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_call_service_is_confined_to_ha_and_safety(path: Path) -> None:
    """Rule 3: HA service calls are a write capability owned by ha/ and safety/."""
    module = module_name(path)
    if in_package(module, HA) or in_package(module, SAFETY):
        return
    offenders = fmt(path, attribute_refs(parse(path), "call_service"))
    assert not offenders, f"call_service referenced outside suncore.ha/safety: {offenders}"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_service_caller_is_confined_to_ha_and_safety(path: Path) -> None:
    """Rule 3b: the write capability and its accessor never leave ha/ and safety/."""
    module = module_name(path)
    if in_package(module, HA) or in_package(module, SAFETY):
        return
    tree = parse(path)
    lines = list(attribute_refs(tree, "service_caller"))
    for name in WRITE_CAPABILITY_NAMES:
        lines.extend(attribute_refs(tree, name))
        lines.extend(line for line, imported in imported_names(tree) if imported == name)
    assert not lines, (
        f"service_caller/ServiceCaller used outside suncore.ha/safety: {fmt(path, lines)}"
    )


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_api_does_not_import_write_side_packages(path: Path) -> None:
    """Rule 5: the API layer sees ``Services`` only, never safety, ha or devices."""
    module = module_name(path)
    if not in_package(module, API):
        return
    offenders = fmt(
        path,
        (
            line
            for line, imported in imported_modules(path, parse(path))
            if any(in_package(imported, pkg) for pkg in API_FORBIDDEN)
        ),
    )
    assert not offenders, f"{module} imports a write-side package: {offenders}"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_dependency_rule(path: Path) -> None:
    """Rule 4: core imports no sibling; safety imports only core and itself."""
    module = module_name(path)
    if in_package(module, CORE):
        allowed = (CORE,)
    elif in_package(module, SAFETY):
        allowed = (CORE, SAFETY)
    else:
        return
    offenders = fmt(
        path,
        (
            line
            for line, imported in imported_modules(path, parse(path))
            if in_package(imported, "suncore")
            and not any(in_package(imported, pkg) for pkg in allowed)
        ),
    )
    assert not offenders, f"{module} imports outside {allowed}: {offenders}"


# --- Self-tests of the scanner on synthetic sources --------------------------


def _tree(src: str) -> ast.Module:
    return ast.parse(src)


def test_scanner_detects_apply_calls() -> None:
    assert list(attribute_calls(_tree("await driver.apply(intent)"), "apply")) == [1]
    assert list(attribute_calls(_tree("x = obj.apply"), "apply")) == []
    assert list(attribute_calls(_tree("apply(intent)"), "apply")) == []


def test_scanner_detects_call_service_refs() -> None:
    assert list(attribute_refs(_tree("conn.call_service(a)"), "call_service")) == [1]
    assert list(attribute_refs(_tree("f = conn.call_service"), "call_service")) == [1]
    assert list(attribute_refs(_tree("from x import call_service"), "call_service")) == []
    assert list(attribute_refs(_tree("call_service(a)"), "call_service")) == [1]
    assert list(attribute_refs(_tree("def call_service(): ..."), "call_service")) == [1]


def test_scanner_detects_service_caller_uses() -> None:
    assert list(attribute_refs(_tree("c = conn.service_caller()"), "service_caller")) == [1]
    assert list(attribute_refs(_tree("service_caller()"), "service_caller")) == [1]
    assert list(attribute_refs(_tree("def service_caller(self): ..."), "service_caller")) == [1]
    assert list(attribute_refs(_tree("x: ha.ServiceCaller"), "ServiceCaller")) == [1]
    assert list(attribute_refs(_tree("x: ServiceCaller"), "ServiceCaller")) == [1]
    src = "from suncore.ha import EntityCache, ServiceCaller as SC\nimport suncore.ha\n"
    assert [n for _, n in imported_names(_tree(src))] == ["EntityCache", "ServiceCaller"]
    assert list(imported_names(_tree("from suncore.ha import ServiceCallerProvider"))) == [
        (1, "ServiceCallerProvider")
    ]


def test_scanner_detects_forbidden_api_imports() -> None:
    tmp = PACKAGE / "api" / "_synthetic.py"
    src = (
        "import suncore.ha.connection\n"
        "from suncore.safety import gate\n"
        "from ..devices import x\n"
        "from suncore.core import site\n"
    )
    hits = {
        line
        for line, imported in imported_modules(tmp, _tree(src))
        if any(in_package(imported, pkg) for pkg in API_FORBIDDEN)
    }
    assert hits == {1, 2, 3}


@pytest.mark.parametrize(
    ("module", "is_package", "src", "expected"),
    [
        (
            "suncore.safety.gate",
            False,
            "from suncore.safety.actuator import Actuator",
            "suncore.safety.actuator",
        ),
        ("suncore.safety.gate", False, "from .actuator import Actuator", "suncore.safety.actuator"),
        ("suncore.safety.gate", False, "from . import actuator", "suncore.safety"),
        ("suncore.safety.gate", False, "from ..core import decisions", "suncore.core"),
        ("suncore.safety", True, "from .actuator import Actuator", "suncore.safety.actuator"),
        ("suncore.safety", True, "from ..ha import x", "suncore.ha"),
    ],
)
def test_relative_imports_are_resolved(
    module: str, is_package: bool, src: str, expected: str
) -> None:
    node = _tree(src).body[0]
    assert isinstance(node, ast.ImportFrom)
    assert resolve_import(module, node, is_package) == expected


def test_import_scan_reports_submodule_imports() -> None:
    src = "from suncore.safety import actuator\nimport suncore.safety.actuator as a\n"
    tmp = PACKAGE / "devices" / "_synthetic.py"
    tree = ast.parse(src)
    found = {imported for _, imported in imported_modules(tmp, tree)}
    assert "suncore.safety.actuator" in found
