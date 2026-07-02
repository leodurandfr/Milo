"""Offline contract test: Milo-Mac (external macOS client) ⇄ Milo backend.

Milo-Mac (github.com/leodurandfr/Milo-Mac) is a SECOND consumer of the REST +
WebSocket API beyond `frontend/`. It is a separate app, not in this checkout,
and has no API versioning — so if the backend drops a route or WS event Milo-Mac
calls, Milo-Mac breaks silently at runtime. This test turns that into a loud
failure at `pytest` time.

The contract lives in `milo_mac_contract.json` (the source of truth, seeded from
Milo-Mac's real Swift source). This test asserts the backend still satisfies it,
with NO network access:

  * every REST entry resolves to a real FastAPI route (method + path template,
    path params included);
  * every WS (category, type) has an emission site in `backend/`: a typed
    `WsEvent` subclass from `core/models/ws_events.py` referenced outside its
    defining module (`broadcast(event)` is the sole emission API).

A separate, non-blocking CI job (`check_milo_mac_freshness.py`) re-clones
Milo-Mac and verifies the *manifest itself* still matches what Milo-Mac
consumes — that is the network half of the guard.

A static check that turns a silent contract break into an actionable failing test.
"""
import ast
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
CONTRACTS_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = CONTRACTS_DIR / "milo_mac_contract.json"
VENDOR_DIR = CONTRACTS_DIR / "vendor" / "milo-mac"

_MANIFEST = json.loads(MANIFEST_PATH.read_text())


