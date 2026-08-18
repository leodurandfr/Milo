"""Offline contract test: Milō backend ⇄ milo-client (the satellite agent).

`milo-client/app/` is a SECOND FastAPI application in this repository. It is
installed on every multiroom satellite and is the only thing listening on
CLIENT_API_PORT; the server backend drives each satellite's DSP entirely over
that HTTP surface (volume, mute, EQ bands, compressor, loudness, mono, the
master bypass gate, crossover/lowpass, snapclient buffer config).

Nothing else guards that surface. It is not versioned, both sides ship in the
same commit, and a mismatch produces no import error and no failing route — it
produces a satellite that silently ignores a command. Reproducing it needs a
second physical unit, which CI does not have and which the manual checklist
lists as its first blind spot. This test closes that gap using only the two
code bases already in the checkout:

  * extract every path the backend calls on a satellite (proxy request /
    try_request, plus raw aiohttp URLs built on CLIENT_API_PORT — under either
    spelling: the update service binds it to `self.satellite_api_port`);
  * extract every route `milo-client/app/routes/` actually serves;
  * assert the first set is a subset of the second;
  * then the same for what crosses the wire in each direction — every key the
    backend puts in a request body is one the handler reads, and every key it
    reads back off a response is one the handler returns.

Both extractors assert their own output is non-trivial first: a parse that
breaks must fail loudly, not pass on an empty surface (same doctrine as the
Milo-Mac contract test).

When this fails: either the backend gained a satellite call milo-client does not
serve (add the route there), or milo-client dropped/renamed a route the backend
still calls (restore it, or update the caller). Do not add a compatibility shim.
"""
import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
CLIENT_ROUTES_DIR = REPO_ROOT / "milo-client" / "app" / "routes"

_HTTP_METHODS = {"GET", "PUT", "POST", "DELETE", "PATCH"}
_PARAM_SEGMENT = re.compile(r"\{[^}]*\}")


def _normalise(path: str) -> str:
    """Collapse every path parameter to a bare `{}` so the two sides compare.

    The backend writes `f"/equalizer/filter/{filter_id}"`, milo-client declares
    `/equalizer/filter/{filter_id}` — same template, but the names need not match
    and only the shape is part of the contract.
    """
    return _PARAM_SEGMENT.sub("{}", path)


def _backend_modules():
    return sorted(
        p for p in BACKEND_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts and "tests" not in p.parts
    )


# --------------------------------------------------------------------------- #
# Side A: what the backend calls on a satellite.
# --------------------------------------------------------------------------- #

def _literal_values_by_param_name(tree):
    """Map parameter name → the string literals THIS module passes to it.

    Two call sites build their path from a variable (`f"/equalizer/{filter_name}"`
    in crossover.py, `f"/equalizer/{setting_type}"` in websocket.py). Resolving
    those needs the values that reach the parameter, and in both cases they are
    literals passed a frame or two up in the same module.

    Deliberately per-module, not repo-wide: parameter names repeat across
    unrelated code (`filter_name` also names CamillaDSP's internal
    "crossover_highpass"/"crossover_lowpass" filters), and pooling them globally
    invents satellite calls that no code makes.
    """
    params_of = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            params_of[node.name] = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]

    values = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        declared = params_of.get(fname)
        if not declared:
            continue
        # `self` occupies slot 0 of a method's parameter list but is never
        # passed at a call site, so positional args start one slot later.
        offset = 1 if declared[0] in ("self", "cls") else 0
        for i, arg in enumerate(node.args):
            slot = i + offset
            if slot >= len(declared):
                continue
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                values.setdefault(declared[slot], set()).add(arg.value)
            elif isinstance(arg, ast.JoinedStr):
                values.setdefault(declared[slot], set()).add(_join(arg))
        for kw in node.keywords:
            if kw.arg in declared and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                values.setdefault(kw.arg, set()).add(kw.value.value)
    return values


def _join(node: ast.JoinedStr) -> str:
    """Render an f-string with every interpolation replaced by `{name}`."""
    out = []
    for part in node.values:
        if isinstance(part, ast.Constant):
            out.append(str(part.value))
        elif isinstance(part, ast.FormattedValue):
            inner = part.value
            name = inner.id if isinstance(inner, ast.Name) else (
                inner.attr if isinstance(inner, ast.Attribute) else "?"
            )
            out.append("{" + name + "}")
    return "".join(out)


