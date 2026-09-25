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
        f"{MANIFEST_PATH.name}. See CLAUDE.md §'External API clients — Milo-Mac and Milo-iOS'."
    )


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


def test_invariant_volume_changed():
    """volume_changed must keep every data key Milo-Mac reads; `state.*` keys
    resolve against VolumeState (the payload docstring pins state =
    VolumeState.to_dict())."""
    import dataclasses

    from backend.core.models.volume_state import VolumeState

    cls = _sole_event_class("volume", "volume_changed")
    volume_state_keys = {f.name for f in dataclasses.fields(VolumeState)}

    for dotted in _INVARIANTS["volume_changed"]["data_keys"]:
        head, _, sub = dotted.partition(".")
        assert head in cls.model_fields, (
            f"{cls.__name__} lost `{head}` — Milo-Mac reads `{dotted}`."
        )
        if sub:
            assert head == "state", (
                f"Unexpected nested invariant `{dotted}` — teach this test how "
                f"to resolve `{head}.*` before changing the manifest."
            )
            assert sub in volume_state_keys, (
                f"VolumeState lost `{sub}` — Milo-Mac reads `{dotted}` on "
                f"volume/volume_changed."
            )


@pytest.mark.parametrize("pair_key", ["settings/volume_limits_changed", "settings/dock_apps_changed"])
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
    """
    corpus = "\n".join(
        (VENDOR_DIR / name).read_text()
        for name in ("MiloAPIService.swift", "WebSocketService.swift", "MiloAudioState.swift")
    )
    assert "func " in corpus, "vendored snapshot unreadable — the check below cannot fail"

    missing = []
    for entry in _MANIFEST["rest"]:
        for consumer in (c.strip() for c in entry["consumer"].split(",")):
            symbol = consumer.split(".")[-1]
            if not re.search(rf"\b{re.escape(symbol)}\b", corpus):
                missing.append(f"REST {entry['method']} {entry['path']} -> {consumer}")
    for event in _MANIFEST["ws"]["events"]:
        for field in ("consumer", "store_delegate"):
            value = event.get(field)
            if not value:
                continue
            # `didReceiveMultiroomTransitionComplete(success: false)` names the
            # call, not just the symbol: take the identifier and drop the args.
            symbol = re.split(r"[ (+]", value.split(".")[-1])[0]
            if not re.search(rf"\b{re.escape(symbol)}\b", corpus):
                missing.append(
                    f"WS {event['category']}/{event['type']} {field} -> {value}"
                )

    assert not missing, (
        "consumers naming a symbol absent from vendor/milo-mac/:\n  "
        + "\n  ".join(missing)
        + "\nRefresh the snapshot, or correct the name to what Milo-Mac calls it now."
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
