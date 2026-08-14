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
    try_request, plus raw aiohttp URLs built on CLIENT_API_PORT);
  * extract every route `milo-client/app/routes/` actually serves;
  * assert the first set is a subset of the second.

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


def _raw_aiohttp_calls(tree):
    """(method, path) for `session.<verb>(f"http://…:{CLIENT_API_PORT}/x")` calls.

    A few callers bypass the proxy service and build the URL inline, either
    passing the f-string straight to the verb or binding it to a local first.
    Both forms are resolved here; the generic transport in client_proxy.py,
    whose suffix is a bare `{path}` placeholder, is skipped — its concrete paths
    come from the request()/try_request() call sites instead.
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
            if "{CLIENT_API_PORT}" not in url:
                continue
            suffix = url.split("{CLIENT_API_PORT}", 1)[1]
            if re.fullmatch(r"\{[^}]*\}", suffix):
                continue  # generic transport, not a concrete endpoint
            calls.add((verb, _normalise(suffix)))
    return calls


def _proxy_calls(tree, param_values):
    """(method, path) for every EqualizerClientProxyService request/try_request."""
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
        for candidate in _path_candidates(node.args[2], param_values):
            calls.add((method_node.value, _normalise(candidate)))
    return calls


def _satellite_calls():
    """(method, normalised path) for every satellite call in backend/."""
    calls = set()
    for path in _backend_modules():
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError as exc:  # pragma: no cover - a broken tree is a real failure
            pytest.fail(f"cannot parse {path}: {exc}")
        param_values = _literal_values_by_param_name(tree)
        calls |= _proxy_calls(tree, param_values)
        calls |= _raw_aiohttp_calls(tree)
    return calls


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


_CALLS = _satellite_calls()
_ROUTES = _satellite_routes()


def test_extractors_are_not_vacuous():
    """A broken parse must fail here, not silently make the contract test pass."""
    assert CLIENT_ROUTES_DIR.is_dir(), f"milo-client routes missing at {CLIENT_ROUTES_DIR}"
    assert len(_ROUTES) >= 20, f"milo-client route extraction looks broken: {sorted(_ROUTES)}"
    assert len(_CALLS) >= 10, f"backend satellite-call extraction looks broken: {sorted(_CALLS)}"
    # The dynamic call sites must have resolved; an unresolved `{name}` segment
    # means _literal_values_by_param_name stopped finding the literals.
    unresolved = {c for c in _CALLS if "{}" in c[1] and c[1].count("{}") > 1}
    assert not unresolved, f"unresolved dynamic satellite paths: {sorted(unresolved)}"


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
    """Field names of every Pydantic model milo-client declares."""
    tree = ast.parse((CLIENT_APP_DIR / "models.py").read_text())
    return {
        node.name: {
            stmt.target.id
            for stmt in node.body
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
        }
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }


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


def _match_route(method: str, path: str) -> tuple[set[str], set[str]] | None:
    """Accepted body keys for a path whose parameters are concrete at runtime.

    The captured path carries a real filter id (`/equalizer/filter/eq_band_00`)
    where milo-client declares a template (`/equalizer/filter/{}`), so match
    segment by segment with `{}` standing for any one segment.
    """
    if (method, path) in _ACCEPTED:
        return _ACCEPTED[(method, path)]
    segments = path.strip("/").split("/")
    for (route_method, template), fields in _ACCEPTED.items():
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
