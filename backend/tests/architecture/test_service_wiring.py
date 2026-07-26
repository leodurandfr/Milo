"""Structural guardrail: how `core/` services are wired, and what they own.

`CLAUDE.md § Core code rules` states three wiring rules that nothing verified.
Each one had already been broken in production code when this test was written:

  * **Encapsulation** — never touch another service's `_private` attrs from a
    route or another service. `RadioSource._do_start` read
    `self._station_data._loaded`.
  * **Background tasks** — no raw `asyncio.create_task` for fire-and-forget;
    a service owning a `BackgroundTaskSet` drains it in `cleanup()`, and the
    lifespan handler calls that `cleanup()`. `CrossoverService.cleanup()` was
    never called; `AudioRoutingService` and `HostnameConflictService` had no
    `cleanup()` at all.
  * **One wiring path** — `dependencies.py::_create_service` injects the
    acyclic dependencies and `initialize_services()` breaks the real cycles
    with setters. Ten setters existed for dependencies the factory already
    passed: a second way to wire the same thing, kept alive only by tests, and
    a false signal that a service supports late binding it never gets.

All three are mechanical and cheap to check, and all three are the kind of
regression that reappears the moment someone adds a service.

Doctrine note (same as the Milo-Mac / milo-client contract tests and the
frontend guardrails): every extractor asserts its own output is non-trivial
first, so a broken parse fails loudly instead of passing on an empty surface.
"""
import ast
import re
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent


def _modules(*subdirs):
    roots = [BACKEND_ROOT / d for d in subdirs] if subdirs else [BACKEND_ROOT]
    return sorted(
        p for root in roots for p in root.rglob("*.py")
        if "__pycache__" not in p.parts and "tests" not in p.parts
    )


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


_PRODUCTION = _modules("core", "shared", "sources", "hardware", "ws", "api") + [
    BACKEND_ROOT / "dependencies.py",
    BACKEND_ROOT / "main.py",
]
_TREES = {}
for _path in _PRODUCTION:
    try:
        _TREES[_path] = ast.parse(_path.read_text())
    except SyntaxError as exc:  # pragma: no cover - a broken tree is a real failure
        raise AssertionError(f"cannot parse {_path}: {exc}") from exc


def test_extractor_sees_the_whole_backend():
    """A collection bug must fail here, not silently pass every rule below."""
    assert len(_TREES) >= 100, f"only {len(_TREES)} production modules parsed"
    assert (BACKEND_ROOT / "dependencies.py") in _TREES


# --------------------------------------------------------------------------- #
# Encapsulation: no service reaches into another object's privates.
# --------------------------------------------------------------------------- #

# `self._x` / `cls._x` are the object's own state. A dunder (`obj.__class__`)
# is not private state. Everything else — `other_service._cache`,
# `self._collaborator._flag` — is someone else's business.
_ALLOWED_RECEIVERS = {"self", "cls"}


def test_no_cross_object_private_access():
    """Reading another object's `_private` pins a name that is free to change.

    Expose a public method or property on the owner instead. A collaborator's
    public API is fine; its underscore-prefixed internals are not.
    """
    violations = []
    for path, tree in _TREES.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if not node.attr.startswith("_") or node.attr.startswith("__"):
                continue
            receiver = node.value
            if isinstance(receiver, ast.Name) and receiver.id in _ALLOWED_RECEIVERS:
                continue
            if isinstance(receiver, ast.Attribute) and receiver.attr.startswith("_"):
                # self._collaborator._flag — the collaborator is not us.
                if isinstance(receiver.value, ast.Name) and receiver.value.id in _ALLOWED_RECEIVERS:
                    violations.append(f"{_rel(path)}:{node.lineno}: self.{receiver.attr}.{node.attr}")
                continue
            if isinstance(receiver, ast.Name):
                violations.append(f"{_rel(path)}:{node.lineno}: {receiver.id}.{node.attr}")

    assert not violations, (
        "cross-object private access (CLAUDE.md § Core code rules — Encapsulation):\n  "
        + "\n  ".join(sorted(violations))
    )


# --------------------------------------------------------------------------- #
# Background tasks: a service that spawns must drain, and main.py must call it.
# --------------------------------------------------------------------------- #

def _classes_owning_a_task_set():
    """(module, class) for every class constructing a BackgroundTaskSet."""
    owners = []
    for path, tree in _TREES.items():
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            constructs = any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "BackgroundTaskSet"
                for n in ast.walk(cls)
            )
            if constructs:
                owners.append((path, cls))
    return owners