def _path_candidates(node, param_values) -> set[str]:
    """Every concrete path a `path` argument can take at runtime."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if not isinstance(node, ast.JoinedStr):
        return set()

    template = _join(node)
    names = re.findall(r"\{([^}]*)\}", template)
    # A single interpolation of a value that is itself a path parameter
    # (`{filter_id}`) stays a template; one carrying a *segment* resolves.
    resolved = {template}
    for name in names:
        literals = param_values.get(name)
        if not literals:
            continue
        resolved = {
            candidate.replace("{" + name + "}", literal)
            for candidate in resolved
            for literal in literals
        }
    return resolved


def _port_markers(tree) -> set[str]:
    """Every f-string spelling of CLIENT_API_PORT this module can produce.

    `SatelliteUpdateService` binds the constant to `self.satellite_api_port` in
    __init__ and builds all six of its URLs from the attribute, so filtering on
    the imported name alone left `GET /status`, `POST /update`,
    `GET /update/status`, `POST /app/update`, `POST /camilladsp/update` and
    `GET /camilladsp/update/status` invisible to this contract — renaming any of
    them in milo-client kept the whole file green. Derived from the assignment
    rather than restated here, so a rename of the attribute follows the code.
    """
    markers = {"{CLIENT_API_PORT}"}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Name)):
            continue
        if node.value.id != "CLIENT_API_PORT":
            continue
        for target in node.targets:
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
            if name:
                markers.add("{" + name + "}")
    return markers


def _dict_bindings(tree) -> dict:
    """name → the dict literal it is bound to, module-wide; None when ambiguous.

    Two call sites hand the body over a local (`payload = {...}` in crossover.py
    and routing.py) rather than inline. A name bound to two different literals in
    one module resolves to None: an unknown body must not read as an empty one.
    """
    bindings = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id in bindings and bindings[target.id] is not node.value:
                bindings[target.id] = None
            else:
                bindings.setdefault(target.id, node.value)
    return bindings


def _literal_dict_keys(node, bindings):
    """Key names of a body written as a dict literal, inline or via a local.

    None when the literal cannot be enumerated — a `**spread`, a computed key, an
    unresolved name, or a body assembled by a `to_dict()`. Those are the bodies
    `_bodies_the_backend_sends()` drives instead; claiming an empty key set for
    them would silently cover nothing.
    """
    if isinstance(node, ast.Name):
        node = bindings.get(node.id)
    if not isinstance(node, ast.Dict):
        return None
    if not all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in node.keys):
        return None
    return frozenset(k.value for k in node.keys)


def _raw_aiohttp_calls(tree, markers, bindings):
    """(method, path, body keys) for `session.<verb>(f"http://…:{PORT}/x")` calls.

    A few callers bypass the proxy service and build the URL inline, either
    passing the f-string straight to the verb or binding it to a local first.
    Both forms are resolved here; the generic transport in client_proxy.py,
    whose suffix is a bare `{path}` placeholder, is skipped — its concrete paths
    come from the request()/try_request() call sites instead.

    The third slot is the key set of a `json=` body written as a dict literal, or
    None when there is no body or it cannot be enumerated.
    """
    calls = set()
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        bound = {
            target.id: _join(node.value)
            for node in ast.walk(scope)
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.JoinedStr)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(scope):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            verb = node.func.attr.upper()
            if verb not in _HTTP_METHODS or not node.args:
                continue
            arg = node.args[0]
            url = (
                _join(arg) if isinstance(arg, ast.JoinedStr)
                else bound.get(arg.id, "") if isinstance(arg, ast.Name)
                else ""
            )
            marker = next((m for m in markers if m in url), None)
            if marker is None:
                continue
            suffix = url.split(marker, 1)[1]
            if re.fullmatch(r"\{[^}]*\}", suffix):
                continue  # generic transport, not a concrete endpoint
            body = next((kw.value for kw in node.keywords if kw.arg == "json"), None)
            keys = _literal_dict_keys(body, bindings) if body is not None else None
            calls.add((verb, _normalise(suffix), keys))
    return calls


def _proxy_calls(tree, param_values, bindings):
    """(method, path, body keys) for every EqualizerClientProxyService call."""
    calls = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr in ("request", "try_request")):
            continue
        # proxy signature: (hostname, method, path, body=None, ...)
        if len(node.args) < 3:
            continue
        method_node = node.args[1]
        if not (isinstance(method_node, ast.Constant) and method_node.value in _HTTP_METHODS):
            continue
        body = node.args[3] if len(node.args) > 3 else next(
            (kw.value for kw in node.keywords if kw.arg == "body"), None
        )
        keys = _literal_dict_keys(body, bindings) if body is not None else None
        for candidate in _path_candidates(node.args[2], param_values):
            calls.add((method_node.value, _normalise(candidate), keys))
    return calls


def _satellite_surface():
    """(method, path, literal body keys) for every satellite call in backend/."""
    records = set()
    for path in _backend_modules():
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError as exc:  # pragma: no cover - a broken tree is a real failure
            pytest.fail(f"cannot parse {path}: {exc}")
        bindings = _dict_bindings(tree)
        records |= _proxy_calls(tree, _literal_values_by_param_name(tree), bindings)
        records |= _raw_aiohttp_calls(tree, _port_markers(tree), bindings)
    return records


# --------------------------------------------------------------------------- #
# Side B: what milo-client serves.
# --------------------------------------------------------------------------- #

def _satellite_routes():
    """(method, normalised path) for every route milo-client declares."""
    routes = set()
    for path in sorted(CLIENT_ROUTES_DIR.glob("*.py")):
        source = path.read_text()
        prefix_match = re.search(r'APIRouter\((?:[^)]*?)prefix\s*=\s*"([^"]*)"', source, re.S)
        prefix = prefix_match.group(1) if prefix_match else ""
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                    continue
                if dec.func.attr.upper() not in _HTTP_METHODS or not dec.args:
                    continue
                arg = dec.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    routes.add((dec.func.attr.upper(), _normalise(prefix + arg.value)))
    return routes


_SURFACE = _satellite_surface()
_ROUTES = _satellite_routes()
_CALLS = {(method, path) for method, path, _ in _SURFACE}

# Bodies written as a dict literal at the call site — the half
# `_bodies_the_backend_sends()` cannot drive, because their producers are nested
# closures or route handlers rather than a service method. Merged per endpoint:
# two call sites push `/snapclient/config` and only their union is the contract.
_LITERAL_BODIES: dict[tuple[str, str], set[str]] = {}
for _method, _path, _keys in _SURFACE:
    if _keys:
        _LITERAL_BODIES.setdefault((_method, _path), set()).update(_keys)


def test_extractors_are_not_vacuous():
    """A broken parse must fail here, not silently make the contract test pass."""
    assert CLIENT_ROUTES_DIR.is_dir(), f"milo-client routes missing at {CLIENT_ROUTES_DIR}"
    assert len(_ROUTES) >= 20, f"milo-client route extraction looks broken: {sorted(_ROUTES)}"
    assert len(_CALLS) >= 10, f"backend satellite-call extraction looks broken: {sorted(_CALLS)}"
    # The dynamic call sites must have resolved; an unresolved `{name}` segment
    # means _literal_values_by_param_name stopped finding the literals.
    unresolved = {c for c in _CALLS if "{}" in c[1] and c[1].count("{}") > 1}
    assert not unresolved, f"unresolved dynamic satellite paths: {sorted(unresolved)}"


def test_the_update_surface_is_visible_to_the_extractor():
    """The six update endpoints reach the satellite over a second port spelling.

    `SatelliteUpdateService` builds its URLs from `self.satellite_api_port`, not
    from the imported constant, and the extractor used to filter on the literal
    name — so this whole surface was outside the contract. Renaming
    `GET /update/status` in milo-client left all 28 tests green while
    `satellite.py` still called the old path.

    When this fails, the extractor stopped resolving that module (a renamed
    attribute, a URL built another way), not necessarily the routes themselves.
    """
    update_surface = {
        ("GET", "/status"),
        ("POST", "/update"),
        ("GET", "/update/status"),
        ("POST", "/app/update"),
        ("POST", "/camilladsp/update"),
        ("GET", "/camilladsp/update/status"),
    }
    assert update_surface <= _CALLS, (
        f"the satellite update surface fell out of the extractor: "
        f"{sorted(update_surface - _CALLS)}"
    )


@pytest.mark.parametrize("method,path", sorted(_CALLS))
def test_every_satellite_call_is_served_by_milo_client(method, path):
    """The backend must not call a satellite endpoint milo-client does not serve."""
    assert (method, path) in _ROUTES, (
        f"backend calls {method} {path} on a satellite, but milo-client/app/routes/ "
        f"does not serve it. Served: {sorted(p for m, p in _ROUTES if m == method)}"
    )


# --------------------------------------------------------------------------- #
# Payload keys: serving the route is not the same as reading the body.
#
# The route check above passes even when the satellite ignores half of what the
# backend sends — Pydantic drops unknown keys silently, and a handler that reads
# a `List[dict]` reads whichever keys it happens to name. That is how the server
# came to push `filter_type` at a batch endpoint that only ever applied
# id/gain/freq/q: no error, no log, just a band whose type never changed on the
# satellite while the server's record said it had.
# --------------------------------------------------------------------------- #

CLIENT_APP_DIR = REPO_ROOT / "milo-client" / "app"


def _client_model_fields() -> dict[str, set[str]]:
    """Field names of every Pydantic model milo-client declares.

    `models.py` is not the only home: `AudioUpdate` sits in `routes/hardware.py`
    beside its handler, and reading models.py alone resolved that route to an
    empty field set — which reads as "accepts nothing" and covers nothing.
    """
    fields = {}
    for path in [CLIENT_APP_DIR / "models.py", *sorted(CLIENT_ROUTES_DIR.glob("*.py"))]:
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ClassDef):
                continue
            fields[node.name] = {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            }
    return fields


def _dict_keys_read_in(node) -> set[str]:
    """Strings this subtree uses as a dict key: `f["g"]`, `f.get("g")`, `"g" in f`."""
    keys = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Subscript) and isinstance(sub.slice, ast.Constant):
            if isinstance(sub.slice.value, str):
                keys.add(sub.slice.value)
        elif (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
              and sub.func.attr == "get" and sub.args
              and isinstance(sub.args[0], ast.Constant)
              and isinstance(sub.args[0].value, str)):
            keys.add(sub.args[0].value)
        elif isinstance(sub, ast.Compare) and isinstance(sub.left, ast.Constant):
            if isinstance(sub.left.value, str) and any(isinstance(op, ast.In) for op in sub.ops):
                keys.add(sub.left.value)
    return keys


def _client_service_functions() -> dict[str, ast.AST]:
    """Every function milo-client's services define, by name."""
    functions = {}
    for path in sorted((CLIENT_APP_DIR / "services").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions[node.name] = node
    return functions


def _client_body_fields_by_route() -> dict[tuple[str, str], tuple[set[str], set[str]]]:
    """(method, path) → (top-level body keys, keys read inside a `List[dict]`).

    The first set comes from the handler's Pydantic model. The second is for
    fields Pydantic cannot describe: it follows the handler into the service
    functions it calls and collects the keys those read by name — per handler,
    not service-wide, so a key some *other* endpoint happens to read does not
    count as accepted here.
    """
    models = _client_model_fields()
    services = _client_service_functions()
    by_route = {}
    for path in sorted(CLIENT_ROUTES_DIR.glob("*.py")):
        source = path.read_text()
        prefix_match = re.search(r'APIRouter\((?:[^)]*?)prefix\s*=\s*"([^"]*)"', source, re.S)
        prefix = prefix_match.group(1) if prefix_match else ""
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            fields = set()
            for arg in node.args.args:
                name = getattr(arg.annotation, "id", None)
                if name in models:
                    fields |= models[name]

            inner = set()
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
                    target = services.get(call.func.attr)
                    if target is not None:
                        inner |= _dict_keys_read_in(target)

            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                    continue
                if dec.func.attr.upper() not in _HTTP_METHODS or not dec.args:
                    continue
                route = dec.args[0]
                if isinstance(route, ast.Constant) and isinstance(route.value, str):
                    key = (dec.func.attr.upper(), _normalise(prefix + route.value))
                    by_route[key] = (fields, inner)
    return by_route


def _bodies_the_backend_sends() -> dict[tuple[str, str], list[dict]]:
    """(method, path) → every real body sent there, by driving the producers.

    Driven rather than parsed: the payloads are assembled from dataclass
    `to_dict()`s and comprehensions that no AST walk resolves faithfully, and
    both producers are single functions, so running them is both simpler and
    truer than re-deriving what they would send.

    A list, not one body: two producers write to /equalizer/mono (the record
    push and the targeted setter) and keeping only the last would leave the
    other unchecked.
    """
    import asyncio
    from unittest.mock import AsyncMock, Mock

    from backend.core.equalizer.client_proxy import EqualizerClientProxyService
    from backend.core.multiroom.equalizer_router import EqualizerRouter
    from backend.core.multiroom.models import (
        EqFilter, EqualizerSettings, FilterType,
    )

    sent = {}

    async def capture(hostname, method, path, body=None, **_):
        if body is not None:
            sent.setdefault((method, _normalise(path)), []).append(body)
        return {"status": "success"}

    record = EqualizerSettings(
        filters=[EqFilter(id="eq_band_00", frequency=100, gain=1.0, q=1.41,
                          filter_type=FilterType.PEAKING, enabled=True)],
    )

    proxy = EqualizerClientProxyService()
    proxy.request = AsyncMock(side_effect=capture)

    client = Mock(is_local=False, online=True, ip="192.168.1.100", volume_control=True)
    registry = Mock(get_client=Mock(return_value=client))
    router = EqualizerRouter(registry, Mock(), proxy)

    async def drive():
        await proxy.apply_record("192.168.1.100", record)
        await router.set_volume("mac", -20.0)
        await router.set_mute("mac", True)
        await router.update_filter("mac", "eq_band_00", {
            "freq": 100, "gain": 1.0, "q": 1.41, "filter_type": "Peaking",
        })
        await router.set_compressor("mac", record.compressor.to_dict())
        await router.set_loudness("mac", record.loudness.to_dict())
        await router.set_mono("mac", {"enabled": True})

    asyncio.run(drive())
    return sent


_SENT = _bodies_the_backend_sends()
_ACCEPTED = _client_body_fields_by_route()


def _match_route(method: str, path: str, table=None):
    """The milo-client entry for a path whose parameters are concrete at runtime.

    The captured path carries a real filter id (`/equalizer/filter/eq_band_00`)
    where milo-client declares a template (`/equalizer/filter/{}`), so match
    segment by segment with `{}` standing for any one segment. `table` selects
    which side is being looked up: accepted body fields, or returned keys.
    """
    table = _ACCEPTED if table is None else table
    if (method, path) in table:
        return table[(method, path)]
    segments = path.strip("/").split("/")
    for (route_method, template), fields in table.items():
        if route_method != method:
            continue
        expected = template.strip("/").split("/")
        if len(expected) == len(segments) and all(
            e == "{}" or e == s for e, s in zip(expected, segments)
        ):
            return fields
    return None


def test_payload_extractors_are_not_vacuous():
    """A broken parse or a producer that sent nothing must fail here."""
    assert len(_SENT) >= 7, f"captured too few satellite bodies: {sorted(_SENT)}"
    assert sum(len(v) for v in _SENT.values()) >= 8, "a producer sent nothing"
    assert len(_ACCEPTED) >= 10, f"milo-client body-field extraction looks broken: {_ACCEPTED}"
    assert any(fields for fields, _ in _ACCEPTED.values()), "no model fields resolved"
    # The nested case is the one the route check cannot see — if it stops being
    # captured, or its handler stops resolving, the check below covers nothing.
    assert ("PUT", "/equalizer/filters") in _SENT
    assert _ACCEPTED[("PUT", "/equalizer/filters")][1], "batch handler's key reads not resolved"


@pytest.mark.parametrize("method,path", sorted(_SENT))
def test_every_key_the_backend_sends_is_read_by_milo_client(method, path):
    """A key the satellite drops is a command that silently did nothing."""
    matched = _match_route(method, path)
    assert matched is not None, f"no milo-client handler found for {method} {path}"
    accepted, inner_keys = matched

    for body in _SENT[(method, path)]:
        unread = set(body) - accepted
        assert not unread, (
            f"backend sends {sorted(unread)} to {method} {path}, but milo-client's "
            f"handler only accepts {sorted(accepted)} — Pydantic drops the rest silently"
        )

        for key, value in body.items():
            if not (isinstance(value, list) and value and isinstance(value[0], dict)):
                continue
            # A `List[dict]` field: Pydantic validates nothing inside, so the
            # contract for the inner keys is what this handler reads by name.
            for item in value:
                unread_inner = set(item) - inner_keys
                assert not unread_inner, (
                    f"backend sends {sorted(unread_inner)} inside `{key}` of {method} "
                    f"{path}, but milo-client's handler never reads those keys — they "
                    f"are dropped silently. It reads {sorted(inner_keys)}"
                )


def test_literal_body_extractor_is_not_vacuous():
    """The four bodies no producer can drive must still be captured here."""
    assert len(_LITERAL_BODIES) >= 8, f"literal-body extraction looks broken: {_LITERAL_BODIES}"
    # These four have no drivable producer — a nested closure in routing.py, a
    # private push in websocket.py, and two route handlers in api/multiroom.py —
    # so if the literal walk stops resolving them, nothing else covers them.
    for endpoint in (
        ("PUT", "/snapclient/config"),
        ("PUT", "/equalizer/crossover"),
        ("PUT", "/equalizer/lowpass"),
        ("PUT", "/api/hardware/audio"),
    ):
        assert _LITERAL_BODIES.get(endpoint), f"no literal body captured for {endpoint}"


@pytest.mark.parametrize("method,path", sorted(_LITERAL_BODIES))
def test_every_key_in_a_literal_body_is_read_by_milo_client(method, path):
    """Same rule as above, for the bodies written inline at the call site.

    `PUT /snapclient/config` is the one that already came apart once: the pair it
    carries is restated on both sides because a satellite tarball ships without
    `backend/`. A key renamed on either side is a buffer setting the satellite
    drops in silence, and only a second physical unit shows it.
    """
    matched = _match_route(method, path)
    assert matched is not None, f"no milo-client handler found for {method} {path}"
    accepted, _ = matched

    unread = _LITERAL_BODIES[(method, path)] - accepted
    assert not unread, (
        f"backend sends {sorted(unread)} to {method} {path}, but milo-client's "
        f"handler only accepts {sorted(accepted)} — Pydantic drops the rest silently"
    )


# --------------------------------------------------------------------------- #
# Side D: the answer, not just the question.
#
# The two checks above cover what the backend SENDS. Nothing covered what it
# READS back, and `satellite.py` subscripts a satellite's answer bare —
# `data["target_version"]` at the one moment a fleet update commits. A satellite
# that renamed that key raises KeyError inside a `try:` whose handler reports
# "Error updating satellite <mac>", which is indistinguishable from the network
# being down and points at nothing.
#
# Scope: the responses read inline off a raw aiohttp call. The proxy service
# returns parsed JSON to its caller, far from the URL that produced it, so a
# proxy response's keys cannot be attributed to an endpoint by reading the code —
# those stay uncovered here rather than covered vaguely.
# --------------------------------------------------------------------------- #

def _json_read_path(node, bindings, resp_var):
    """Dotted path of a read rooted at `await <resp_var>.json()`, else None.

    Resolves the three forms the backend uses, through any depth of chaining:
    the root itself, `.get("k")` (with or without a default) and `["k"]`. So
    `data.get("app", {}).get("started_at")` reads as `app.started_at`.
    """
    if isinstance(node, ast.Await):
        call = node.value
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "json"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == resp_var):
            return ""
        return None
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.Call):
        fn = node.func
        if (isinstance(fn, ast.Attribute) and fn.attr == "get" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            base = _json_read_path(fn.value, bindings, resp_var)
            return None if base is None else f"{base}.{node.args[0].value}".lstrip(".")
        return None
    if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)):
        base = _json_read_path(node.value, bindings, resp_var)
        return None if base is None else f"{base}.{node.slice.value}".lstrip(".")
    return None


