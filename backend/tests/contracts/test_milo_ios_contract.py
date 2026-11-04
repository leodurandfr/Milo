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

Six guards, all offline:

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
  * every request body an entry names (`body`, `body_values`) is taken by the
    route's request model and is exactly the dict the app builds — a 422 on
    POST /api/push/tokens files the token as refused for good.

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


def _load_sibling(filename: str, name: str):
    """Import a sibling helper module: the freshness script's Swift extractors,
    or the request-body checks shared with the Milo-Mac test.

    By file path rather than `import`, so it resolves identically whether or not
    pytest treats backend/tests/contracts as a package — the same reason the
    Milo-Mac test does it.
    """
    spec = importlib.util.spec_from_file_location(name, CONTRACTS_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_FRESHNESS = _load_sibling("check_milo_ios_freshness.py", "milo_ios_freshness")
_BODIES = _load_sibling("request_bodies.py", "milo_request_bodies")
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

# A helper of the app's own, in the shape `writeVolume(path:body:label:)` had
# until Milo-iOS 9a9ed25: it builds its URL from `baseURL() + path` and assigns
# `httpMethod` in its own body. The app no longer has one, so the vendored
# snapshot cannot witness this shape any more; the extractor still reads it,
# and the next such helper must not find it gone.
_WRAPPED_FIXTURE = """
    private static func writeVolume(path: String, body: [String: Any], label: String) async {
        guard let url = URL(string: baseURL() + path) else { return }
        var request = URLRequest(url: url)
        request.httpMethod = "PATCH"
    }
    static func writeRoom(_ mac: String) async {
        await writeVolume(path: "/api/volume/client/mac/\\(mac)", body: [:], label: "room")
    }
"""


def test_the_extractor_reads_every_call_shape():
    """The surface reader must find routes through ALL THREE shapes it models.

    `get(path: "…")` is the helper, where the method is the helper's own name
    (`adjustVolume` reaches `post(path: "/api/volume/adjust")` this way since
    Milo-iOS 2ceb0df); `setGlobalVolume` and `sendControl` hand-build their URL
    with `URL(string: baseURL() + "…")` and set `httpMethod` separately; and a
    helper of the app's own may take `path:` and carry the method in its body.

    An extractor modelling fewer shapes reports a smaller surface with no error,
    and a route missing from it reads as "the app does not use it" — the silence
    this whole contract exists to break. The third shape is not hypothetical:
    at Milo-iOS be15c1f the two-shape extractor lost BOTH volume writes, one of
    which the manifest already pinned. The app dropped that helper at 9a9ed25,
    so the third shape is witnessed on `_WRAPPED_FIXTURE` rather than on the
    snapshot; the first two still are on the snapshot, interpolation included.

    Asserted before anything trusts the extraction: a guard that cannot see is a
    guard that passes.
    """
    surface = _FRESHNESS.extract_rest(_VENDORED_SWIFT)

    assert ("GET", "/api/volume/state") in surface, "helper call shape not read"
    assert ("POST", "/api/volume/adjust") in surface, "helper call shape not read"
    assert ("PATCH", "/api/volume/global") in surface, "hand-built URL shape not read"
    assert ("POST", "/api/audio/control/{}") in surface, (
        "interpolated path not collapsed on a hand-built URL"
    )

    wrapped = _FRESHNESS.extract_rest(_WRAPPED_FIXTURE)
    assert ("PATCH", "/api/volume/client/mac/{}") in wrapped, (
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
    assert _FRESHNESS.helper_methods(_WRAPPED_FIXTURE)["writeVolume"] == "PATCH", (
        "the wrapping helper's verb is not read"
    )

    methods = _FRESHNESS.helper_methods(_VENDORED_SWIFT)
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


def _upstream_copy(tmp_path: Path) -> Path:
    """A Milo-iOS checkout holding the vendored snapshot, laid out as the real
    app lays it out (the bridge in the app target, the rest in Shared/): the
    stand-in for an upstream that has not moved."""
    for path in _FRESHNESS.matching_files(VENDOR_DIR):
        target = "Milo-iOS" if path.name == "MiloNowPlayingBridge.swift" else "Shared"
        (tmp_path / target).mkdir(exist_ok=True)
        (tmp_path / target / path.name).write_text(path.read_text())
    return tmp_path


def test_freshness_reports_an_unmoved_upstream_in_sync(tmp_path, capsys):
    """The control for the test below: an upstream identical to the snapshot
    must read as matching, or a check that flags everything would pass it."""
    _upstream_copy(tmp_path)

    assert _FRESHNESS.main(["x", str(tmp_path)]) == 0
    assert "matches upstream" in capsys.readouterr().out


def _rename_a_pinned_body_key(root: Path) -> str:
    entry = next(e for e in _MANIFEST["rest"] if (e["method"], e["path"]) == ("POST", "/api/push/tokens"))
    assert "device_id" in entry["body"], "the manifest no longer pins device_id on the token body"
    push = root / "Shared" / "MiloAPIClient+Push.swift"
    assert '"device_id"' in push.read_text(), "the vendored push client no longer sends device_id"
    push.write_text(push.read_text().replace('"device_id"', '"phone_id"'))
    return push.name


def _add_an_upstream_client_file(root: Path) -> str:
    name = "MiloAPIClient+Library.swift"
    (root / "Shared" / name).write_text("extension MiloAPIClient {}\n")
    return name


@pytest.mark.parametrize("drift", [_rename_a_pinned_body_key, _add_an_upstream_client_file])
def test_freshness_flags_a_content_drift_that_moves_no_route(tmp_path, capsys, drift):
    """An upstream that moved without moving a route must not read as matching.

    The script judged upstream on the extracted REST surface alone, while the
    manifest pins what the app SENDS (`body`, `body_values`, `data_keys`) and
    the keys it decodes — all read from the vendored files, none a route. A
    renamed body key, or a new client file carrying no route yet, answered
    "vendored snapshot matches upstream" with exit 0, and the offline test went
    on comparing the manifest to a copy describing an app that no longer
    existed. The Milo-Mac twin compares every vendored file by content for the
    same reason.
    """
    moved = drift(_upstream_copy(tmp_path))

    assert _FRESHNESS.main(["x", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "matches upstream" not in out
    assert moved in out


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

def _walk(dotted: str, node: dict, spec: dict) -> bool:
    """Resolve `a.b[k].c` / `a[].c` through an OpenAPI schema.

    `a.b` is a property; `a[]` steps into `items`, the element of a `List[Model]`;
    `a[k]` steps into `additionalProperties`, the shape a `Dict[str, Model]`
    takes. Returns False the moment a step is unresolvable — which is the whole
    point: a renamed field must fail, not be assumed present.
    """
    for step in dotted.split("."):
        keyed = step.endswith("]")
        name = step.split("[", 1)[0] if keyed else step

        node = _BODIES.resolve(node, spec)
        properties = node.get("properties", {})
        if name not in properties:
            return False
        node = properties[name]

        if keyed:
            node = _BODIES.resolve(node, spec)
            inner = node.get("items" if step.endswith("[]") else "additionalProperties")
            if not isinstance(inner, dict):
                return False
            node = inner
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

    stations = _response_schema("/api/radio/stations", spec)
    assert _walk("stations[].favicon", stations, spec) is True
    assert _walk("stations[].no_such_field", stations, spec) is False
    assert _walk("total[].favicon", stations, spec) is False


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
# 5. The bodies the app sends are taken, and are the ones it builds.
# --------------------------------------------------------------------------- #

_REST_IDS = [f"{e['method']} {e['path']}" for e in _MANIFEST["rest"]]

# A consumer that sends a body some other function builds. `fire` posts what
# `ControlCommand.body(forSource:)` returns, and nothing in `fire` itself.
_BODY_BUILT_BY = {"fire": "body"}


@pytest.mark.parametrize("entry", _MANIFEST["rest"], ids=_REST_IDS)
def test_request_body_keys_are_accepted(entry):
    """The body Milo-iOS sends is one the route's request model takes.

    The push routes are where this bites hardest: a 422 on POST /api/push/tokens
    files the token as refused and the app never sends it again, so the
    widget's push stays silent after the backend is fixed.
    """
    from backend.main import app

    spec = app.openapi()
    problems = _BODIES.acceptance_problems(entry, _BODIES.operation(spec, entry, _segments_match), spec)
    assert not problems, (
        f"{entry['method']} {entry['path']} ({entry['consumer']}): {problems}. Restore the "
        f"field, or — if Milo-iOS changed its body — refresh the snapshot and `body` together."
    )


@pytest.mark.parametrize("entry", _MANIFEST["rest"], ids=_REST_IDS)
def test_request_body_keys_occur_in_the_snapshot(entry):
    """`body` is exactly the top-level keys of the dicts the consumer builds in
    the vendored Swift — none for a route it sends no body to — and every
    value pinned for an enum field is one the app spells."""
    problems = _BODIES.snapshot_problems(entry, _VENDORED_SWIFT, _BODY_BUILT_BY)
    assert not problems, (
        f"{entry['method']} {entry['path']}: {problems} — refresh `body` from the vendored snapshot"
    )


_COMMAND_ENTRIES = [e for e in _MANIFEST["rest"] if "data_keys" in e]


@pytest.mark.parametrize("entry", _COMMAND_ENTRIES, ids=[e["consumer"] for e in _COMMAND_ENTRIES])
def test_command_data_keys_are_taken_by_every_source(entry):
    """The lock screen's `seek` and `skip` reach whichever source the card
    names, so their `data` must be what every source declaring the command
    takes. A params model refuses a key it does not declare: a field renamed
    in one source alone answers 400 to the lock screen's −15/+30 there."""
    problems = _BODIES.command_data_problems(entry, _BODIES.source_commands())
    assert not problems, (
        f"{entry['path']} ({entry['consumer']}): {problems}. Restore the field, or — if "
        f"Milo-iOS changed what it sends — refresh the snapshot and `data_keys` together."
    )


def test_body_values_are_every_value_the_app_sends():
    """`body_values` lists every value the app can put in each pinned field,
    not a subset that happens to be spelled somewhere: `kind` is PushTokenKind's
    raw values, `environment` what `apsEnvironment` returns. A kind the app adds
    and the backend's enum lacks is a 422, and the token is never sent again."""
    pinned = {
        (e["method"], e["path"], key): sorted(values)
        for e in _MANIFEST["rest"]
        for key, values in e.get("body_values", {}).items()
    }
    enum = re.search(r"enum PushTokenKind: String \{(.*?)\}", _VENDORED_SWIFT, re.S)
    environment = re.search(r"static let apsEnvironment: String\? = \{(.*?)\}\(\)", _VENDORED_SWIFT, re.S)
    assert enum and environment, "PushTokenKind or apsEnvironment not found in the vendored Swift"
    kinds = [raw or name for name, raw in re.findall(r'\bcase (\w+)(?:\s*=\s*"(\w+)")?', enum.group(1))]
    environments = re.findall(r'return "(\w+)"', environment.group(1))
    assert len(kinds) >= 3 and environments, "value extraction drift"

    assert pinned == {
        ("POST", "/api/push/tokens", "kind"): sorted(kinds),
        ("POST", "/api/push/tokens", "environment"): sorted(environments),
    }, "body_values != the values the vendored app sends — a new pinned field needs its reader here"


def test_the_body_reader_reads_every_shape_the_app_writes():
    """A named `bodyDict`, `withJSONObject: [...]` inline, a `body` built then
    extended by `body["session_id"] = …`, and a builder function returning one
    of several dicts — the reader sees each, and `_BODY_BUILT_BY` still names a
    function its consumer calls. Asserted before anything trusts it."""
    def read(name):
        return _BODIES.sent_keys(_BODIES.swift_function(_VENDORED_SWIFT, name))

    assert read("adjustVolume") == {"delta_db", "show_bar"}
    assert read("setGlobalVolume") == {"volume", "show_bar"}
    assert "session_id" in read("registerPushToken")
    assert read("body") == {"command", "data"}
    assert _BODIES.sent_data(_BODIES.swift_function(_VENDORED_SWIFT, "body")) == [
        (None, {"position_ms"}), (None, {"seconds"})
    ]
    assert len(_COMMAND_ENTRIES) == 1, "the control-route entry lost `data_keys`"
    for consumer, builder in _BODY_BUILT_BY.items():
        assert f".{builder}(" in _BODIES.swift_function(_VENDORED_SWIFT, consumer), (
            f"{consumer} no longer calls {builder}: drop the mapping"
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

    malformed = {e["path"]: _BODIES.format_problems(e) for e in _MANIFEST["rest"] if _BODIES.format_problems(e)}
    assert not malformed, f"malformed body fields: {malformed}"

    declared = {e["path"] for e in _MANIFEST["rest"]}
    orphans = [
        path
        for path in _MANIFEST["payload_invariants"]["schema"]
        if not path.startswith("_") and path not in declared
    ]
    assert not orphans, f"payload invariants for undeclared routes: {orphans}"


# --------------------------------------------------------------------------- #
# The Lock Screen card has two authors.
# --------------------------------------------------------------------------- #

def test_the_two_apps_decode_the_state_with_one_file():
    """Milo-iOS and Milo-Mac share `MiloAudioState.swift` byte for byte, which
    is what lets the Mac manifest's `details_keys` pin what the iOS card reads
    from `details` (an untitled CD track's `current_track`): this manifest pins
    no `details` key of its own. Two copies that drift leave the iOS side
    pinned by nothing."""
    mac_copy = CONTRACTS_DIR / "vendor" / "milo-mac" / "MiloAudioState.swift"
    ios_copy = VENDOR_DIR / "MiloAudioState.swift"
    assert "struct MiloAudioState" in ios_copy.read_text(), "vendored decoder unreadable"
    assert ios_copy.read_bytes() == mac_copy.read_bytes(), (
        "the vendored decoders differ: refresh both snapshots from the same "
        "MiloAudioState.swift, or pin the iOS app's details keys here"
    )


def test_an_untitled_cd_track_is_spelled_as_the_app_spells_it():
    """The push (`payloads.shown_track`) and the app's own card
    (`MiloSourceCard.untitledTrack`) name an untitled CD track for the same
    session: two spellings make the Lock Screen flip between them at every
    round trip."""
    from backend.core.push.payloads import UNTITLED_CD_TRACK

    bridge = (VENDOR_DIR / "MiloNowPlayingBridge.swift").read_text()
    found = re.search(
        r'static func untitledTrack\(_ (\w+): Int\) -> String \{ "([^"]*)" \}', bridge,
    )
    assert found, "MiloSourceCard.untitledTrack not found in the vendored bridge"
    swift = found.group(2).replace(f"\\({found.group(1)})", "{}")
    assert "{}" in swift, f"the app's label does not interpolate the number: {swift!r}"
    assert swift == UNTITLED_CD_TRACK


def test_the_app_writes_the_artist_line_as_the_push_does():
    """`payloads.build_attributes` writes the shown track's artist whatever the
    phase, so the app's `buildAttributes` must too: a CONNECTED session that
    names its track (an iPhone in Spotify Connect over Bluetooth) read
    "Connected to …" from the app and the artist from the push, and the Lock
    Screen flipped between them at every round trip."""
    bridge = (VENDOR_DIR / "MiloNowPlayingBridge.swift").read_text()
    found = re.search(
        r"private static func buildAttributes\(from.*?\n(.*?)\n        return MiloSessionAttributes\(",
        bridge, re.S,
    )
    assert found, "buildAttributes not found in the vendored bridge"
    body = found.group(1)
    titled = re.search(
        r"MiloSourceCard\.shown\(for: state\)(.*?)MiloSourceCard\.card\(for: state\)", body, re.S,
    )
    assert titled, "the titled branch was not isolated"
    artist = re.search(r"Track\(.*?\bartist: ([^,\n]+)", titled.group(1), re.S)
    assert artist, "no artist argument in the titled branch's Track("
    assert artist.group(1).strip() == "shown.artist"
    assert "connectedTo" not in body and ".connected" not in body
