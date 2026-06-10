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
  * every WS (category, type) has at least one `broadcast_event()` site in
    `backend/` (AST scan, handling the variable-`event_type` indirection).

A separate, non-blocking CI job (`check_milo_mac_freshness.py`) re-clones
Milo-Mac and verifies the *manifest itself* still matches what Milo-Mac
consumes — that is the network half of the guard.

Same philosophy as `test_breaking_changes_coherence`: a static check that turns
a silent contract break into an actionable failing test.
"""
import ast
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
MANIFEST_PATH = Path(__file__).resolve().parent / "milo_mac_contract.json"

_MANIFEST = json.loads(MANIFEST_PATH.read_text())


# --------------------------------------------------------------------------- #
# REST: every manifest entry must resolve to a live FastAPI route.
# --------------------------------------------------------------------------- #

def _route_table():
    """(methods, path_template) for every HTTP route on the real app.

    Imports the fully-wired app from backend.main. D-Bus / hardware services
    fail open under test (CLAUDE.md), so this import is offline and ~1s.
    """
    from backend.main import app

    return [
        (r.methods, r.path)
        for r in app.routes
        if hasattr(r, "methods") and getattr(r, "path", None)
    ]


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
# WS: every manifest (category, type) must have a broadcast_event() site.
# --------------------------------------------------------------------------- #

def _scan_broadcast_events():
    """Static model of `broadcast_event()` emission across backend/ (no tests).

    Returns three sets:
      * explicit_pairs       — (category, type) where both args are literals.
      * dynamic_categories   — categories broadcast with a non-literal type arg
                               (the type is decided at runtime / passed in).
      * dynamic_type_values  — string literals that flow into such broadcasts:
                               values assigned to / passed as `event_type`.

    A (category, type) is satisfied by an explicit pair, OR by a dynamic
    category whose type appears among the dynamic type values. This covers the
    `settings` helper indirection in api/settings.py, where
    `broadcast_event("settings", event_type, ...)` is fed by callers that pass
    `event_type="volume_limits_changed"`.
    """
    explicit_pairs: set[tuple[str, str]] = set()
    dynamic_categories: set[str] = set()
    dynamic_type_values: set[str] = set()

    def _str(node):
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

    for py in BACKEND_ROOT.rglob("*.py"):
        if "/tests/" in py.as_posix():
            continue
        try:
            tree = ast.parse(py.read_text(), filename=str(py))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            # event_type="..." flowing into a helper, or event_type = "..."
            for kw in node.keywords:
                if kw.arg == "event_type" and (v := _str(kw.value)) is not None:
                    dynamic_type_values.add(v)

            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "broadcast_event"):
                continue
            if len(node.args) < 2:
                continue

            category = _str(node.args[0])
            evt_type = _str(node.args[1])
            if category is None:
                continue
            if evt_type is not None:
                explicit_pairs.add((category, evt_type))
            else:
                dynamic_categories.add(category)

        # event_type = "..." simple assignments (belt-and-suspenders).
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                if any(isinstance(t, ast.Name) and t.id == "event_type" for t in node.targets):
                    if (v := _str(node.value)) is not None:
                        dynamic_type_values.add(v)

    return explicit_pairs, dynamic_categories, dynamic_type_values


_EXPLICIT, _DYNAMIC_CATS, _DYNAMIC_TYPES = _scan_broadcast_events()


@pytest.mark.parametrize(
    "event",
    _MANIFEST["ws"]["events"],
    ids=[f"{e['category']}/{e['type']}" for e in _MANIFEST["ws"]["events"]],
)
def test_ws_broadcast_site_exists(event):
    """Each WS (category, type) Milo-Mac listens for must be broadcast somewhere."""
    category, evt_type = event["category"], event["type"]

    satisfied = (category, evt_type) in _EXPLICIT or (
        category in _DYNAMIC_CATS and evt_type in _DYNAMIC_TYPES
    )

    assert satisfied, (
        f"WS event `{category}/{evt_type}` has no broadcast_event() site but is "
        f"required by Milo-Mac ({event['consumer']}). Restore the broadcast or, "
        f"if Milo-Mac genuinely dropped it, delete the entry from "
        f"{MANIFEST_PATH.name}. See CLAUDE.md §'External API clients — Milo-Mac'."
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
