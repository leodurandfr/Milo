"""Offline contract test: Milo-iOS (external iOS client) ⇄ Milo backend.

Milo-iOS (github.com/leodurandfr/Milo-iOS) is a THIRD consumer of the REST API
beyond `frontend/` and Milo-Mac. Its consumers are short-lived processes — a
WidgetKit timeline entry, App Intents — so it holds no WebSocket and cannot be
told about anything: every dependency it has is a route it calls and a field it
decodes. Drop either and it breaks silently at runtime.

`cf430ae6` removed `GET /api/settings/dock-apps` as "Milo-Mac-only" after the Mac
converged on `/bulk`. Milo-iOS' client layer targets it too, and nothing in this
checkout recorded that. The break is latent — no caller reaches `getDockApps` in
the published build, so nobody is broken today — and that is the point: nobody
could have known either way. A route with no caller in `frontend/src/` is not
dead, it is unwitnessed, and this file is what witnesses it.

Four guards, all offline:

  * every route the manifest declares still resolves to a FastAPI route;
  * the manifest and the VENDORED snapshot describe the same surface, exactly —
    neither may depend on something the other does not know about;
  * a route the app declares that the backend does not serve is listed in
    `_broken_calls`, and stops being listed the moment either side moves;
  * every field the app reads by name still exists on the typed response model.

`check_milo_ios_freshness.py` is the network half, non-blocking, in CI.
"""
import importlib.util
import json
from pathlib import Path

import pytest

CONTRACTS_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = CONTRACTS_DIR / "milo_ios_contract.json"
VENDOR_DIR = CONTRACTS_DIR / "vendor" / "milo-ios"

_MANIFEST = json.loads(MANIFEST_PATH.read_text())


def _load_freshness():
    """Import the Swift extractors from the sibling freshness script.

    By file path rather than `import`, so it resolves identically whether or not
    pytest treats backend/tests/contracts as a package — the same reason the
    Milo-Mac test does it.
    """
    path = CONTRACTS_DIR / "check_milo_ios_freshness.py"
    spec = importlib.util.spec_from_file_location("milo_ios_freshness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_FRESHNESS = _load_freshness()
_VENDORED_SWIFT = _FRESHNESS._read_source(VENDOR_DIR)


def _route_table():
    """{(METHOD, path)} the backend actually serves.

    From the OpenAPI schema and not `app.routes`: FastAPI 0.137 stopped
    flattening `include_router()` into that tree, so iterating it silently sees
    zero routes — and a guard that sees nothing passes everything.
    """
    from backend.main import app

    return {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }


_ROUTES = _route_table()
assert len(_ROUTES) > 150, f"only {len(_ROUTES)} routes found — extractor broken?"


def _segments_match(manifest_path: str, route_path: str) -> bool:
    """Path-template match where `{...}` on EITHER side is a wildcard.

    The manifest spells params the way the app does (`{id}`, `{name}`); the
    backend spells them its own way (`{album_id}`, `{source_name}`). Comparing
    literally would report every parameterised route as missing.
    """
    m = manifest_path.strip("/").split("/")
    r = route_path.strip("/").split("/")
    if len(m) != len(r):
        return False
    return all(
        ms.startswith("{") or rs.startswith("{") or ms == rs
        for ms, rs in zip(m, r)
    )


# --------------------------------------------------------------------------- #
# 1. The backend still serves what the app calls.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "entry",
    _MANIFEST["rest"],
    ids=[f"{e['method']} {e['path']}" for e in _MANIFEST["rest"]],
)
def test_rest_route_exists(entry):
    """Each route Milo-iOS calls must still resolve to a backend route."""
    method, path = entry["method"], entry["path"]

    matches = [
        route_path
        for route_method, route_path in _ROUTES
        if route_method == method and _segments_match(path, route_path)
    ]

    assert matches, (
        f"Route `{method} {path}` was removed or renamed but Milo-iOS calls it"
        f"{' (' + entry['consumer'] + ')' if 'consumer' in entry else ''}. "
        f"Restore it, or — if Milo-iOS genuinely dropped it — delete the entry "
        f"from {MANIFEST_PATH.name} together with the vendored snapshot. "
        f"This is exactly how GET /api/settings/dock-apps was lost."
    )


