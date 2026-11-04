"""Structural guardrail: a multiroom client is admitted exactly one way.

Four notifications can be the first to see a snapclient arrive — the sweep over
already-connected clients at WebSocket connect, `Client.OnConnect`,
`Server.OnUpdate` and the online-status flip the reconcile sweep detects — and
which one wins is a race decided by whether the backend or the satellite booted
first. So each of them must produce the same registry entry and leave the client
in the same state, and the only way that stays true is if they run the same code.

They did not, and the drift was invisible: nothing imports differently, no route
fails, no test goes red. It surfaced as bug reports that never reproduced,
because each depended on which notification had won:

  * **A path that skipped the pending lookup.** A speaker configured as "Bureau"
    was admitted under its Snapcast host name, and `register_client` preserves an
    existing non-empty name, so no later notification could repair it.
  * **A path that announced the client online before its volume reached the
    hardware, and discarded the result.** When the apply failed, snapserver and
    the registry then both read "online", no transition ever fired again, and the
    speaker stayed muted — CamillaDSP starts with `-m` — for as long as it was up.
  * **Two implementations of the same five-step recipe**, differing on retry, on
    the snapserver passthrough and on the buffer-config push, with no decision
    behind any of the three: which one ran depended on whether the caller
    happened to hold a Snapcast client id.

The rules below are structural on purpose. A behavioural test can only cover the
paths someone thought to write one for; these fail on a *fifth* path added later
that does its own thing. The registration and online-flag rules read the whole of
`backend/`, not one class, and read the state argument in keyword form too: a
heartbeat route in `api/multiroom.py` marking a silent satellite online, or a
`set_client_online(mac, online=True)`, is the same bypass as a fifth notification.

Doctrine note (same as the Milo-Mac / milo-client contract tests and the other
architecture guardrails): every extractor asserts its own output is non-trivial
first, so a broken parse fails loudly instead of passing on an empty surface.
"""
import ast
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
WEBSOCKET_PATH = BACKEND_ROOT / "core" / "multiroom" / "websocket.py"
WEBSOCKET_REL = WEBSOCKET_PATH.relative_to(BACKEND_ROOT).as_posix()

# The one scope allowed to announce a client online, and the one allowed to
# register it — as (file relative to backend/, dotted enclosing scope).
ONLINE_SETTER = (WEBSOCKET_REL, "SnapcastWebSocketService._do_sync_reconnecting_client_volume")
REGISTRATION = (WEBSOCKET_REL, "SnapcastWebSocketService._register_snapclient")

# `register_client` uses that do not reach ClientRegistryService, as
# (receiver, file, enclosing-scope prefix). Everything else counts as a registry
# registration, so a receiver nobody listed fails rather than passes.
FOREIGN_REGISTER_CLIENT = (
    ("pending_clients_service", "api/multiroom.py", ""),  # the setup wizard's queue
    ("self", "core/volume/state.py", "VolumeStateStore."),  # the volume cache seeding itself
)

# The recipe: what "bring this client to the state Milō holds for it" is made of.
# Named by the calls it issues, so the rule survives a rename of the function
# that issues them but not a second copy of the sequence.
RECIPE_CALLS = frozenset({
    "_resolve_target_volume",
    "_apply_target_volume_to_client",
    "_sync_standalone_equalizer_to_client",
})


@pytest.fixture(scope="module")
def service_functions() -> dict:
    """Every method of SnapcastWebSocketService, by name."""
    tree = ast.parse(WEBSOCKET_PATH.read_text())
    cls = next(
        (n for n in tree.body
         if isinstance(n, ast.ClassDef) and n.name == "SnapcastWebSocketService"),
        None,
    )
    assert cls is not None, f"SnapcastWebSocketService not found in {WEBSOCKET_PATH}"

    functions = {
        n.name: n for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert len(functions) > 20, f"only {len(functions)} methods parsed — extractor is broken"
    return functions


def _calls_named(node: ast.AST, name: str) -> list:
    """Calls to ``self.<name>(...)`` or ``self.<anything>.<name>(...)`` inside node."""
    return [
        sub for sub in ast.walk(node)
        if isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Attribute)
        and sub.func.attr == name
    ]


def _registry_calls(tree: ast.Module) -> list:
    """(enclosing scope, node) for every use of ``<x>.set_client_online/register_client``.

    The node is the Call when the method is called in place, and the bare
    Attribute when it escapes the call — aliased, passed as a callback, wrapped
    in a partial — which no rule can read, so both rules refuse it. The scope is
    the dotted path of the enclosing classes and functions (``Class.method``,
    ``create_router.route``), or ``<module>`` at top level.
    """
    found, called = [], set()

    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, scope + [child.name])
                continue
            if (isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr in {"set_client_online", "register_client"}):
                found.append((".".join(scope) or "<module>", child))
                called.add(id(child.func))
            elif (isinstance(child, ast.Attribute)
                    and child.attr in {"set_client_online", "register_client"}
                    and id(child) not in called):
                found.append((".".join(scope) or "<module>", child))
            visit(child, scope)

    visit(tree, [])
    return found