def _reads_in_block(block, resp_var) -> set[str]:
    """Every response key read inside one `async with session.<verb>(…)` block.

    Bindings are resolved to a fixed point first: the payload is bound to a local
    once (`data = await response.json()`) or twice (`audio = (await
    resp.json()).get("audio", {})`), and only reads on THAT local count — which
    is what keeps a nested block's own payload from being attributed here.
    """
    bindings = {}
    for _ in range(3):
        for node in ast.walk(block):
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
                continue
            if not isinstance(node.targets[0], ast.Name):
                continue
            path = _json_read_path(node.value, bindings, resp_var)
            if path is not None:
                bindings[node.targets[0].id] = path

    reads = set()
    for node in ast.walk(block):
        path = _json_read_path(node, bindings, resp_var)
        if path:
            reads.add(path)
    return reads


def _response_reads() -> dict[tuple[str, str], set[str]]:
    """(method, path) → the response keys the backend reads off that endpoint."""
    out = {}
    for module in _backend_modules():
        tree = ast.parse(module.read_text())
        markers = _port_markers(tree)
        for scope in ast.walk(tree):
            if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            bound = {
                target.id: _join(node.value)
                for node in ast.walk(scope)
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.JoinedStr)
                for target in node.targets
                if isinstance(target, ast.Name)
            }
            for node in ast.walk(scope):
                if not isinstance(node, ast.AsyncWith):
                    continue
                for item in node.items:
                    call = item.context_expr
                    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                        continue
                    verb = call.func.attr.upper()
                    if verb not in _HTTP_METHODS or not call.args:
                        continue
                    arg = call.args[0]
                    url = (
                        _join(arg) if isinstance(arg, ast.JoinedStr)
                        else bound.get(arg.id, "") if isinstance(arg, ast.Name)
                        else ""
                    )
                    marker = next((m for m in markers if m in url), None)
                    if marker is None or not isinstance(item.optional_vars, ast.Name):
                        continue
                    suffix = _normalise(url.split(marker, 1)[1])
                    reads = _reads_in_block(node, item.optional_vars.id)
                    if reads:
                        out.setdefault((verb, suffix), set()).update(reads)
    return out