# --------------------------------------------------------------------------- #
# 2/3. Manifest and vendored snapshot agree, and the gap is declared.
# --------------------------------------------------------------------------- #

def test_the_extractor_reads_both_call_shapes():
    """The surface reader must find routes through BOTH shapes the app uses.

    `get(path: "…")` is the helper; `fireAdjustVolume` hand-builds its URL with
    `URL(string: baseURL() + "…")` and sets `httpMethod` separately. An extractor
    modelling only the helper reports a smaller surface with no error, and a
    route missing from it reads as "the app does not use it" — the silence this
    whole contract exists to break. Asserted before anything trusts the
    extraction: a guard that cannot see is a guard that passes.
    """
    surface = _FRESHNESS.extract_rest(_VENDORED_SWIFT)

    assert ("GET", "/api/volume/state") in surface, "helper call shape not read"
    assert ("POST", "/api/volume/adjust") in surface, "hand-built URL shape not read"
    assert ("POST", "/api/audio/source/{}") in surface, "interpolated path not collapsed"


def test_manifest_matches_the_vendored_surface_exactly():
    """Manifest and snapshot must describe the same surface, in both directions.

    Undeclared: the app targets a route the manifest does not account for, so the
    backend is free to delete it as dead code — the failure this contract exists
    for. Unexplained: the manifest pins a route the app no longer targets, which
    freezes backend surface for nobody.

    Equality, not containment. An earlier draft tolerated a manifest that led the
    published app, for a batch of routes that were then cancelled — a tolerance
    outlives the reason for it, an equality cannot.
    """
    undeclared, unexplained = _FRESHNESS.compute_diff(_MANIFEST, _VENDORED_SWIFT)

    assert not undeclared, (
        f"the vendored Milo-iOS snapshot targets routes the manifest does not "
        f"account for: {sorted(undeclared)}. Add them to {MANIFEST_PATH.name}."
    )
    assert not unexplained, (
        f"the manifest pins routes the vendored snapshot no longer targets: "
        f"{sorted(unexplained)}. Either Milo-iOS dropped them (prune the entries) "
        f"or the snapshot is stale (refresh it, together with the manifest)."
    )


@pytest.mark.parametrize(
    "entry",
    _MANIFEST["_broken_calls"]["routes"],
    ids=[f"{e['method']} {e['path']}" for e in _MANIFEST["_broken_calls"]["routes"]],
)
def test_broken_calls_are_still_broken(entry):
    """A listed client-side defect must still be one, on BOTH sides.

    `GET /api/settings/dock-apps` is the first entry: the app calls it, the
    backend serves only PUT on that path, and the call has answered 405 since
    `cf430ae6`. It cannot go in `rest` — the route does not exist — and leaving
    it unlisted would make the contract silent about a client already broken.

    So it is listed, and this is what stops the list becoming a permanent
    exemption. The entry is stale, and fails here, as soon as either side moves:
    the backend starts serving the route, or the app stops calling it. Removing
    it is then the only way back to green.
    """
    method, path = entry["method"].upper(), entry["path"]

    served = [p for m, p in _ROUTES if m == method and _segments_match(path, p)]
    assert not served, (
        f"the backend now serves `{method} {path}` — Milo-iOS' call is no longer "
        f"broken. Move the entry from `_broken_calls` into `rest`."
    )

    called = _FRESHNESS.extract_rest(_VENDORED_SWIFT)
    assert (method, _FRESHNESS._shape(path)) in called, (
        f"the vendored app no longer calls `{method} {path}` — it was fixed "
        f"({entry.get('replacement', '')}). Delete the entry from `_broken_calls`."
    )


def test_a_declared_route_the_snapshot_shows_names_its_consumer():
    """A route the snapshot proves the app calls must say WHERE it calls it from.

    The `consumer` is what makes a contract failure actionable — it points at the
    Swift function to fix. It is required only where the snapshot can back it:
    demanding one for a `_pending_push` route would be asking for a name nobody
    can check.
    """
    called = _FRESHNESS.extract_rest(_VENDORED_SWIFT)
    missing = [
        f"{e['method']} {e['path']}"
        for e in _MANIFEST["rest"]
        if (e["method"].upper(), _FRESHNESS._shape(e["path"])) in called
        and not e.get("consumer")
    ]
    assert not missing, f"declared, called by the snapshot, no consumer named: {missing}"


