"""Offline contract test: Milo-iOS (external iOS client) ⇄ Milo backend.

Milo-iOS (github.com/leodurandfr/Milo-iOS) is a THIRD consumer of the REST API
beyond `frontend/` and Milo-Mac. Its consumers are short-lived processes — a
WidgetKit timeline entry, App Intents — so it holds no WebSocket and cannot be
told about anything: every dependency it has is a route it calls and a field it
decodes. Drop either and it breaks silently at runtime.

`cf430ae6` removed `GET /api/settings/dock-apps` as "Milo-Mac-only" after the Mac
converged on `/bulk`. Milo-iOS' client layer targeted it too, and nothing in this
checkout recorded that. The break was latent — no caller reached `getDockApps` in
the published build, so nobody was broken — and that is the point: nobody could
have known either way. A route with no caller in `frontend/src/` is not dead, it
is unwitnessed, and this file is what witnesses it. Milo-iOS deleted the call at
741f8dc1, which is how such a dependency is meant to end: seen, then removed on
purpose, rather than discovered by a unit in the field.

Five guards, all offline:

  * every route the manifest declares still resolves to a FastAPI route;
  * the manifest and the VENDORED snapshot describe the same surface, exactly —
    neither may depend on something the other does not know about;
  * a tolerance section does not reappear without the test that proves its
    entries are still defects — `_broken_calls` held one, and self-deleted at
    741f8dc1 when Milo-iOS dropped the call;
  * every field the app reads by name still exists on the typed response model;
  * every field pinned as an invariant is a key the app actually mentions — the
    same check from the client side, which is where `dock_apps.enabled_apps`
    went unwitnessed on /api/settings/bulk until 2026-09-20.

`check_milo_ios_freshness.py` is the network half. It is run BY HAND against a
checkout — there is NO CI job for it, unlike the Milo-Mac twin — so nothing but
a person deciding the app has moved will ever report a stale snapshot.
"""
import importlib.util
import json
import re
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

def test_the_extractor_reads_every_call_shape():
    """The surface reader must find routes through ALL THREE shapes the app uses.

    `get(path: "…")` is the helper, where the method is the helper's own name;
    `fireAdjustVolume` hand-builds its URL with `URL(string: baseURL() + "…")`
    and sets `httpMethod` separately; `writeClientVolume` and `writeGlobalVolume`
    go through `writeVolume(path:body:label:)`, a helper of the app's own that
    builds the URL inside its body and is named after nothing in particular.

    An extractor modelling fewer shapes reports a smaller surface with no error,
    and a route missing from it reads as "the app does not use it" — the silence
    this whole contract exists to break. The third shape is not hypothetical:
    at Milo-iOS be15c1f the two-shape extractor lost BOTH volume writes, one of
    which the manifest already pinned, so the freshness script would have
    reported the app as having dropped a route it calls on every gesture.

    Since 741f8dc1 no PLAIN helper call carries an interpolated path — changeSource
    was the last — so the collapsing is witnessed on the hand-built shape and on
    the wrapped helper instead. Both still exercise it; the notation is a property
    of `_shape`, not of any one call site.

    Asserted before anything trusts the extraction: a guard that cannot see is a
    guard that passes.
    """
    surface = _FRESHNESS.extract_rest(_VENDORED_SWIFT)

    assert ("GET", "/api/volume/state") in surface, "helper call shape not read"
    assert ("POST", "/api/volume/adjust") in surface, "hand-built URL shape not read"
    assert ("POST", "/api/audio/control/{}") in surface, (
        "interpolated path not collapsed on a hand-built URL"
    )
    assert ("PATCH", "/api/volume/global") in surface, "wrapped helper shape not read"
    assert ("PATCH", "/api/volume/client/mac/{}") in surface, (
        "wrapped helper shape not read on an interpolated path"
    )


def test_a_wrapped_helper_takes_the_method_its_own_body_assigns():
    """Where the third shape's method comes from, stated rather than assumed.

    Not a table of known helper names — that is the bet `SOURCE_FILES` already
    had to stop making. The rule is URLRequest's: a helper's method is the
    `httpMethod` its body assigns, and GET when it assigns none. It therefore
    holds for a helper nobody has written yet, and a helper this snapshot cannot
    see raises instead of silently defaulting to GET.
    """
    methods = _FRESHNESS.helper_methods(_VENDORED_SWIFT)

    assert methods["writeVolume"] == "PATCH", "the wrapping helper's verb is not read"
    assert methods["post"] == "POST"
    assert methods["get"] == "GET", "a helper that assigns no method is not a GET"

    with pytest.raises(ValueError, match="does not declare"):
        _FRESHNESS.extract_rest('unknownHelper(path: "/api/volume/state")')


