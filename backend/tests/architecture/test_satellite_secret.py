"""Structural guardrail: a satellite's paths to root answer its server, and the server signs every request to them.

`milo-client` is root by construction, and until the secret every host on the
LAN could push it a tarball or open its SSH. Owner decision, 2026-11-13: a
satellite takes its paths to root from its own server only; reading and
settings stay open to the LAN. Two trees carry that decision and no import
connects them — a satellite update ships `milo-client/` alone — so it is held
here, from source, on both sides:

- the two spellings of the secret's header and form field are equal;
- the satellite requires the secret on exactly `TO_ROOT`, the decision's list:
  a route of that list that drops the guard, or a setting that gains one,
  fails. The list is not every route that reaches root: `POST /update` and
  `POST /camilladsp/update` install as root a release upstream published, at
  the version the caller names, and stay open to the LAN, outside it;
- every request the server sends to one of those routes carries the secret,
  through `core/multiroom/satellite_http.py` and nowhere else: a relay sent
  bare is a speaker's panel that answers 403 on a second physical unit only;
- the pin lives where an update's trial cannot take it: a pin lost with an
  `app/` rolled back is a satellite that pins the next host that asks.

Behavior is driven elsewhere: `milo-client/app/tests/test_server_secret.py`
(the satellite's refusals, the pin) and `tests/test_satellite_secret.py` (the
key, one secret on every path). Every extractor asserts its own output is
non-trivial first.
"""
import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
CLIENT_APP = REPO / "milo-client" / "app"
CLIENT_ROUTES = CLIENT_APP / "routes"
SERVER_HALF = BACKEND / "core" / "multiroom" / "satellite_http.py"
SATELLITE_HALF = CLIENT_APP / "server_secret.py"
UPDATER = CLIENT_APP / "services" / "app_update.py"

# The decision, route by route: the paths to root it closes on a satellite, and
# the hand-over that pins the secret. Left out on purpose, open to the LAN: the
# two dependency installs, `POST /update` (snapclient) and `POST /camilladsp/update`.
TO_ROOT = {
    ("POST", "/app/update"),
    ("PUT", "/api/system/ssh"),
    ("POST", "/api/system/password"),
    ("POST", "/api/hardware/reboot"),
    ("PUT", "/api/system/server-secret"),
}
MULTIPART = ("POST", "/app/update")

# The server's modules that put the secret on a request, besides its own module.
SIGNERS = {"api/multiroom.py", "core/updates/satellite.py"}

_VERBS = {"GET", "PUT", "POST", "DELETE", "PATCH"}
_GUARDS = {"require_server_secret", "guard"}


def _constants(path: Path) -> dict:
    found = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = node.value
            if isinstance(value, ast.Call) and getattr(value.func, "id", None) == "Path" and value.args:
                value = value.args[0]
            try:
                found[node.targets[0].id] = ast.literal_eval(value)
            except ValueError:
                continue
    return found


SERVER = _constants(SERVER_HALF)
SATELLITE = _constants(SATELLITE_HALF)


def test_both_halves_spell_the_secret_alike():
    """A header renamed on one side is every path to root refused, fleet-wide,
    after the release that carried it."""
    assert SERVER["SATELLITE_SECRET_HEADER"] == SATELLITE["SECRET_HEADER"]
    assert SERVER["SATELLITE_SECRET_FIELD"] == SATELLITE["SECRET_FIELD"]


# --------------------------------------------------------------------------- #
# The satellite
# --------------------------------------------------------------------------- #

def _routes(path: Path):
    """(method, full path, handler node) for every route a satellite module declares."""
    source = path.read_text(encoding="utf-8")
    prefix = re.search(r'APIRouter\((?:[^)]*?)prefix\s*=\s*"([^"]*)"', source, re.S)
    prefix = prefix.group(1) if prefix else ""
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr.upper() in _VERBS and dec.args
                    and isinstance(dec.args[0], ast.Constant)):
                yield dec.func.attr.upper(), prefix + dec.args[0].value, node, dec


def _requires_the_secret(handler, decorator) -> bool:
    """`dependencies=[Depends(require_server_secret)]`, a parameter that
    depends on it, or a call to the guard in the body."""
    names = {
        n.id for part in (decorator, handler.args, *handler.body)
        for n in ast.walk(part) if isinstance(n, ast.Name)
    }
    return bool(names & _GUARDS)


def _satellite_routes():
    routes = {}
    for path in sorted(CLIENT_ROUTES.glob("*.py")):
        for method, route, handler, decorator in _routes(path):
            routes[(method, route)] = (handler, decorator)
    assert len(routes) >= 20, f"satellite route extraction looks broken: {sorted(routes)}"
    return routes


def test_the_satellite_requires_the_secret_on_every_path_to_root_and_nowhere_else():
    """Reading and settings stay open to the LAN (owner decision 2026-11-13):
    a guard on the volume would lock the server's own push out of a speaker
    still waiting for its first admission."""
    guarded = {
        route for route, (handler, decorator) in _satellite_routes().items()
        if _requires_the_secret(handler, decorator)
    }

    assert guarded == TO_ROOT


def test_the_update_form_takes_the_secret_without_requiring_it():
    """The form's names are frozen and append-only: required, the field would
    be a 422 from every satellite a retracted release's server pushes to,
    rather than the 403 that says why."""
    handler, _ = _satellite_routes()[MULTIPART]
    args = handler.args
    defaults = dict(zip([a.arg for a in args.args[len(args.args) - len(args.defaults):]], args.defaults))
    field = defaults.get(SATELLITE["SECRET_FIELD"])

    assert isinstance(field, ast.Call) and getattr(field.func, "id", None) == "Form", (
        f"POST /app/update declares no Form() parameter named {SATELLITE['SECRET_FIELD']!r}"
    )
    assert field.args and isinstance(field.args[0], ast.Constant) and field.args[0].value is None