# --------------------------------------------------------------------------- #
# 4. The fields the app decodes by name still exist.
# --------------------------------------------------------------------------- #

def _resolve(node: dict, spec: dict) -> dict:
    """Follow `$ref` and unwrap the Optional `anyOf` FastAPI emits."""
    while True:
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            node = spec["components"]["schemas"][name]
            continue
        if "anyOf" in node:
            concrete = [b for b in node["anyOf"] if b.get("type") != "null"]
            if len(concrete) == 1:
                node = concrete[0]
                continue
        return node


def _walk(dotted: str, node: dict, spec: dict) -> bool:
    """Resolve `a.b[k].c` through an OpenAPI schema.

    `a.b` is a property; `a[k]` steps into `additionalProperties`, the shape a
    `Dict[str, Model]` takes. Returns False the moment a step is unresolvable —
    which is the whole point: a renamed field must fail, not be assumed present.
    """
    for step in dotted.split("."):
        keyed = step.endswith("]")
        name = step.split("[", 1)[0] if keyed else step

        node = _resolve(node, spec)
        properties = node.get("properties", {})
        if name not in properties:
            return False
        node = properties[name]

        if keyed:
            node = _resolve(node, spec)
            extra = node.get("additionalProperties")
            if not isinstance(extra, dict):
                return False
            node = extra
    return True


def _response_schema(path: str, spec: dict) -> dict:
    """The 200 response schema of a manifest path's single declared method."""
    entry = next(e for e in _MANIFEST["rest"] if e["path"] == path)
    matches = [p for _, p in _ROUTES if _segments_match(path, p)]
    operations = spec["paths"][matches[0]]
    operation = operations[entry["method"].lower()]
    return operation["responses"]["200"]["content"]["application/json"]["schema"]


_SCHEMA_INVARIANTS = [
    (path, field)
    for path, fields in _MANIFEST["payload_invariants"]["schema"].items()
    if not path.startswith("_")
    for field in fields
]


def test_the_field_walker_rejects_a_field_that_is_not_there():
    """The walker must answer False for an absent field.

    Every extractor here asserts its own output is non-trivial first: a walk that
    returned True unconditionally — one `.get()` too lenient — would pass every
    invariant below while checking nothing at all.
    """
    from backend.main import app

    spec = app.openapi()
    schema = _response_schema("/api/volume/state", spec)

    assert _walk("data.global_volume_db", schema, spec) is True
    assert _walk("data.no_such_field", schema, spec) is False
    assert _walk("data.clients[mac].volume_db", schema, spec) is True
    assert _walk("data.clients[mac].no_such_field", schema, spec) is False


@pytest.mark.parametrize(
    ("path", "field"), _SCHEMA_INVARIANTS, ids=[f"{p} {f}" for p, f in _SCHEMA_INVARIANTS]
)
def test_schema_payload_invariant(path, field):
    """Each field Milo-iOS decodes by name must exist on the typed response."""
    from backend.main import app

    spec = app.openapi()

    assert _walk(field, _response_schema(path, spec), spec), (
        f"`{field}` is gone from the response of {path}, but Milo-iOS decodes it "
        f"by name. A Codable struct missing a non-optional key throws — the whole "
        f"call fails, it does not degrade."
    )


# --------------------------------------------------------------------------- #
# Manifest hygiene.
# --------------------------------------------------------------------------- #

def test_manifest_is_self_consistent():
    """No duplicate route, and no invariant pointing at a route nobody declares.

    `POST /api/audio/control/music_library` and `POST /api/audio/control/{source}`
    are one route under two spellings — listed twice, the second entry would pin
    a shape the snapshot can never produce.
    """
    keys = [(e["method"].upper(), _FRESHNESS._shape(e["path"])) for e in _MANIFEST["rest"]]
    duplicates = {k for k in keys if keys.count(k) > 1}
    assert not duplicates, f"the same route is declared twice: {sorted(duplicates)}"

    declared = {e["path"] for e in _MANIFEST["rest"]}
    orphans = [
        path
        for path in _MANIFEST["payload_invariants"]["schema"]
        if not path.startswith("_") and path not in declared
    ]
    assert not orphans, f"payload invariants for undeclared routes: {orphans}"