def _method(node) -> ast.Attribute:
    return node.func if isinstance(node, ast.Call) else node


def _online_state(node):
    """The state a set_client_online use passes: True, False, or None if unreadable."""
    if not isinstance(node, ast.Call):
        return None
    state = node.args[1] if len(node.args) > 1 else next(
        (kw.value for kw in node.keywords if kw.arg == "online"), None
    )
    if isinstance(state, ast.Constant) and isinstance(state.value, bool):
        return state.value
    return None


def _online_setters(rel: str, uses: list) -> tuple:
    """(legitimate, violations) among one module's set_client_online uses.

    An offline call is ignored. An online one is legitimate only in
    ONLINE_SETTER. A state that is not a literal bool — a variable, a missing
    argument, ``**kwargs``, a method that escapes the call — is a violation:
    the rule cannot tell, so it refuses.
    """
    legitimate, violations = [], []
    for scope, node in uses:
        if _method(node).attr != "set_client_online":
            continue
        state = _online_state(node)
        if state is False:
            continue
        where = f"{rel}::{scope}: {ast.unparse(node)}"
        if state is True and (rel, scope) == ONLINE_SETTER:
            legitimate.append(where)
        else:
            violations.append(where)
    return legitimate, violations


def _registrations(rel: str, uses: list) -> tuple:
    """(legitimate, violations) among one module's registry register_client uses."""
    legitimate, violations = [], []
    for scope, node in uses:
        method = _method(node)
        if method.attr != "register_client":
            continue
        receiver = ast.unparse(method.value)
        if any(receiver == r and rel == f and (scope + ".").startswith(prefix)
               for r, f, prefix in FOREIGN_REGISTER_CLIENT):
            continue
        where = f"{rel}::{scope}: {ast.unparse(node)}"
        if isinstance(node, ast.Call) and (rel, scope) == REGISTRATION:
            legitimate.append(where)
        else:
            violations.append(where)
    return legitimate, violations


@pytest.fixture(scope="module")
def backend_uses() -> dict:
    """Every production module of backend/ → its set_client_online/register_client uses."""
    uses = {
        p.relative_to(BACKEND_ROOT).as_posix(): _registry_calls(ast.parse(p.read_text()))
        for p in sorted(BACKEND_ROOT.rglob("*.py"))
        if "__pycache__" not in p.parts and "tests" not in p.relative_to(BACKEND_ROOT).parts
    }
    found = sum(len(f) for f in uses.values())
    assert len(uses) > 100 and found, (
        f"{len(uses)} modules parsed, {found} uses found — extractor is broken"
    )
    return uses


def test_one_registration_path(backend_uses):
    """`register_client` is reached only through `_register_snapclient`.

    That helper is where the pending entry is consulted and cleared, so a second
    caller is a path that drops the name, speaker type and volume_control the
    setup wizard just assigned — irrecoverably, since register_client preserves
    an existing non-empty name.
    """
    legitimate, violations = [], []
    for rel, uses in backend_uses.items():
        ok, bad = _registrations(rel, uses)
        legitimate += ok
        violations += bad

    assert legitimate, "no register_client call found in _register_snapclient — extractor is broken"
    assert not violations, (
        f"register_client is called from {violations}; every admission path must go "
        "through _register_snapclient so the pending identity is honored once"
    )


