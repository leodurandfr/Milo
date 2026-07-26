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