def _flatten_returned(dict_node, prefix, keys, opaque) -> bool:
    """Dotted keys of a returned dict literal; False when it cannot be enumerated.

    A `**spread` or a computed key leaves the shape open, and a value that is not
    itself a literal (a name, a call) is recorded as opaque: the key exists, but
    nothing below it can be checked from the source.
    """
    for key, value in zip(dict_node.keys, dict_node.values):
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            return False
        path = prefix + key.value
        keys.add(path)
        if isinstance(value, ast.Dict):
            if not _flatten_returned(value, path + ".", keys, opaque):
                return False
        else:
            opaque.add(path)
    return True


def _client_response_shapes() -> dict[tuple[str, str], tuple[set[str], set[str], bool]]:
    """(method, path) → (returned keys, opaque keys, enumerable).

    The union of every `return {...}` in the handler, deliberately: `POST /update`
    answers `target_version` on the branch that started one and `latest_version`
    on the branch that found nothing to do, and the backend reads the first only
    after `data.get("success")`. Per-branch would report a false mismatch.
    """
    shapes = {}
    for path in sorted(CLIENT_ROUTES_DIR.glob("*.py")):
        source = path.read_text()
        prefix_match = re.search(r'APIRouter\((?:[^)]*?)prefix\s*=\s*"([^"]*)"', source, re.S)
        prefix = prefix_match.group(1) if prefix_match else ""
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            keys, opaque, enumerable = set(), set(), True
            for sub in ast.walk(node):
                if not (isinstance(sub, ast.Return) and sub.value is not None):
                    continue
                if isinstance(sub.value, ast.Dict):
                    enumerable &= _flatten_returned(sub.value, "", keys, opaque)
                else:
                    enumerable = False
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                    continue
                if dec.func.attr.upper() not in _HTTP_METHODS or not dec.args:
                    continue
                route = dec.args[0]
                if isinstance(route, ast.Constant) and isinstance(route.value, str):
                    shapes[(dec.func.attr.upper(), _normalise(prefix + route.value))] = (
                        keys, opaque, enumerable
                    )
    return shapes