def test_one_implementation_of_the_admission_recipe(service_functions):
    """The five-step sync exists once.

    Two copies is the state this file was in: they had drifted on retry, on the
    snapserver passthrough and on the buffer-config push, and a client got one or
    the other depending on which notification announced it.
    """
    implementations = sorted(
        name for name, fn in service_functions.items()
        if RECIPE_CALLS <= {c.func.attr for c in ast.walk(fn)
                            if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
    )

    assert len(implementations) == 1, (
        f"{len(implementations)} functions issue the whole admission sequence "
        f"{sorted(RECIPE_CALLS)}: {implementations}. There is one recipe — call it, "
        "do not restate it"
    )


def test_the_recipe_owns_the_online_flag(backend_uses):
    """Nothing announces a client online *before* its volume reached the hardware.

    `set_client_online(mac, True)` is legitimate in exactly one place: the sync
    itself, once the hardware confirmed. Anywhere else is the window that left a
    muted speaker showing as online with nothing to retry it — and, at WebSocket
    connect, a known satellite back from a multiroom toggle shown online without
    the mute it was given while away.
    """
    legitimate, violations = [], []
    for rel, uses in backend_uses.items():
        ok, bad = _online_setters(rel, uses)
        legitimate += ok
        violations += bad

    assert legitimate, "no set_client_online(mac, True) call found — extractor is broken"
    assert not violations, (
        f"{violations} announce a client online directly (or with a state the rule "
        "cannot read); let the sync do it via set_online_after once the hardware confirmed"
    )


_DRIFTS = {
    "keyword online=True in a service method": (
        WEBSOCKET_REL, _online_setters, True,
        "class SnapcastWebSocketService:\n"
        "    async def _handle_client_connect(self, mac):\n"
        "        await self.registry.set_client_online(mac, online=True)\n",
    ),
    "heartbeat route marking a satellite online": (
        "api/multiroom.py", _online_setters, True,
        "def create_multiroom_router(registry_service):\n"
        "    async def register_client(request):\n"
        "        await registry_service.set_client_online(request.mac_id, True)\n",
    ),
    "module-level helper": (
        WEBSOCKET_REL, _online_setters, True,
        "async def _mark_online(registry, mac):\n"
        "    await registry.set_client_online(mac, True)\n",
    ),
    "state the rule cannot read": (
        WEBSOCKET_REL, _online_setters, True,
        "class SnapcastWebSocketService:\n"
        "    async def _do_sync_reconnecting_client_volume(self, mac, state):\n"
        "        await self.registry.set_client_online(mac, state)\n",
    ),
    "registry registration from a route": (
        "api/multiroom.py", _registrations, True,
        "def create_multiroom_router(registry_service):\n"
        "    async def register_client(request):\n"
        "        await registry_service.register_client(request.mac_id, 'x', '1.2.3.4')\n",
    ),
    "registration through an unlisted receiver": (
        WEBSOCKET_REL, _registrations, True,
        "class SnapcastWebSocketService:\n"
        "    async def _handle_client_connect(self, svc, mac):\n"
        "        await svc.register_client(mac, 'x', '1.2.3.4')\n",
    ),
    "bound method escaping the call": (
        "api/multiroom.py", _online_setters, True,
        "def create_multiroom_router(registry_service):\n"
        "    async def register_client(request):\n"
        "        mark = registry_service.set_client_online\n"
        "        await mark(request.mac_id, True)\n",
    ),
    "self.register_client outside the volume cache": (
        "core/volume/state.py", _registrations, True,
        "class SomethingElse:\n"
        "    async def _seed(self, mac):\n"
        "        await self.register_client(mac, 'x', '1.2.3.4')\n",
    ),
    "the volume cache seeding itself": (
        "core/volume/state.py", _registrations, False,
        "class VolumeStateStore:\n"
        "    async def _on_update(self, mac):\n"
        "        await self.register_client(mac, volume_db=-30.0)\n",
    ),
    "keyword online=False": (
        WEBSOCKET_REL, _online_setters, False,
        "class SnapcastWebSocketService:\n"
        "    async def _handle_client_disconnect(self, mac):\n"
        "        await self.registry.set_client_online(mac, online=False)\n",
    ),
    "the sync announcing online": (
        WEBSOCKET_REL, _online_setters, False,
        "class SnapcastWebSocketService:\n"
        "    async def _do_sync_reconnecting_client_volume(self, mac):\n"
        "        await self.registry.set_client_online(mac, online=True)\n",
    ),
    "the setup wizard's pending queue": (
        "api/multiroom.py", _registrations, False,
        "def create_multiroom_router(pending_clients_service):\n"
        "    async def register_client(request):\n"
        "        await pending_clients_service.register_client(request.mac_id)\n",
    ),
}


@pytest.mark.parametrize("case", sorted(_DRIFTS))
def test_the_admission_rules_see_every_drift_shape(case):
    """The two rules above flag each bypass shape, and only those.

    The extractor's own check against simulated drift: a keyword argument, a
    module function and a route in api/multiroom.py all escaped the previous
    version of these rules, which read one class and one positional argument.
    """
    rel, rule, flagged, source = _DRIFTS[case]
    _, violations = rule(rel, _registry_calls(ast.parse(source)))

    assert bool(violations) is flagged, (
        f"{case}: expected {'a violation' if flagged else 'no violation'}, got {violations}"
    )


def test_every_spawned_sync_waits_for_the_hardware(service_functions):
    """A path that spawns the sync passes set_online_after=True.

    Spawning it without that flag registers the client and then never shows it,
    since nothing else marks it online — the mirror image of the bug above, and
    just as silent.
    """
    spawns = [
        call for fn in service_functions.values()
        for call in _calls_named(fn, "_sync_reconnecting_client_volume")
    ]
    assert len(spawns) >= 3, f"only {len(spawns)} sync call sites found — extractor is broken"

    missing = [
        ast.unparse(call) for call in spawns
        if not any(
            kw.arg == "set_online_after"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in call.keywords
        )
    ]

    assert not missing, (
        "these admission paths sync without set_online_after=True, so the client "
        f"is registered and never shown: {missing}"
    )