def _load_freshness():
    """Import the Swift-surface extractors from the sibling freshness script.

    Loaded by file path rather than `import` so it resolves identically whether
    or not pytest treats backend/tests/contracts as a package.
    """
    path = CONTRACTS_DIR / "check_milo_mac_freshness.py"
    spec = importlib.util.spec_from_file_location("milo_mac_freshness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_FRESHNESS = _load_freshness()


# --------------------------------------------------------------------------- #
# REST: every manifest entry must resolve to a live FastAPI route.
# --------------------------------------------------------------------------- #

_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def _route_table():
    """(methods, path_template) for every HTTP route on the real app.

    Derived from the app's OpenAPI schema (`app.openapi()`) — the public,
    version-stable view of the route surface — NOT from iterating `app.routes`.
    FastAPI 0.137 / Starlette 1.3 stopped flattening `include_router()` routes
    into `app.routes`: each include now appears as a single opaque
    `_IncludedRouter` wrapper whose leaves are nested and whose prefix is no
    longer baked into the leaf paths, so the old flat iteration silently saw
    zero routes. OpenAPI paths are fully prefix-resolved and immune to that
    internal change. (Routes with `include_in_schema=False` are excluded, but no
    Milo-Mac route uses that.)

    Importing the fully-wired app from backend.main is offline (~1s): D-Bus /
    hardware services fail open under test, per CLAUDE.md.
    """
    from backend.main import app

    table = []
    for path, operations in app.openapi()["paths"].items():
        methods = {m.upper() for m in operations} & _HTTP_METHODS
        if methods:
            table.append((methods, path))
    return table


def _segments_match(manifest_path: str, route_path: str) -> bool:
    """Match a manifest path against a FastAPI path template, param-aware.

    A `{...}` segment on EITHER side is a wildcard: it matches any literal or
    any other `{...}`. This lets a manifest path that bakes in a concrete value
    (e.g. `/api/equalizer/target/local/enabled`) match a templated route
    (`/api/equalizer/target/{target}/enabled`), and vice-versa for params whose
    names differ (`{source}` vs `{source_name}`).
    """
    m = manifest_path.strip("/").split("/")
    r = route_path.strip("/").split("/")
    if len(m) != len(r):
        return False
    for ms, rs in zip(m, r):
        if ms.startswith("{") or rs.startswith("{"):
            continue
        if ms != rs:
            return False
    return True


@pytest.mark.parametrize(
    "entry",
    _MANIFEST["rest"],
    ids=[f"{e['method']} {e['path']}" for e in _MANIFEST["rest"]],
)
def test_rest_route_exists(entry):
    """Each REST path Milo-Mac calls must still resolve to a backend route."""
    method, path, consumer = entry["method"], entry["path"], entry["consumer"]

    matches = [
        route_path
        for methods, route_path in _route_table()
        if method in methods and _segments_match(path, route_path)
    ]

    assert matches, (
        f"Route `{method} {path}` was removed/renamed but is required by "
        f"Milo-Mac ({consumer}). It is not in this checkout — restore the route "
        f"or, if Milo-Mac genuinely dropped it, delete the entry from "
        f"{MANIFEST_PATH.name}. See CLAUDE.md §'External API clients — Milo-Mac'."
    )


# --------------------------------------------------------------------------- #
# WS: every manifest (category, type) must have an emission site — a typed
# WsEvent class actually used by the backend.
# --------------------------------------------------------------------------- #

def _scan_typed_events():
    """Static model of typed `broadcast(event)` emission.

    Typed events pin (CATEGORY, TYPE) at the class level, so an emission site
    is any reference to the event class outside `core/models/ws_events.py`
    (instantiation `SourceStateChanged(...)` or class handoff
    `progress_event_cls=SatelliteUpdateProgress`). Bare imports don't count —
    an imported-but-unused class is dead code ruff flags anyway.
    """
    from backend.core.models import ws_events

    def _concrete(cls):
        for sub in cls.__subclasses__():
            if "TYPE" in vars(sub):
                yield sub
            yield from _concrete(sub)

    class_to_pair = {
        cls.__name__: (cls.CATEGORY, cls.TYPE)
        for cls in _concrete(ws_events.WsEvent)
    }

    referenced_pairs: set[tuple[str, str]] = set()
    ws_events_path = BACKEND_ROOT / "core" / "models" / "ws_events.py"

    for py in BACKEND_ROOT.rglob("*.py"):
        if "/tests/" in py.as_posix() or py == ws_events_path:
            continue
        try:
            tree = ast.parse(py.read_text(), filename=str(py))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in class_to_pair:
                referenced_pairs.add(class_to_pair[node.id])

    return referenced_pairs

_TYPED = _scan_typed_events()


@pytest.mark.parametrize(
    "event",
    _MANIFEST["ws"]["events"],
    ids=[f"{e['category']}/{e['type']}" for e in _MANIFEST["ws"]["events"]],
)
def test_ws_broadcast_site_exists(event):
    """Each WS (category, type) Milo-Mac listens for must be broadcast somewhere."""
    category, evt_type = event["category"], event["type"]

    assert (category, evt_type) in _TYPED, (
        f"WS event `{category}/{evt_type}` has no emission site (no WsEvent "
        f"subclass referenced outside ws_events.py) but is required by "
        f"Milo-Mac ({event['consumer']}). Restore the broadcast or, if "
        f"Milo-Mac genuinely dropped it, delete the entry from "
        f"{MANIFEST_PATH.name}. See CLAUDE.md §'External API clients — Milo-Mac'."
    )


# --------------------------------------------------------------------------- #
# Vendored snapshot: the manifest must mirror Milo-Mac's real surface, offline.
# --------------------------------------------------------------------------- #

def test_manifest_matches_vendored_milo_mac():
    """The manifest must equal the surface the vendored Milo-Mac snapshot uses.

    vendor/milo-mac/ holds a committed copy of Milo-Mac's MiloAPIService.swift +
    WebSocketService.swift. We re-extract what they actually consume — offline,
    no clone — and require the manifest to match it, in BOTH directions:

      * snapshot - manifest → Milo-Mac consumes surface the manifest forgets to
        protect (the backend could delete it with every other test still green);
      * manifest - snapshot → the manifest declares dependencies Milo-Mac dropped
        (stale entries that over-constrain the backend).

    Exact match also self-guards the heuristic Swift extractors: if a Milo-Mac
    refactor breaks the regexes (they extract nothing / the wrong thing) this
    test fails loudly instead of silently passing on an empty surface. The
    network half — detecting when this snapshot falls behind the REAL upstream
    Milo-Mac — is the non-blocking check_milo_mac_freshness.py CI job.
    """
    api_swift = (VENDOR_DIR / "MiloAPIService.swift").read_text()
    ws_swift = (VENDOR_DIR / "WebSocketService.swift").read_text()

    # Self-guard: a regex-broken extractor must never read as "nothing consumed".
    assert _FRESHNESS.extract_rest(api_swift), "extract_rest() found no routes — extractor drift?"
    assert _FRESHNESS.extract_ws(ws_swift), "extract_ws() found no events — extractor drift?"

    errors, warnings = _FRESHNESS.compute_diff(_MANIFEST, api_swift, ws_swift)
    assert not errors and not warnings, (
        "Surface drift between the vendored Milo-Mac snapshot and the manifest:\n  "
        + "\n  ".join(errors + warnings)
        + "\nRefresh vendor/milo-mac/ and milo_mac_contract.json together, in one "
        "conscious commit. See CLAUDE.md §'External API clients — Milo-Mac'."
    )


def test_manifest_is_self_consistent():
    """Guard the manifest's own shape so a malformed edit fails loudly."""
    assert _MANIFEST["rest"], "manifest has no REST entries"
    assert _MANIFEST["ws"]["events"], "manifest has no WS events"
    for e in _MANIFEST["rest"]:
        assert e["method"] in {"GET", "POST", "PUT", "PATCH", "DELETE"}, e
        assert e["path"].startswith("/api/"), e
        assert e.get("consumer"), e
    for e in _MANIFEST["ws"]["events"]:
        assert e["category"] and e["type"] and e.get("consumer"), e