_RESPONSE_READS = _response_reads()
_RESPONSE_SHAPES = _client_response_shapes()

# `GET /api/hardware` returns hardware.json verbatim, so its shape is a file's,
# not the handler's, and no source read can enumerate it. Named here so a SECOND
# route going unenumerable — a handler rewritten to return a variable — surfaces
# as a failure instead of quietly leaving the check.
_UNENUMERABLE = {("GET", "/api/hardware")}
_CHECKED_RESPONSES = sorted(
    endpoint for endpoint in _RESPONSE_READS
    if (_match_route(*endpoint, table=_RESPONSE_SHAPES) or (None, None, False))[2]
)


def test_response_extractors_are_not_vacuous():
    """A read the walk stops resolving must fail here, not shrink the contract."""
    assert len(_RESPONSE_READS) >= 5, f"response-read extraction looks broken: {_RESPONSE_READS}"
    # The nested form is the fragile one: every version the update path compares
    # is read two levels down (`app.version`, `snapclient.version`).
    nested = {k for reads in _RESPONSE_READS.values() for k in reads if "." in k}
    assert nested, "no nested response read resolved — _json_read_path stopped chaining"
    unenumerable = {
        endpoint for endpoint in _RESPONSE_READS
        if not (_match_route(*endpoint, table=_RESPONSE_SHAPES) or (None, None, False))[2]
    }
    assert unenumerable == _UNENUMERABLE, (
        f"the set of satellite responses this check cannot verify moved: {sorted(unenumerable)}"
    )