_TEARDOWN_NAMES = ("cleanup", "shutdown", "stop", "stop_connection", "close")


def _registry_class_names():
    """Class names `dependencies.py` builds — the ones main.py can tear down."""
    src = (BACKEND_ROOT / "dependencies.py").read_text()
    names = dict(re.findall(r'"([a-z_]+)": lambda: _import\("[^"]+", "(\w+)"\)', src))
    assert len(names) >= 25, f"service registry extraction looks broken: {names}"
    return names


def test_task_set_owners_drain_where_they_tear_down():
    """`BackgroundTaskSet` without `cancel_all()` leaks on shutdown.

    A systemd stop then drops in-flight work with the event loop instead of
    cancelling it — including tasks that sleep for tens of seconds
    (`_delayed_multiroom_sync` waits up to 15s on snapserver readiness).

    Two obligations, because not every owner has a lifecycle: a service the
    registry builds must have a teardown method *and* drain in it; anything else
    (a logging handler, a hardware click dispatcher — process-lifetime objects
    with no owner to call them) only has to drain in whatever teardown it does
    declare. Inventing a cleanup() nobody calls would fail the next test anyway.
    """
    owners = _classes_owning_a_task_set()
    assert len(owners) >= 10, f"BackgroundTaskSet extraction looks broken: {len(owners)} owners"

    registry_classes = set(_registry_class_names().values())
    violations = []
    for path, cls in owners:
        teardown = [
            m for m in cls.body
            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name in _TEARDOWN_NAMES
        ]
        if not teardown:
            if cls.name in registry_classes:
                violations.append(f"{_rel(path)}::{cls.name} is a registry service with no teardown method")
            continue
        if not any("cancel_all" in ast.unparse(m) for m in teardown):
            violations.append(
                f"{_rel(path)}::{cls.name} tears down via "
                f"{'/'.join(m.name for m in teardown)} but never calls _bg.cancel_all()"
            )

    assert not violations, (
        "BackgroundTaskSet owners that do not drain (CLAUDE.md § Core code rules "
        "— Background tasks):\n  " + "\n  ".join(sorted(violations))
    )


def test_registry_services_with_cleanup_are_called_on_shutdown():
    """A cleanup() nobody calls is not a cleanup.

    `CrossoverService.cleanup()` existed for months while the lifespan handler
    never invoked it, so its per-client retry tasks outlived every shutdown.
    """
    main_src = (BACKEND_ROOT / "main.py").read_text()
    shutdown = main_src.split("yield", 1)[1] if "yield" in main_src else ""
    assert "cleanup" in shutdown, "could not locate main.py's shutdown block"

    # Map a registry name to the class it builds, then to its module.
    class_of = _registry_class_names()
    classes = {
        cls.name: (path, cls)
        for path, tree in _TREES.items()
        for cls in ast.walk(tree)
        if isinstance(cls, ast.ClassDef)
    }

    uncalled = []
    for service_name, cls_name in sorted(class_of.items()):
        entry = classes.get(cls_name)
        if entry is None:
            continue
        path, cls = entry
        if "core/" not in _rel(path):
            continue  # sources and hardware are torn down by their own owners
        has_cleanup = any(
            isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name in ("cleanup", "shutdown")
            for m in cls.body
        )
        if not has_cleanup:
            continue
        # main.py may hold the service in a local or resolve it inline.
        called = re.search(rf'\b{service_name}\b[^\n]*\.(cleanup|shutdown)\(', shutdown) or re.search(
            rf'get_service\("{service_name}"\)\.(cleanup|shutdown)\(', shutdown
        )
        local = re.search(rf'^(\w+) = get_service\("{service_name}"\)', main_src, re.M)
        if not called and local:
            called = re.search(rf'\b{local.group(1)}\b\.(cleanup|shutdown)\(', shutdown)
        if not called:
            uncalled.append(f"{cls_name}.cleanup() is never called in main.py's shutdown")

    assert not uncalled, "\n  ".join(["services that clean up nothing:"] + sorted(uncalled))


