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
  * every request body an entry names (`body`) is taken by the route's request
    model and is exactly the dict its consumer builds in the vendored Swift
    (request_bodies.py, shared with the Milo-iOS test);
  * every WS (category, type) has an emission site in `backend/`: a typed
    `WsEvent` subclass from `core/models/ws_events.py` referenced outside its
    defining module (`broadcast(event)` is the sole emission API);
  * every payload invariant the manifest documents (`ws.payload_invariants`)
    holds on the typed event models — the exact fields Milo-Mac reads exist,
    and (for source/state) the vendored decoder MiloAudioState.swift reads them.

A separate, non-blocking CI job (`check_milo_mac_freshness.py`) re-clones
Milo-Mac and verifies the *manifest itself* still matches what Milo-Mac
consumes — that is the network half of the guard.

A static check that turns a silent contract break into an actionable failing test.
"""
import ast
import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
CONTRACTS_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = CONTRACTS_DIR / "milo_mac_contract.json"
VENDOR_DIR = CONTRACTS_DIR / "vendor" / "milo-mac"

_MANIFEST = json.loads(MANIFEST_PATH.read_text())


def _load_sibling(filename: str, name: str):
    """Import a sibling helper module: the freshness script's Swift-surface
    extractors, or the request-body checks shared with the Milo-iOS test.

    Loaded by file path rather than `import` so it resolves identically whether
    or not pytest treats backend/tests/contracts as a package.
    """
    spec = importlib.util.spec_from_file_location(name, CONTRACTS_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_FRESHNESS = _load_sibling("check_milo_mac_freshness.py", "milo_mac_freshness")
_BODIES = _load_sibling("request_bodies.py", "milo_request_bodies")


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
        f"{MANIFEST_PATH.name}. See CLAUDE.md §'External API clients — Milo-Mac and Milo-iOS'."
    )


# --------------------------------------------------------------------------- #
# REST response keys: what Milo-Mac reads off an answer, resolved against the
# answer the backend really produces.
# --------------------------------------------------------------------------- #

def _resolve_on_payload(payload, dotted: str) -> None:
    """Raise KeyError unless `dotted` names a key in a real payload.

    `a.b[k].c` reads `c` in EVERY entry of the map `b`, and an empty map is
    refused: a producer that built no entry would otherwise let any key under
    it resolve.
    """
    current = [payload]
    for part in dotted.split("."):
        step = re.fullmatch(r"(\w+)(\[\w+\])?", part)
        assert step, f"unparsable step `{part}` in `{dotted}`"
        key, each = step.groups()
        found = []
        for node in current:
            if not isinstance(node, dict) or key not in node:
                raise KeyError(f"{dotted}: no `{key}`")
            value = node[key]
            if each:
                if not isinstance(value, dict) or not value:
                    raise KeyError(f"{dotted}: `{key}` is not a non-empty map")
                found.extend(value.values())
            else:
                found.append(value)
        current = found


def _swift_struct(swift: str, name: str) -> str:
    """The body of a top-level `struct <name>` in a vendored Swift file."""
    match = re.search(rf"^struct {name}\b.*?^\}}$", swift, re.S | re.M)
    assert match, f"struct {name} not found in the vendored snapshot — extractor drift?"
    return match.group(0)


async def _multiroom_state_answer() -> dict:
    """GET /api/multiroom/state through the real router, response model
    included, over a real registry holding a local client, a satellite and a
    zone — `zone_to_enriched_dict` is a producer too, so no registry mock."""
    import httpx
    from fastapi import FastAPI

    from backend.api.multiroom import create_multiroom_router
    from backend.core.multiroom.client_registry import ClientRegistryService

    registry = ClientRegistryService(settings_service=None)
    await registry.register_client("aa:bb:cc:dd:ee:ff", "Milō", "127.0.0.1")
    await registry.register_client("dc:a6:32:7e:d3:43", "Canapé", "192.168.1.153")
    await registry.create_zone("z1", "Salon", ["dc:a6:32:7e:d3:43", "aa:bb:cc:dd:ee:ff"])
    app = FastAPI()
    app.include_router(create_multiroom_router(registry))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://milo") as client:
        response = await client.get("/api/multiroom/state")
    assert response.status_code == 200, response.text
    return response.json()


async def _bulk_settings_answer() -> dict:
    """GET /api/settings/bulk through the real router, over the document the
    production validator guarantees for a unit with no settings file yet."""
    from unittest.mock import AsyncMock, Mock

    import httpx
    from fastapi import FastAPI

    from backend.api.settings import create_settings_router
    from backend.core.settings import SettingsService

    document = SettingsService()._validate_and_merge({})
    settings = Mock(get_all_settings=AsyncMock(return_value=document))
    app = FastAPI()
    app.include_router(
        create_settings_router(
            volume_service=Mock(), state_machine=Mock(get_source=Mock(return_value=None)),
            screen_controller=Mock(),
            systemd_manager=Mock(), routing_service=Mock(), hardware_service=Mock(),
            settings_service=settings, multiroom_equalizer_service=Mock(),
            mac_link_calibration=Mock(),
        ),
        prefix="/api/settings",
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://milo") as client:
        response = await client.get("/api/settings/bulk")
    assert response.status_code == 200, response.text
    return response.json()


# (method, path) -> the backend's real answer on that route. An entry carrying
# `response_keys` needs one here (test_manifest_is_self_consistent).
_RESPONSE_PRODUCERS = {
    ("GET", "/api/multiroom/state"): _multiroom_state_answer,
    ("GET", "/api/settings/bulk"): _bulk_settings_answer,
}

_ENTRIES_WITH_RESPONSE_KEYS = [e for e in _MANIFEST["rest"] if "response_keys" in e]


def _rest_entry(method: str, path: str) -> dict:
    (entry,) = [e for e in _MANIFEST["rest"] if (e["method"], e["path"]) == (method, path)]
    return entry


@pytest.mark.parametrize(
    "entry",
    _ENTRIES_WITH_RESPONSE_KEYS,
    ids=[f"{e['method']} {e['path']}" for e in _ENTRIES_WITH_RESPONSE_KEYS],
)
async def test_response_keys_resolve_against_the_producer(entry):
    """Each key Milo-Mac reads off a REST answer is in the answer the route
    really serves.

    The route's response model cannot stand in for it: GET /api/multiroom/state
    declares its clients and zones as opaque dicts, so the keys come from
    Client.to_dict(), the route's `online` and zone_to_enriched_dict() alone,
    and a rename there left every contract test green while MultiroomClient
    dropped every client from the app's submenu.
    """
    answer = await _RESPONSE_PRODUCERS[(entry["method"], entry["path"])]()
    missing = []
    for dotted in entry["response_keys"]:
        try:
            _resolve_on_payload(answer, dotted)
        except KeyError as e:
            missing.append(str(e))
    assert not missing, (
        f"{entry['method']} {entry['path']} no longer serves key(s) Milo-Mac reads "
        f"({entry['consumer']}): {missing}"
    )


def test_multiroom_state_response_keys_are_what_the_decoders_read():
    """`response_keys` + `decoded_not_served` are exactly the `json[…]` reads of
    MultiroomClient and MultiroomZone, so the list cannot fall behind the app
    or name a key it no longer reads."""
    entry = _rest_entry("GET", "/api/multiroom/state")
    api_swift = (VENDOR_DIR / "MiloAPIService.swift").read_text()
    client_keys = re.findall(r'json\["(\w+)"\]', _swift_struct(api_swift, "MultiroomClient"))
    zone_keys = re.findall(r'json\["(\w+)"\]', _swift_struct(api_swift, "MultiroomZone"))
    assert "mac_id" in client_keys and "client_ids" in zone_keys, "decoder extraction drift"

    decoded = {f"clients[mac].{k}" for k in client_keys} | {f"zones[id].{k}" for k in zone_keys}
    served, not_served = set(entry["response_keys"]), set(entry["decoded_not_served"])
    assert not served & not_served, f"listed as both served and not: {served & not_served}"
    assert served | not_served == decoded, (
        f"manifest {sorted(served | not_served)} != decoders' reads {sorted(decoded)} — "
        "refresh the manifest from the vendored MiloAPIService.swift"
    )


async def test_multiroom_state_keys_it_does_not_serve_are_still_absent():
    """`decoded_not_served` is a fact about the route, so it is checked like
    one: the day the route serves one of them, even for a single client, the
    key belongs in `response_keys` and this fails until it is moved there."""
    answer = await _multiroom_state_answer()
    clients = list(answer["clients"].values())
    assert len(clients) >= 2, "no entries to look in — the absence would be vacuous"
    served = []
    for dotted in _rest_entry("GET", "/api/multiroom/state")["decoded_not_served"]:
        _, _, leaf = dotted.partition("clients[mac].")
        assert leaf and "." not in leaf, f"`{dotted}`: teach this test to look under it"
        if any(leaf in client for client in clients):
            served.append(dotted)
    assert not served, f"the route now serves {served} — move them to response_keys"


def test_bulk_settings_response_keys_are_what_fetch_bulk_settings_reads():
    """The keys `fetchBulkSettings` subscripts are exactly the segments of the
    entry's `response_keys`, so the list cannot fall behind the app. Its
    `mac_roc` sub-keys are read by RocVADSettings(miloLink:), which is not
    vendored, so `mac_roc` itself is the deepest key pinned there."""
    function = _BODIES.swift_function((VENDOR_DIR / "MiloAPIService.swift").read_text(), "fetchBulkSettings")
    read = set(re.findall(r'\["(\w+)"\]', function))
    assert {"volume_limits", "min_db"} <= read, "fetchBulkSettings extraction drift"
    pinned = {
        segment
        for dotted in _rest_entry("GET", "/api/settings/bulk")["response_keys"]
        for segment in dotted.split(".")
    }
    assert pinned == read, (
        f"response_keys segments {sorted(pinned)} != the keys fetchBulkSettings reads "
        f"{sorted(read)} — refresh the manifest from the vendored MiloAPIService.swift"
    )


# --------------------------------------------------------------------------- #
# REST request bodies: what Milo-Mac sends, accepted by the route's request
# model and equal to the dict its consumer function builds.
# --------------------------------------------------------------------------- #

_REST_IDS = [f"{e['method']} {e['path']}" for e in _MANIFEST["rest"]]


@pytest.mark.parametrize("entry", _MANIFEST["rest"], ids=_REST_IDS)
def test_request_body_keys_are_accepted(entry):
    """The body Milo-Mac sends is one the route's request model takes.

    Resolved against the real OpenAPI schema: a renamed field answers 422 to
    every request the app makes, and a route that reads a raw `Request`
    publishes no request body for this to hold, which is why
    PUT /api/equalizer/target/{target}/enabled takes a model.
    """
    from backend.main import app

    spec = app.openapi()
    problems = _BODIES.acceptance_problems(entry, _BODIES.operation(spec, entry, _segments_match), spec)
    assert not problems, (
        f"{entry['method']} {entry['path']} ({entry['consumer']}): {problems}. Restore the "
        f"field, or — if Milo-Mac changed its body — refresh the snapshot and `body` together."
    )


@pytest.mark.parametrize("entry", _MANIFEST["rest"], ids=_REST_IDS)
def test_request_body_keys_occur_in_the_snapshot(entry):
    """`body` is exactly the top-level keys of the dict the consumer function
    builds in the vendored MiloAPIService.swift — none for a route it sends no
    body to — so the manifest can neither invent a key nor miss one."""
    problems = _BODIES.snapshot_problems(entry, (VENDOR_DIR / "MiloAPIService.swift").read_text())
    assert not problems, (
        f"{entry['method']} {entry['path']}: {problems} — refresh `body` from the vendored snapshot"
    )


_COMMAND_ENTRIES = [e for e in _MANIFEST["rest"] if "data_keys" in e]


@pytest.mark.parametrize("entry", _COMMAND_ENTRIES, ids=[e["consumer"] for e in _COMMAND_ENTRIES])
def test_command_data_keys_are_taken_by_the_source(entry):
    """The `data` Milo-Mac sends with a command is one the source's params
    model takes. It refuses a key it does not declare, so a field renamed in
    the backend alone answers 400 to the library's play_context, and the Mac
    browser plays nothing."""
    problems = _BODIES.command_data_problems(entry, _BODIES.source_commands())
    assert not problems, (
        f"{entry['path']} ({entry['consumer']}): {problems}. Restore the field, or — if "
        f"Milo-Mac changed what it sends — refresh the snapshot and `data_keys` together."
    )


def test_the_body_reader_reads_the_app_s_dict_literals():
    """The reader behind the test above sees a body, and only the top level of
    it: the music library's `data` carries `tracks`, a command parameter that is
    not a key of the request model — read apart, with the command it goes with.
    Asserted before anything trusts it."""
    source = (VENDOR_DIR / "MiloAPIService.swift").read_text()
    assert _BODIES.sent_keys(_BODIES.swift_function(source, "setZoneVolumeDelta")) == {"delta_db"}
    assert _BODIES.sent_keys(_BODIES.swift_function(source, "playMusicLibraryContext")) == {"command", "data"}
    assert _BODIES.sent_keys(_BODIES.swift_function(source, "fetchBulkSettings")) == set()
    assert _BODIES.sent_data(_BODIES.swift_function(source, "playMusicLibraryContext")) == [
        ("play_context", {"tracks", "start_index", "shuffle"})
    ]
    assert _BODIES.sent_data(_BODIES.swift_function(source, "sendPlaybackCommand")) == [(None, set())]
    assert len(_COMMAND_ENTRIES) == 2, "the two control-route entries lost `data_keys`"


# --------------------------------------------------------------------------- #
# WS: every manifest (category, type) must have an emission site — a typed
# WsEvent class actually used by the backend.
# --------------------------------------------------------------------------- #

def _concrete_event_classes():
    """All concrete WsEvent subclasses (those pinning TYPE), keyed by pair.

    A pair may map to several classes (unions discriminated by `data.source`,
    e.g. source/favorite_added radio|podcast), hence the list values.
    """
    from backend.core.models import ws_events

    def _walk(cls):
        for sub in cls.__subclasses__():
            if "TYPE" in vars(sub):
                yield sub
            yield from _walk(sub)

    table: dict[tuple[str, str], list] = {}
    for cls in _walk(ws_events.WsEvent):
        table.setdefault((cls.CATEGORY, cls.TYPE), []).append(cls)
    return table


_EVENT_CLASSES = _concrete_event_classes()


def _scan_typed_events():
    """Static model of typed `broadcast(event)` emission.

    Typed events pin (CATEGORY, TYPE) at the class level, so an emission site
    is any reference to the event class outside `core/models/ws_events.py`
    (instantiation `SourcePosition(...)` or class handoff
    `progress_event_cls=SatelliteUpdateProgress`). Bare imports don't count —
    an imported-but-unused class is dead code ruff flags anyway.
    """
    class_to_pair = {
        cls.__name__: pair
        for pair, classes in _EVENT_CLASSES.items()
        for cls in classes
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
        f"{MANIFEST_PATH.name}. See CLAUDE.md §'External API clients — Milo-Mac and Milo-iOS'."
    )


# --------------------------------------------------------------------------- #
# Payload invariants: every field Milo-Mac reads (per the manifest) must exist
# on the typed event models — statically, before any wire traffic.
# --------------------------------------------------------------------------- #

_INVARIANTS = _MANIFEST["ws"]["payload_invariants"]


def _sole_event_class(category: str, evt_type: str):
    """The single WsEvent class for a pair (invariant pairs are never unions)."""
    classes = _EVENT_CLASSES[(category, evt_type)]
    assert len(classes) == 1, (
        f"{category}/{evt_type} maps to {len(classes)} event classes — the "
        f"payload-invariant tests assume a single shape for this pair."
    )
    return classes[0]


def _models_of(annotation) -> list:
    """The pydantic models an annotation can hold: Optional, List and a
    discriminated union unwrapped."""
    import typing

    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        return _models_of(typing.get_args(annotation)[0])
    if origin in (typing.Union, list, getattr(__import__("types"), "UnionType", None)):
        return [m for arg in typing.get_args(annotation) for m in _models_of(arg)]
    return [annotation] if isinstance(annotation, type) and hasattr(annotation, "model_fields") else []


def _resolve_on_model(model, dotted: str) -> None:
    """Raise KeyError unless `dotted` names a field on `model`; a step into a
    union (`details`) must find the field on every member."""
    current = [model]
    for part in dotted.split("."):
        if not current:
            raise KeyError(f"{dotted}: `{part}` is under a non-model")
        for m in current:
            if part not in m.model_fields:
                raise KeyError(f"{dotted}: {m.__name__} has no `{part}`")
        current = [n for m in current for n in _models_of(m.model_fields[part].annotation)]


def _swift_enum_cases(swift: str, name: str) -> list:
    """The case names of `enum <name>: String` in the vendored decoder."""
    match = re.search(rf"enum {name}: String[^{{]*\{{(.*?)\n    \}}", swift, re.S)
    assert match, f"enum {name} not found in the vendored decoder — extractor drift?"
    cases = []
    for line in match.group(1).splitlines():
        line = line.split("//")[0].strip()
        if line.startswith("case "):
            cases += [c.strip() for c in line[len("case "):].split(",")]
    assert cases, f"enum {name} has no cases — extractor drift?"
    return cases


def test_invariant_source_state():
    """Every key MiloAudioState.swift reads off `source/state` exists on the
    backend's typed state AND is read by the vendored decoder; the two frozen
    enums hold the same values on both sides.

    Both directions matter. A key the backend drops fails the whole decode in
    the app for the required ones (source, switching, service, controls, the
    two flags, a session's id/phase/senders) — Milo-Mac then ignores every
    state and its menu freezes on the last one. A key the manifest keeps after
    the decoder stopped reading it over-constrains the backend.
    """
    from backend.core.models import audio_wire
    from backend.core.models.session import Phase, ServiceState

    inv = _INVARIANTS["source/state"]
    decoder = (VENDOR_DIR / inv["decoder"]).read_text()
    assert "struct MiloAudioState" in decoder, "vendored decoder unreadable"
    assert _sole_event_class("source", "state").__mro__[1] is audio_wire.AudioState

    missing_backend, missing_decoder = [], []
    for dotted in inv["data_keys"]:
        try:
            _resolve_on_model(audio_wire.AudioState, dotted)
        except KeyError as e:
            missing_backend.append(str(e))
        leaf = dotted.split(".")[-1]
        if not re.search(rf"\b{re.escape(leaf)}\b", decoder):
            missing_decoder.append(dotted)
    details = {
        "radio": audio_wire.RadioDetails, "music_library": audio_wire.MusicLibraryDetails,
        "cd": audio_wire.CdDetails,
    }
    for kind, keys in inv["details_keys"].items():
        for dotted in keys:
            try:
                _resolve_on_model(details[kind], dotted)
            except KeyError as e:
                missing_backend.append(f"details[{kind}].{e}")
            if not re.search(rf"\b{re.escape(dotted.split('.')[-1])}\b", decoder):
                missing_decoder.append(f"details[{kind}].{dotted}")
    assert not missing_backend, f"the state lost key(s) Milo-Mac decodes: {missing_backend}"
    assert not missing_decoder, (
        f"manifest keys the vendored decoder never reads: {missing_decoder} — "
        "refresh the manifest from MiloAudioState.swift"
    )

    enums = inv["frozen_enums"]
    assert enums["service"] == [s.value for s in ServiceState] == _swift_enum_cases(decoder, "ServiceState")
    assert enums["session.phase"] == [p.value for p in Phase] == _swift_enum_cases(decoder, "Phase")


def _volume_state_wire() -> dict:
    """What `volume_changed.state` carries: the real VolumeState.to_dict(), with
    one client and one zone so the nested keys have somewhere to resolve."""
    from backend.core.models.volume_state import ClientVolume, VolumeState, ZoneVolume

    mac = "dc:a6:32:7e:d3:43"
    return VolumeState(
        mode="multiroom", global_volume_db=-30.0, global_mute=False,
        limit_min_db=-80.0, limit_max_db=0.0,
        clients={mac: ClientVolume(-30.0, False)},
        zones={"z1": ZoneVolume("z1", "Salon", [mac], -30.0, False)},
    ).to_dict()


def test_invariant_volume_changed():
    """Every key Milo-Mac reads off volume_changed exists in what the backend
    sends AND is read by the vendored Swift; the per-client and per-zone keys
    are exactly the ones MultiroomVolume(state:) reads.

    `state.*` resolves against the real VolumeState.to_dict(), not against the
    dataclass's fields: the wire is that hand-written dict, so a key renamed in
    it (or in ClientVolume/ZoneVolume.to_dict) kept every dataclass field and
    the old check green while the app's sliders fell back to -80 dB.
    """
    cls = _sole_event_class("volume", "volume_changed")
    wire = _volume_state_wire()
    assert wire["clients"] and wire["zones"], "the producer built no client or zone — the nested checks would be vacuous"
    api_swift = (VENDOR_DIR / "MiloAPIService.swift").read_text()
    corpus = api_swift + (VENDOR_DIR / "WebSocketService.swift").read_text()

    data_keys = _INVARIANTS["volume_changed"]["data_keys"]
    missing_backend, missing_app = [], []
    for dotted in data_keys:
        head, _, sub = dotted.partition(".")
        if head not in cls.model_fields:
            missing_backend.append(f"{cls.__name__} has no `{head}`")
        if sub:
            assert head == "state", (
                f"Unexpected nested invariant `{dotted}` — teach this test how "
                f"to resolve `{head}.*` before changing the manifest."
            )
            try:
                _resolve_on_payload(wire, sub)
            except KeyError as e:
                missing_backend.append(str(e))
        leaf = dotted.split(".")[-1].split("[")[0]
        if f'"{leaf}"' not in corpus:
            missing_app.append(dotted)
    assert not missing_backend, f"volume_changed lost key(s) Milo-Mac reads: {missing_backend}"
    assert not missing_app, f"manifest keys the vendored Swift never reads: {missing_app}"

    # MultiroomVolume reads the clients first, then the zones.
    clients_part, _, zones_part = _swift_struct(api_swift, "MultiroomVolume").partition('state["zones"]')
    decoded = {f'state.clients[mac].{k}' for k in re.findall(r'dict\["(\w+)"\]', clients_part)}
    decoded |= {f'state.zones[id].{k}' for k in re.findall(r'dict\["(\w+)"\]', zones_part)}
    assert any("clients[" in k for k in decoded) and any("zones[" in k for k in decoded), (
        "MultiroomVolume extraction found no client or no zone key — extractor drift?"
    )
    pinned = {k for k in data_keys if "[" in k}
    assert pinned == decoded, (
        f"the manifest's nested volume keys {sorted(pinned)} != what MultiroomVolume(state:) "
        f"reads {sorted(decoded)} — refresh the manifest from the vendored MiloAPIService.swift"
    )


@pytest.mark.parametrize("pair_key", [
    "settings/volume_limits_changed", "settings/dock_apps_changed", "settings/mac_roc_changed",
])
def test_invariant_settings_payloads(pair_key):
    """Settings payload sub-models must keep the fields Milo-Mac reads."""
    inv = _INVARIANTS[pair_key]
    category, evt_type = pair_key.split("/")
    cls = _sole_event_class(category, evt_type)

    assert inv["data_key"] in cls.model_fields, (
        f"{cls.__name__} lost `{inv['data_key']}` — Milo-Mac reads it."
    )
    sub_model = cls.model_fields[inv["data_key"]].annotation
    for key in inv["subkeys"]:
        assert key in sub_model.model_fields, (
            f"{sub_model.__name__} lost `{key}` — Milo-Mac reads "
            f"`{inv['data_key']}.{key}` on {pair_key}."
        )


def test_all_payload_invariants_are_verified():
    """A new manifest invariant must not silently skip verification: this list
    mirrors the test functions above (routing/multiroom_error is presence-only,
    covered by test_ws_broadcast_site_exists)."""
    verified = {
        "source/state",
        "volume_changed",
        "settings/volume_limits_changed",
        "settings/dock_apps_changed",
        "settings/mac_roc_changed",
        "routing/multiroom_error",
        "multiroom/client_state_changed",
        "multiroom/zone_changed",
    }
    assert set(_INVARIANTS) == verified, (
        "payload_invariants changed in the manifest — add/remove the matching "
        "verification test in this file, then update this list."
    )
    presence_only = [
        "routing/multiroom_error",
        "multiroom/client_state_changed",
        "multiroom/zone_changed",
    ]
    for pair_key in presence_only:
        assert _INVARIANTS[pair_key]["data_keys"] == [], (
            f"{pair_key} is documented as presence-only; if Milo-Mac now reads "
            f"payload fields, write a real invariant test for them."
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
        "conscious commit. See CLAUDE.md §'External API clients — Milo-Mac and Milo-iOS'."
    )


def test_every_consumer_named_exists_in_the_snapshot():
    """A `consumer` must name something the vendored Milo-Mac source contains.

    The field is what makes a contract failure actionable — it points at the
    Swift function to fix instead of at a path — so a name that resolves to
    nothing points the reader at a function that does not exist, with the same
    authority as one that does. Nothing checked it, and TEN had rotted: the two
    REST entries still said `applyZoneDelta` and `fetchMultiroomTopology`
    (renamed to setZoneVolumeDelta / fetchMultiroomState), and every WS entry
    named a `handle*` method deleted in the 9727a28 refactor. One was worse than
    absent — `handleVolumeChange` still EXISTS, in VolumeController, where it
    takes a Double from the hotkey path and has nothing to do with the
    volume/volume_changed event. A reader following it would have landed in the
    wrong file and believed they were right.

    This is why a WS `consumer` now names the decoder in WebSocketService, which
    IS vendored and therefore checkable. `store_delegate` is checked too, and
    the first draft of this test wrongly excused it as unverifiable "because
    MiloStore.swift is not vendored": the IMPLEMENTATIONS live there, but every
    one of them is DECLARED in the WebSocketServiceDelegate protocol inside the
    vendored WebSocketService.swift, so the name is right here. Excusing a field
    that can be checked is how the ten names rotted in the first place — the
    exemption, not the rename, is what kept them quiet.

    The rule is the freshness script's `missing_consumers`, which the weekly job
    applies to upstream: this test alone only proves the names against a
    snapshot that may already be behind.
    """
    corpus = "\n".join(
        (VENDOR_DIR / name).read_text() for name in _MANIFEST["_snapshot"]["files"]
    )
    assert "func " in corpus, "vendored snapshot unreadable — the check below cannot fail"

    missing = _FRESHNESS.missing_consumers(_MANIFEST, corpus)
    assert not missing, (
        "consumers naming a symbol absent from vendor/milo-mac/:\n  "
        + "\n  ".join(missing)
        + "\nRefresh the snapshot, or correct the name to what Milo-Mac calls it now."
    )


def _upstream_copy(tmp_path: Path) -> Path:
    """A Milo-Mac checkout laid out as the freshness job clones it, holding the
    vendored snapshot: the stand-in for an upstream that has not moved."""
    base = tmp_path / "Milo Mac"
    base.mkdir()
    for name in _MANIFEST["_snapshot"]["files"]:
        (base / name).write_text((VENDOR_DIR / name).read_text())
    return base


def test_freshness_reports_an_unmoved_upstream_in_sync(tmp_path, capsys):
    """The control for the test below: an upstream identical to the snapshot
    must read as in sync, or a check that flags everything would pass it."""
    _upstream_copy(tmp_path)

    assert _FRESHNESS.main(["x", str(tmp_path)]) == 0
    assert "in sync" in capsys.readouterr().out


def test_freshness_flags_a_renamed_delegate(tmp_path, capsys):
    """An upstream that renamed a delegate the manifest names must not read as in sync.

    The weekly job judged upstream on the extracted surface only, and a rename
    moves no route and no event: Milo-Mac 2ed3957 renamed the delegate behind
    routing/multiroom_error, the job answered "in sync" with exit 0, and its
    "back in sync" step would have closed the drift issue — while the manifest's
    pointer at the entry easiest to delete by accident named a function that no
    longer existed.
    """
    base = _upstream_copy(tmp_path)
    event = next(
        e for e in _MANIFEST["ws"]["events"]
        if (e["category"], e["type"]) == ("routing", "multiroom_error")
    )
    delegate = re.split(r"[ (+]", event["store_delegate"])[0]
    assert len(re.findall(rf"\b{delegate}\b", (base / "WebSocketService.swift").read_text())) >= 2, (
        f"{delegate} should be declared and called in WebSocketService.swift"
    )
    for swift in base.iterdir():
        swift.write_text(re.sub(rf"\b{delegate}\b", f"{delegate}Renamed", swift.read_text()))

    assert _FRESHNESS.main(["x", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "in sync" not in out
    assert f"store_delegate -> {event['store_delegate']}" in out


def test_manifest_is_self_consistent():
    """Guard the manifest's own shape so a malformed edit fails loudly."""
    assert _MANIFEST["rest"], "manifest has no REST entries"
    assert _MANIFEST["ws"]["events"], "manifest has no WS events"
    for e in _MANIFEST["rest"]:
        assert e["method"] in {"GET", "POST", "PUT", "PATCH", "DELETE"}, e
        assert e["path"].startswith("/api/"), e
        assert e.get("consumer"), e
        assert not _BODIES.format_problems(e), (e["path"], _BODIES.format_problems(e))
    with_keys = {(e["method"], e["path"]) for e in _ENTRIES_WITH_RESPONSE_KEYS}
    assert with_keys == set(_RESPONSE_PRODUCERS), (
        "every entry carrying response_keys needs a producer in _RESPONSE_PRODUCERS, "
        f"and every producer an entry: entries {sorted(with_keys)}, producers {sorted(_RESPONSE_PRODUCERS)}"
    )
    for e in _MANIFEST["ws"]["events"]:
        assert e["category"] and e["type"] and e.get("consumer"), e