@pytest.mark.parametrize("method,path", _CHECKED_RESPONSES)
def test_every_response_key_the_backend_reads_is_returned_by_milo_client(method, path):
    """A key the satellite stopped answering is a KeyError reported as a timeout."""
    returned, opaque, _ = _match_route(method, path, table=_RESPONSE_SHAPES)

    for read in sorted(_RESPONSE_READS[(method, path)]):
        # A key whose value is not a literal on the satellite side ends the
        # chain: `current_version` holds whatever the service returned, so
        # nothing under it is knowable from the source.
        if any(read.startswith(o + ".") for o in opaque):
            continue
        assert read in returned, (
            f"backend reads `{read}` from the {method} {path} response, but "
            f"milo-client's handler never returns it. It returns {sorted(returned)}"
        )


# --------------------------------------------------------------------------- #
# Side C: a value both trees bound, and must bound identically.
# --------------------------------------------------------------------------- #

def _satellite_snapclient_bounds() -> dict:
    """field → (ge, le) as milo-client's SnapclientConfigUpdate declares them."""
    tree = ast.parse((CLIENT_APP_DIR / "models.py").read_text())
    bounds = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == "SnapclientConfigUpdate"):
            continue
        for stmt in node.body:
            if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                continue
            call = stmt.value
            if not (isinstance(call, ast.Call) and getattr(call.func, "id", None) == "Field"):
                continue
            limits = {
                kw.arg: kw.value.value
                for kw in call.keywords
                if kw.arg in ("ge", "le") and isinstance(kw.value, ast.Constant)
            }
            if {"ge", "le"} <= set(limits):
                bounds[stmt.target.id] = (limits["ge"], limits["le"])
    return bounds


def test_snapclient_bounds_agree_across_the_two_trees():
    """The ALSA buffer pair is bounded on both sides, and the two must be one range.

    A satellite tarball ships without `backend/`, so milo-client cannot import
    SNAPCLIENT_LIMITS and restates it — which is exactly how the two came apart:
    the server clamped buffer_time to 200 ms while the satellites accepted 300,
    so a single write left the local speaker on a different ALSA buffer than
    every other room, with nothing anywhere reporting a disagreement.

    When this fails, move both declarations together — do not widen one side to
    absorb the other.
    """
    from backend.core.multiroom.routing import SNAPCLIENT_LIMITS

    bounds = _satellite_snapclient_bounds()
    assert set(bounds) == {"buffer_time", "fragments"}, (
        f"SnapclientConfigUpdate no longer declares a bounded pair: {bounds}. "
        "Either the model moved, or a field lost its Field(ge=…, le=…) — in which "
        "case the satellite accepts anything the server sends and this check is blind."
    )
    assert bounds == {k: tuple(v) for k, v in SNAPCLIENT_LIMITS.items()}