# The satellite's state that outlives its app: the pin, and how its last stop
# went, which decides whether it follows a new server after a power cut.
DURABLE_STATE = ("SECRET_FILE", "CLEAN_STOP_FILE", "BOOT_FILE")


def test_the_pin_survives_an_update_trial():
    """A trial swaps app/ and configs/ and may put the previous ones back; a
    pin among them, or named like the trial's own state, goes with them. So
    does the trace of a clean stop: lost, an update would read as a power cut
    and open the window in which the speaker follows a new server."""
    updater = _constants(UPDATER)
    trial_names = {updater[name] for name in (
        "VERSION_NAME", "PAYLOAD_NAME", "TRIAL_NAME", "ATTEMPTS_NAME", "LAST_UPDATE_NAME",
    )}
    assert set(DURABLE_STATE) <= set(SATELLITE), (
        f"server_secret.py no longer declares {sorted(set(DURABLE_STATE) - set(SATELLITE))}"
    )

    for name in DURABLE_STATE:
        path = Path(SATELLITE[name])
        assert path.parent == Path(updater["STATE_DIR"]), f"{name} lives outside the state directory"
        assert path.name not in trial_names, f"{name} is named like the trial's own state"
        assert not any(path.name.startswith(f"{tree}.") for tree in updater["SYNCED_TREES"])
    assert len({Path(SATELLITE[name]).name for name in DURABLE_STATE}) == len(DURABLE_STATE)


# --------------------------------------------------------------------------- #
# The server
# --------------------------------------------------------------------------- #

def _backend_modules():
    return sorted(
        p for p in BACKEND.rglob("*.py")
        if "__pycache__" not in p.parts and "tests" not in p.parts
    )


def _url_suffix(node) -> str:
    """The path after `{CLIENT_API_PORT}` (or a name bound to it) in an f-string URL, else ''."""
    if not isinstance(node, ast.JoinedStr):
        return ""
    text = "".join(
        str(part.value) if isinstance(part, ast.Constant)
        else "{" + ast.unparse(part.value) + "}"
        for part in node.values
    )
    match = re.search(r"\{(?:self\.)?(?:CLIENT_API_PORT|satellite_api_port)\}(/[^{]*)$", text)
    return match.group(1) if match else ""


def _signed_names(scope) -> set:
    """Locals bound to `await _signed(...)` or `await <x>.headers(...)` in one function."""
    names = set()
    for node in ast.walk(scope):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Await)):
            continue
        call = node.value.value
        if not isinstance(call, ast.Call):
            continue
        fn = call.func
        if (isinstance(fn, ast.Name) and fn.id == "_signed") or (
            isinstance(fn, ast.Attribute) and fn.attr == "headers"
        ):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def _calls_to_root():
    """(module, method, path, signed) for every request the server sends to a TO_ROOT route."""
    calls = []
    for path in _backend_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module = str(path.relative_to(BACKEND))
        for scope in ast.walk(tree):
            if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            urls = {
                t.id: node.value for node in ast.walk(scope)
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.JoinedStr)
                for t in node.targets if isinstance(t, ast.Name)
            }
            signed = _signed_names(scope)
            signs_a_form = any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "sign"
                for n in ast.walk(scope)
            )
            for node in ast.walk(scope):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr.upper() in _VERBS and node.args):
                    continue
                arg = node.args[0]
                suffix = _url_suffix(urls.get(arg.id) if isinstance(arg, ast.Name) else arg)
                route = (node.func.attr.upper(), suffix)
                if route not in TO_ROOT:
                    continue
                if route == MULTIPART:
                    ok = signs_a_form and any(kw.arg == "data" for kw in node.keywords)
                else:
                    header = next((kw.value for kw in node.keywords if kw.arg == "headers"), None)
                    ok = isinstance(header, ast.Name) and header.id in signed
                calls.append((module, *route, ok))
    return calls


CALLS = _calls_to_root()


def test_the_call_extractor_sees_every_path_to_root():
    """The relays, the reboot, the push and the hand-over: a URL built another
    way would leave its call out of the check below, not fail it."""
    assert {(method, route) for _, method, route, _ in CALLS} == TO_ROOT


def test_every_request_to_a_path_to_root_carries_the_secret():
    """Sent bare, the request is refused by every satellite that pinned its
    server — which is every satellite this server ever admitted."""
    bare = [f"{module}: {method} {route}" for module, method, route, ok in CALLS if not ok]

    assert not bare, (
        f"requests to a satellite's path to root that do not carry the secret: {bare} — "
        "pass `headers=` bound to `await _signed(...)` / `await satellite_secret.headers(...)`, "
        "or `sign()` the form"
    )


def test_only_the_signers_carry_the_secret():
    """One place makes the secret and puts it on the wire: a header spelled by
    hand in a third module is a secret that can drift from the one the
    satellite pinned."""
    header, field = SERVER["SATELLITE_SECRET_HEADER"], SERVER["SATELLITE_SECRET_FIELD"]
    spelled, importers = set(), set()
    for path in _backend_modules():
        module = str(path.relative_to(BACKEND))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in (header, field):
                spelled.add(module)
            if isinstance(node, ast.ImportFrom) and node.module == "backend.core.multiroom.satellite_http":
                importers.add(module)

    assert spelled == {"core/multiroom/satellite_http.py"}
    assert importers == SIGNERS
    callers = {module for module, *_ in CALLS}
    assert callers == SIGNERS | {"core/multiroom/satellite_http.py"}