def test_the_extractor_reads_every_client_file_not_just_the_first():
    """The surface is spread across several files, and all of them are read.

    This is the defect of 2026-09-19, named so it cannot come back quietly.
    `SOURCE_FILES` was a frozen two-name list while Milo-iOS 09b9789b put its
    push routes in a third file, `MiloAPIClient+Push.swift`. The extractor read
    an unchanged surface, and `check_milo_ios_freshness.py` printed "vendored
    snapshot matches upstream" while the app had gained two routes — the
    contract passing by describing an app that no longer existed.

    `test_manifest_matches_the_vendored_surface_exactly` does catch a
    regression here today, but only as a side effect of the manifest happening
    to list those two routes. This asserts the property itself: more than one
    client file vendored, and a route that exists in none but the second one.
    """
    client_files = [
        p.name for p in _FRESHNESS.matching_files(VENDOR_DIR)
        if p.name.startswith("MiloAPIClient")
    ]
    assert len(client_files) > 1, (
        f"only {client_files} vendored — if Milo-iOS really collapsed back to one "
        f"client file, this test is the place to say so"
    )

    surface = _FRESHNESS.extract_rest(_VENDORED_SWIFT)
    assert ("POST", "/api/push/tokens") in surface, (
        "a route declared outside MiloAPIClient.swift is missing from the "
        "extracted surface — SOURCE_FILES is reading too few files again"
    )


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


def test_a_tolerance_section_brings_its_test_back():
    """`_broken_calls` self-deleted at 741f8dc1. If it returns, its test must too.

    The section held one route — GET /api/settings/dock-apps, which the app
    targeted and the backend answered 405 — and `test_broken_calls_are_still_broken`
    is what kept it from becoming a permanent exemption: it failed the moment
    either side moved. Milo-iOS deleted the call, so both the entry and its test
    are gone.

    This is what stands in their place. A tolerance section reappearing WITHOUT
    the test that proves its entries are still broken is the failure mode the
    manifest's `_no_tolerance_sections` describes: a list that cannot fail on its
    own is a comment, not a contract. Restoring the section is legitimate; doing
    it silently is not.
    """
    assert "_broken_calls" not in _MANIFEST, (
        "`_broken_calls` is back in the manifest. Restore "
        "test_broken_calls_are_still_broken alongside it (deleted with the "
        "section; it asserted the backend still does not serve the route AND "
        "the vendored app still calls it), then delete this guard's assertion. "
        "A listed defect nothing re-checks stops being a defect and becomes an "
        "excuse."
    )


def test_every_declared_route_names_its_consumer():
    """Each route must say WHERE in the app it is called from.

    The `consumer` is what makes a contract failure actionable — it points at the
    Swift function to fix rather than at a path. Required for every entry, with
    no exception: manifest == snapshot means each one is backed by a call the
    extractor found, so there is always a name to give.
    """
    missing = [
        f"{e['method']} {e['path']}"
        for e in _MANIFEST["rest"]
        if not e.get("consumer")
    ]
    assert not missing, f"declared with no consumer named: {missing}"


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


_BRACKETED = re.compile(r"\[[^\]]*\]")
_SEGMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _wire_keys(field: str) -> list[str]:
    """The literal wire keys in a dotted invariant — `a.b[k].c` -> [a, b, c].

    The bracket content is this manifest's notation for a dict KEY, not a key the
    app decodes: `clients[mac]` is additionalProperties and `mac` is a stand-in
    that appears in no Swift file. Dropping it is the difference between a guard
    and a guard that is permanently red.
    """
    return _SEGMENT.findall(_BRACKETED.sub("", field))


_WIRE_KEYS = sorted({
    (path, field, key)
    for path, field in _SCHEMA_INVARIANTS
    for key in _wire_keys(field)
})


def test_the_wire_key_reader_reports_a_key_the_app_never_mentions():
    """The guard below must be able to go red, and be red on the known case.

    Same rule as the field walker above: an extractor is trusted only after its
    own output is shown non-trivial. `dock_apps` is the measured case — it was a
    payload_invariant on /api/settings/bulk while occurring ZERO times in the
    app, so it is the one key that proves this reads anything at all.

    The second assertion is the standing half: if Milo-iOS ever does start
    reading dock apps out of /bulk, this fails and the invariant is owed back.
    """
    assert _wire_keys("dock_apps.enabled_apps") == ["dock_apps", "enabled_apps"]
    assert _wire_keys("data.clients[mac].volume") == ["data", "clients", "volume"]

    assert not re.search(r"\bdock_apps\b", _VENDORED_SWIFT)
    assert re.search(r"\bvolume_limits\b", _VENDORED_SWIFT)


@pytest.mark.parametrize(
    ("path", "field", "key"), _WIRE_KEYS,
    ids=[f"{p} {f} -> {k}" for p, f, k in _WIRE_KEYS],
)
def test_an_invariant_names_a_key_the_app_mentions(path, field, key):
    """Every wire key pinned here must occur in the vendored Swift.

    The other direction of test_schema_payload_invariant, which resolves these
    fields against the BACKEND only. That asymmetry let `dock_apps.enabled_apps`
    sit on /api/settings/bulk from the day it was written: the backend does serve
    it, so the check passed, and nothing ever asked the client. syncVolumeSettings
    reads three fields and has never read that one.

    PRESENCE, not attribution — see payload_invariants._about. A key the app
    mentions for a DIFFERENT route still passes here, and confirming otherwise is
    the hand check owed at each snapshot refresh.
    """
    assert re.search(rf"\b{re.escape(key)}\b", _VENDORED_SWIFT), (
        f"`{path}` pins `{field}`, but `{key}` occurs nowhere in the vendored "
        f"Milo-iOS snapshot — so the app does not decode it and the invariant is "
        f"describing the backend rather than the client. Drop it, or refresh the "
        f"snapshot if the app has gained the field."
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
