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
that does its own thing.

Doctrine note (same as the Milo-Mac / milo-client contract tests and the other
architecture guardrails): every extractor asserts its own output is non-trivial
first, so a broken parse fails loudly instead of passing on an empty surface.
"""
import ast
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
WEBSOCKET_PATH = BACKEND_ROOT / "core" / "multiroom" / "websocket.py"

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


def test_one_registration_path(service_functions):
    """`register_client` is reached only through `_register_snapclient`.

    That helper is where the pending entry is consulted and cleared, so a second
    caller is a path that drops the name, speaker type and volume_control the
    setup wizard just assigned — irrecoverably, since register_client preserves
    an existing non-empty name.
    """
    callers = sorted(
        name for name, fn in service_functions.items()
        if _calls_named(fn, "register_client")
    )

    assert callers == ["_register_snapclient"], (
        f"register_client is called from {callers}; every admission path must go "
        "through _register_snapclient so the pending identity is honoured once"
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


def test_the_recipe_owns_the_online_flag(service_functions):
    """Nothing announces a client online *before* its volume reached the hardware.

    `set_client_online(mac, True)` is legitimate in exactly two places: the sync
    itself, once the hardware confirmed, and the readmission of an already-known
    client at WebSocket connect, where no sync is due because the satellite never
    stopped playing. Anywhere else is the window that left a muted speaker showing
    as online with nothing to retry it.
    """
    allowed = {"_do_sync_reconnecting_client_volume", "_initialize_existing_clients"}

    setters = set()
    for name, fn in service_functions.items():
        for call in _calls_named(fn, "set_client_online"):
            positional = call.args[1] if len(call.args) > 1 else None
            if isinstance(positional, ast.Constant) and positional.value is True:
                setters.add(name)

    assert setters, "no set_client_online(mac, True) call found — extractor is broken"
    assert setters <= allowed, (
        f"{sorted(setters - allowed)} announce a client online directly; let the "
        "sync do it via set_online_after once the hardware confirmed"
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
