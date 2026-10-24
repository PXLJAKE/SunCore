# SPDX-License-Identifier: Apache-2.0
"""Architecture tests for the safety boundary (plan phase-1 WP2, risk R8).

AST-based scan over ``suncore/``; no module is imported. The rules:

1. Only ``suncore.safety.gate`` may call ``.apply(...)`` and ``.release(...)`` - the
   actuator write and the hardware release (Phase 3: the only write paths) - and the battery
   adapters' ``.arm_fallback(...)`` / ``.keep_alive(...)`` (Phase 4 WP10: the §3.5 fallback
   registers and the heartbeat re-write).
2. Only ``suncore.safety`` may import ``suncore.safety.actuator``; driver modules
   (``suncore.devices.drivers``, Phase 3 WP6, and the battery adapters
   ``suncore.devices.battery``, Phase 4 WP10) may only be imported by ``suncore.safety``,
   the composition root ``suncore.app`` and the simulator's closed-loop composition
   ``suncore.sim`` (Phase 3 WP8; nothing in production imports ``suncore.sim`` but the demo).
3. Only ``suncore.ha`` and ``suncore.safety`` (and the simulator, which *is* the fake
   Home Assistant and composes the closed-loop test bed, Phase 3 WP8) may reference an
   attribute named ``call_service`` (the connector exposes it only through a
   capability object handed to safety).
   The same holds for the name ``service_caller`` (the capability accessor) and
   for imports or references of ``ServiceCaller`` / ``ServiceCallerProvider``. The composition
   root never names them: it hands the *connection* and a driver factory to
   ``suncore.safety.wiring.build_actuators`` (part of ``safety``), and only
   ``suncore.app.control`` constructs the ``SafetyGate`` (Phase 3 WP9, last two tests).
4. Dependency rule (docs/architecture.md §2): ``suncore.core`` imports no
   sibling package; ``suncore.safety`` imports only ``suncore.core`` and
   itself; ``suncore.templates`` imports only ``suncore.core`` and itself (plus
   yaml/pydantic, ADR-0012); ``suncore.controller`` imports only ``suncore.core``
   and itself (Phase 3 WP7: drivers, power models and the gate are injected);
   ``suncore.tariff`` imports only ``suncore.core``, ``suncore.templates`` and itself (Phase 4
   WP4: Home Assistant reaches it through the injected action caller and entity reader);
   ``suncore.planner`` imports only ``suncore.core`` and itself (Phase 4 WP8: the runtime
   hands it the services' outputs).
5. ``suncore.api`` imports none of ``suncore.safety``, ``suncore.ha``,
   ``suncore.devices`` (it talks to the ``Services`` protocol only).
6. ``suncore.safety`` imports neither ``suncore.core.config`` nor ``suncore.storage`` and
   mentions neither ``OperationConfig`` nor the ``operation`` section: the gate learns the
   operating modes only through the injected ``ModeProvider`` (Phase 3 WP3) and the
   runtime only through the injected ``RuntimeStore``.
7. ``call_action_response`` (read-only action with ``return_response``) is defined and used
   only in ``suncore.ha`` (implementation, rate limited per action), ``suncore.sim`` (fake) and
   the one adapter wiring module ``suncore.app.adapters`` (Phase 4); no other part of
   ``suncore.app`` may use it. ``READ_ONLY_ACTIONS`` is the fixed allowlist.
8. ``statistics_during_period`` (read-only recorder statistics) is used only in ``suncore.ha``
   and ``suncore.sim`` (``suncore.app.history`` goes through ``suncore.ha.statistics``).
9. ``energy_solar_forecast`` (the read-only ``energy/solar_forecast`` command) is used only in
   ``suncore.ha`` and ``suncore.sim`` (``suncore.app.adapters`` goes through
   ``suncore.ha.fetch_solar_forecast``). It is a read: no write path changes (Phase 4 WP5).

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
TEMPLATES = "suncore.templates"
CONTROLLER = "suncore.controller"
TARIFF = "suncore.tariff"
PLANNER = "suncore.planner"
ACTUATOR_MODULE = "suncore.safety.actuator"
GATE_MODULE = "suncore.safety.gate"
DRIVERS = "suncore.devices.drivers"
BATTERY_DRIVERS = "suncore.devices.battery"
SIM = "suncore.sim"
DRIVER_IMPORTERS = (SAFETY, "suncore.app", SIM)
WRITE_CALLS = ("apply", "release", "arm_fallback", "keep_alive")
API = "suncore.api"
API_FORBIDDEN = (SAFETY, HA, "suncore.devices")
WRITE_CAPABILITY_NAMES = frozenset({"ServiceCaller", "ServiceCallerProvider"})
SAFETY_FORBIDDEN_IMPORTS = ("suncore.core.config", "suncore.storage")
STATISTICS_OWNERS = ("suncore.ha", "suncore.sim")
SOLAR_FORECAST_OWNERS = ("suncore.ha", "suncore.sim")
ACTION_RESPONSE_OWNERS = ("suncore.ha", "suncore.sim", "suncore.app.adapters")


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
def test_only_the_gate_calls_apply_and_release(path: Path) -> None:
    """Rule 1: ``.apply(`` / ``.release(`` are the hardware writes and belong to the gate."""
    module = module_name(path)
    if module == GATE_MODULE:
        return
    tree = parse(path)
    offenders = fmt(path, (line for attr in WRITE_CALLS for line in attribute_calls(tree, attr)))
    assert not offenders, f"actuator write outside suncore.safety.gate: {offenders}"


def test_the_gate_is_the_single_writer() -> None:
    """The write path exists exactly once: ``gate.py`` calls ``apply`` and ``release`` (and the
    battery adapters' ``arm_fallback`` and ``keep_alive``)."""
    gate = PACKAGE / "safety" / "gate.py"
    tree = parse(gate)
    for attr in ("apply", "release"):
        assert list(attribute_calls(tree, attr)), f"gate.py must contain the {attr}() call"
    everywhere = [
        line
        for path in SOURCES
        if path != gate
        for attr in WRITE_CALLS
        for line in fmt(path, attribute_calls(parse(path), attr))
    ]
    assert not everywhere, f"a second write path exists: {everywhere}"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_only_safety_imports_the_actuator_module(path: Path) -> None:
    """Rule 2: the actuator protocol stays behind the gate.

    Actuator implementations (``suncore.devices.drivers``, Phase 3 WP6) satisfy the
    protocol structurally and need not import it.
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
def test_drivers_are_imported_only_by_safety_and_app(path: Path) -> None:
    """Rule 2b: driver modules reach the gate through the composition root only.

    The drivers do not exist yet (WP6); the rule is in place before the first one lands.
    """
    module = module_name(path)
    if (
        in_package(module, DRIVERS)
        or in_package(module, BATTERY_DRIVERS)
        or any(in_package(module, pkg) for pkg in DRIVER_IMPORTERS)
    ):
        return
    offenders = fmt(
        path,
        (
            line
            for line, imported in imported_modules(path, parse(path))
            if in_package(imported, DRIVERS) or in_package(imported, BATTERY_DRIVERS)
        ),
    )
    assert not offenders, f"driver module imported outside safety/app: {offenders}"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_call_service_is_confined_to_ha_and_safety(path: Path) -> None:
    """Rule 3: HA service calls are a write capability owned by ha/ and safety/."""
    module = module_name(path)
    if in_package(module, HA) or in_package(module, SAFETY) or in_package(module, SIM):
        return
    offenders = fmt(path, attribute_refs(parse(path), "call_service"))
    assert not offenders, f"call_service referenced outside suncore.ha/safety: {offenders}"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_service_caller_is_confined_to_ha_and_safety(path: Path) -> None:
    """Rule 3b: the write capability and its accessor never leave ha/ and safety/."""
    module = module_name(path)
    if in_package(module, HA) or in_package(module, SAFETY) or in_package(module, SIM):
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
    """Rule 4: core imports no sibling; the other layered packages only what they may."""
    module = module_name(path)
    if in_package(module, CORE):
        allowed = (CORE,)
    elif in_package(module, SAFETY):
        allowed = (CORE, SAFETY)
    elif in_package(module, TEMPLATES):
        allowed = (CORE, TEMPLATES)
    elif in_package(module, CONTROLLER):
        allowed = (CORE, CONTROLLER)
    elif in_package(module, TARIFF):
        allowed = (CORE, TEMPLATES, TARIFF)
    elif in_package(module, PLANNER):
        allowed = (CORE, PLANNER)
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


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_safety_does_not_read_config_or_storage(path: Path) -> None:
    """Rule 6: no import of the config sections or the storage, no mention of ``operation``."""
    module = module_name(path)
    if not in_package(module, SAFETY):
        return
    tree = parse(path)
    imports = fmt(
        path,
        (
            line
            for line, imported in imported_modules(path, tree)
            if any(in_package(imported, bad) for bad in SAFETY_FORBIDDEN_IMPORTS)
        ),
    )
    names = fmt(path, attribute_refs(tree, "OperationConfig"))
    operation_strings = fmt(
        path,
        (
            n.lineno
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and n.value in ("operation", "device_modes")
        ),
    )
    assert not imports, f"safety imports config/storage: {imports}"
    assert not names, f"safety references OperationConfig: {names}"
    assert not operation_strings, f"safety mentions the operation section: {operation_strings}"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_call_action_response_is_confined(path: Path) -> None:
    """Rule 7: the response-returning call lives in ha (+ the fake) and app.adapters only."""
    module = module_name(path)
    if any(in_package(module, owner) for owner in ACTION_RESPONSE_OWNERS):
        return
    offenders = fmt(path, attribute_refs(parse(path), "call_action_response"))
    assert not offenders, (
        f"call_action_response referenced outside ha/sim/app.adapters: {offenders}"
    )


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_statistics_during_period_is_confined(path: Path) -> None:
    """Rule 8: the recorder statistics read lives in ha (+ the fake) only."""
    module = module_name(path)
    if any(in_package(module, owner) for owner in STATISTICS_OWNERS):
        return
    offenders = fmt(path, attribute_refs(parse(path), "statistics_during_period"))
    assert not offenders, f"statistics_during_period referenced outside ha/sim: {offenders}"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_energy_solar_forecast_is_confined(path: Path) -> None:
    """Rule 9: the Energy dashboard's forecast read lives in ha (+ the fake) only."""
    module = module_name(path)
    if any(in_package(module, owner) for owner in SOLAR_FORECAST_OWNERS):
        return
    offenders = fmt(path, attribute_refs(parse(path), "energy_solar_forecast"))
    assert not offenders, f"energy_solar_forecast referenced outside ha/sim: {offenders}"


def test_action_response_owner_is_the_adapter_module_not_the_whole_app() -> None:
    """Rule 7 admits ``suncore.app.adapters`` only: its siblings in ``suncore.app`` stay out."""
    assert "suncore.app" not in ACTION_RESPONSE_OWNERS
    assert (PACKAGE / "app" / "adapters.py").is_file()
    for owner in ACTION_RESPONSE_OWNERS:
        assert not in_package("suncore.app.control", owner)
        assert not in_package("suncore.app.runtime", owner)
        assert not in_package("suncore.app.adapters_extra", owner)
    assert in_package("suncore.app.adapters", "suncore.app.adapters")


def test_read_only_action_allowlist_is_fixed() -> None:
    """A new read-only action needs a PR that verifies it against the integration source."""
    from suncore.core.actions import READ_ONLY_ACTIONS, is_read_only_action  # noqa: PLC0415

    assert (
        frozenset(
            {
                "tibber.get_prices",
                "nordpool.get_prices_for_date",
                "solcast_solar.query_forecast_data",
                "weather.get_forecasts",
                "workday.check_date",
            }
        )
        == READ_ONLY_ACTIONS
    )
    assert not is_read_only_action("switch", "turn_on")
    assert not is_read_only_action("tibber", "get_prices.extra")


def test_the_safety_gate_never_sees_the_operation_section() -> None:
    """The gate learns modes only through the injected ``ModeProvider`` (Phase 3 design).

    No config section, no store and no mode constant: the constructor takes a ``modes``
    provider, and every mode read in ``gate.py`` goes through ``self._modes`` / ``view``.
    """
    gate_path = PACKAGE / "safety" / "gate.py"
    gate_source = gate_path.read_text(encoding="utf-8")
    assert "OperationConfig" not in gate_source
    assert "ConfigStore" not in gate_source
    tree = parse(gate_path)
    init = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
        and n.name == "__init__"
        and _in_class(tree, n, "SafetyGate")
    )
    params = {a.arg for a in init.args.kwonlyargs}
    assert "modes" in params, "SafetyGate must take the mode provider as `modes`"
    assert "mode" not in params, "the gate takes no mode value, only a provider"
    assert not any(
        isinstance(n, ast.Name) and n.id == "OperatingMode" and isinstance(n.ctx, ast.Store)
        for n in ast.walk(tree)
    )
    # Every `.global_mode()` / `.device_mode()` call is on the provider, never on config.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("global_mode", "device_mode")
        ):
            target = node.func.value
            assert (isinstance(target, ast.Attribute) and target.attr == "_modes") or (
                isinstance(target, ast.Name) and target.id == "provider"
            ), ast.dump(node)


def _in_class(tree: ast.Module, func: ast.FunctionDef, class_name: str) -> bool:
    return any(
        isinstance(n, ast.ClassDef) and n.name == class_name and func in n.body
        for n in ast.walk(tree)
    )


def test_scanner_detects_action_response_refs() -> None:
    assert list(
        attribute_refs(_tree("await c.call_action_response('a', 'b')"), "call_action_response")
    ) == [1]
    assert list(
        attribute_refs(_tree("async def call_action_response(): ..."), "call_action_response")
    ) == [1]


# --- Self-tests of the scanner on synthetic sources --------------------------


def _tree(src: str) -> ast.Module:
    return ast.parse(src)


def test_scanner_detects_apply_calls() -> None:
    assert list(attribute_calls(_tree("await driver.apply(intent)"), "apply")) == [1]
    assert list(attribute_calls(_tree("await driver.release(None)"), "release")) == [1]
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


def test_templates_package_exists_and_old_location_is_gone() -> None:
    assert (PACKAGE / "templates" / "models.py").is_file()
    assert not (PACKAGE / "devices" / "templates").exists()


def test_driver_rule_rejects_other_packages() -> None:
    tmp = PACKAGE / "controller" / "_synthetic.py"
    src = "from suncore.devices.drivers import onoff\nimport suncore.devices.power\n"
    hits = {
        line
        for line, imported in imported_modules(tmp, _tree(src))
        if in_package(imported, DRIVERS)
    }
    assert hits == {1}


def test_templates_rule_rejects_siblings() -> None:
    tmp = PACKAGE / "templates" / "_synthetic.py"
    src = "from suncore.ha import x\nfrom suncore.core import site\nimport suncore.devices\n"
    bad = {
        line
        for line, imported in imported_modules(tmp, _tree(src))
        if in_package(imported, "suncore")
        and not any(in_package(imported, pkg) for pkg in (CORE, TEMPLATES))
    }
    assert bad == {1, 3}


def test_only_the_control_stack_constructs_the_gate() -> None:
    """Phase 3 WP9: production code builds the gate in exactly one place.

    ``suncore.app.control.ControlStack`` is the composition root of the control layer (the
    simulator and the demo run the same class); a second ``SafetyGate(...)`` outside ``safety``
    would be a second, unreviewed wiring of drivers to the gate.
    """
    builders = []
    for path in SOURCES:
        module = module_name(path)
        if in_package(module, SAFETY):
            continue
        for node in ast.walk(parse(path)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name | ast.Attribute)
                and getattr(node.func, "id", getattr(node.func, "attr", "")) == "SafetyGate"
            ):
                builders.append(module)
    assert builders == ["suncore.app.control"]


def test_the_write_capability_is_taken_from_the_connection_inside_safety() -> None:
    """The composition root hands the *connection* to ``safety.wiring``; only that module (and
    ``ha``/``sim``) touches ``service_caller``, so rule 3b stays as strict as it was."""
    users = []
    for path in SOURCES:
        if list(attribute_refs(parse(path), "service_caller")):
            users.append(module_name(path))
    assert sorted(users) == [
        "suncore.ha.connection",
        "suncore.ha.websocket",
        "suncore.safety.wiring",
        "suncore.sim.fake_ha",
    ]
    app = PACKAGE / "app" / "control.py"
    assert "suncore.safety.wiring" in {m for _, m in imported_modules(app, parse(app))}