def test_no_untracked_fire_and_forget_tasks():
    """Raw `create_task` is allowed only when the task is kept and awaited.

    Permitted: stored on `self` (a tracked long-running loop), bound to a local
    that is later cancelled or gathered, or `BackgroundTaskSet.spawn`'s own
    primitive. Anything else is fire-and-forget whose exception nobody logs.
    """
    violations = []
    for path, tree in _TREES.items():
        source = path.read_text()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "create_task":
                continue
            parent = next(
                (
                    p for p in ast.walk(tree)
                    if isinstance(p, (ast.Assign, ast.AnnAssign, ast.DictComp, ast.Return))
                    and node in list(ast.walk(p))
                ),
                None,
            )
            if parent is not None:
                continue
            line = source.splitlines()[node.lineno - 1].strip()
            violations.append(f"{_rel(path)}:{node.lineno}: {line}")

    assert not violations, (
        "untracked fire-and-forget create_task (CLAUDE.md § Core code rules — use "
        "BackgroundTaskSet.spawn):\n  " + "\n  ".join(sorted(violations))
    )


# --------------------------------------------------------------------------- #
# One wiring path: no setter for a dependency the factory already injects.
# --------------------------------------------------------------------------- #

def test_no_injection_setter_without_a_production_caller():
    """A setter with no caller is a second wiring path that only tests use.

    It also lies about the class: a `set_registry` implies `_registry` can
    arrive late, so a reader cannot tell whether the None-guards are reachable.
    If `dependencies.py` constructor-injects the dependency, delete the setter;
    if it genuinely breaks an A-to-B cycle, `initialize_services()` calls it and
    this passes.
    """
    setters = []
    for path, tree in _TREES.items():
        if "core/" not in _rel(path):
            continue
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            for m in cls.body:
                if not isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not m.name.startswith("set_") or len(m.args.args) != 2:
                    continue
                param = m.args.args[1].arg
                body = ast.unparse(m)
                # An injection setter does nothing but store its argument.
                if not re.search(rf"self\.\w+\s*=\s*{re.escape(param)}\b", body):
                    continue
                if len([n for n in ast.walk(m) if isinstance(n, ast.stmt)]) > 4:
                    continue  # does real work (subscribes, recomputes) — not pure injection
                setters.append((path, cls.name, m.name, m.lineno))

    assert len(setters) >= 5, f"injection-setter extraction looks broken: {setters}"

    callers = "\n".join(p.read_text() for p in _PRODUCTION)
    orphans = [
        f"{_rel(path)}:{lineno}: {cls}.{name}()"
        for path, cls, name, lineno in setters
        if not re.search(rf"\.{re.escape(name)}\s*\(", callers.replace(f"def {name}(", ""))
    ]
    assert not orphans, (
        "injection setters with no production caller — dependencies.py already "
        "constructor-injects these:\n  " + "\n  ".join(sorted(orphans))
    )


# --------------------------------------------------------------------------- #
# The pending-settings queue: producers and dispatch must agree.
# --------------------------------------------------------------------------- #

def test_pending_setting_types_match_their_dispatch():
    """A queued type the dispatch ignores is discarded, silently.

    `apply_pending_settings` pops the whole per-client dict, so a type it does
    not handle vanishes with no log and no retry. "mono" and "enabled" were
    queued by the reconnection sync and dropped here for exactly that reason.
    """
    from backend.core.multiroom.crossover import PENDING_SETTING_TYPES

    crossover_src = (BACKEND_ROOT / "core" / "multiroom" / "crossover.py").read_text()
    dispatch_body = crossover_src.split("async def apply_pending_settings", 1)[1]
    dispatched = set(re.findall(r'"([a-z]+)" in pending', dispatch_body))
    # crossover/lowpass share one loop rather than an `in pending` branch each.
    for group in re.findall(r"for filter_name in \(([^)]*)\)", dispatch_body):
        dispatched |= set(re.findall(r'"([a-z]+)"', group))
    assert len(dispatched) >= 5, f"dispatch extraction looks broken: {dispatched}"

    queued = set()
    for path, tree in _TREES.items():
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "queue_pending_settings" or len(node.args) < 2:
                continue
            arg = node.args[1]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                queued.add(arg.value)
            elif isinstance(arg, ast.Name):
                queued |= {"crossover", "lowpass"}  # _set_client_filter's two filter names
    assert queued, "no queue_pending_settings producer found"

    assert queued <= set(PENDING_SETTING_TYPES), (
        f"queued but not declared in PENDING_SETTING_TYPES: {sorted(queued - set(PENDING_SETTING_TYPES))}"
    )
    assert set(PENDING_SETTING_TYPES) == dispatched, (
        f"declared but never replayed: {sorted(set(PENDING_SETTING_TYPES) - dispatched)}; "
        f"replayed but not declared: {sorted(dispatched - set(PENDING_SETTING_TYPES))}"
    )
