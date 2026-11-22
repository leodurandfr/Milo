# backend/tests/architecture/test_recovery_ap_guard.py
"""Every write the app serves, sent by a client of the recovery access point.

The AP is open, so its clients are anyone in radio range, and
`api/middleware.py::RecoveryAccessPointGuard` is the only thing between them and
the API's writes. It refuses by default, which is what this file holds against
the whole route table rather than a sample: before setup, the wizard's and the
adoption's writes reach their endpoint and nothing else does; once set up,
only saving a WiFi network does, and only from the unit's own AP. A route added
tomorrow is refused without anyone listing it.

Both directions of that one door are held: from a speaker's hotspot the save
never reaches its endpoint, and a server with no screen and no Ethernet whose
home network changed has, from its own AP, its one repair.

The lists themselves are held the other way: every entry names a route the app
serves, under the one spelling the guard compares, and one that its caller
actually sends — an entry that outlived its caller is a write handed to the
neighbors for nothing. And the wizard's writes are held against the list: each
one it can send (read from its components and the functions of theirs they
name, never by substring) is listed, or kept off the AP by a named condition —
one it sends and the list lacks stops a WiFi-only unit's first setup at a 403.

Never through the live endpoints: this checkout is the appliance, and a guard
that let `POST /api/system/restart` through would reboot it. The installed guard
class wraps a sentinel that records what reached it.
"""
import json
import re
from pathlib import Path

import pytest

from backend.api.middleware import (
    PRE_SETUP_WRITES,
    SET_UP_WRITES,
    RecoveryAccessPointGuard,
)
from backend.config.constants import RECOVERY_AP_ADDRESS

REPO_ROOT = Path(__file__).resolve().parents[3]

READS = {"GET", "HEAD", "OPTIONS"}

# Where a pre-setup write may come from: the wizard (read from its entry, below),
# and the adopting server's request to a fresh unit.
WIZARD_ENTRY = REPO_ROOT / "frontend/src/components/setup/SetupWizard.vue"
ADOPTION = REPO_ROOT / "backend/core/multiroom/wifi_adoption.py"

# Writes the wizard sends that a client of the AP never does, each with the
# condition that keeps it off the AP. Every other write the wizard sends must be
# in PRE_SETUP_WRITES, or the first setup of a WiFi-only unit is refused (403)
# over the AP, which no CI run and no unit on Ethernet sees.
OFF_AP_WIZARD_WRITES = {
    ("POST", "/api/network/wifi/connect"):
        "NetworkStep passes `:action=\"hotspotActive ? 'save' : 'connect'\"` to NetworkSelector,"
        " whose button and Enter key both go through `act()`: over the AP it saves",
}

# Where a write admitted once set up comes from: the frontend.
FRONTEND = REPO_ROOT / "frontend/src"

AP_CLIENT = RECOVERY_AP_ADDRESS.rsplit(".", 1)[0] + ".5"


def _walk(router, prefix=""):
    """(path, route) for every route, prefixes resolved.

    `app.routes` is not flat since FastAPI 0.137: each `include_router()` is one
    `_IncludedRouter` holding the router it wraps. OpenAPI is flat but leaves out
    `include_in_schema=False`, and a write it hid would be one this file never sent.
    """
    for route in getattr(router, "routes", []):
        if type(route).__name__ == "_IncludedRouter":
            inner = getattr(route.include_context, "prefix", "") or ""
            yield from _walk(route.original_router, prefix + inner)
        else:
            yield prefix + route.path, route


def _writes():
    from backend.main import app

    writes = sorted(
        (method, path)
        for path, route in _walk(app)
        for method in (getattr(route, "methods", None) or ())
        if method not in READS
    )
    assert len(writes) > 100, f"only {len(writes)} writes found — route walk broken?"
    return writes


WRITES = _writes()


def _installed_guard(setup_completed, own_ap=True):
    """The guard class main.py installs, as a wrapper, on a unit whose AP is up —
    or, `own_ap=False`, held by an adoption on a speaker's open hotspot."""
    from backend.main import app as main_app

    entry = next(m for m in main_app.user_middleware if m.cls is RecoveryAccessPointGuard)

    class Network:
        open_ap_reachable = True
        recovery_ap_possible = own_ap

    class Settings:
        async def get_setting(self, key):
            return {"setup_completed": setup_completed}[key]

    return lambda app: entry.cls(app, network_service=Network(), settings_service=Settings())


def _concrete(template):
    return re.sub(r"\{[^}]+\}", "x", template)


async def _exchange(guard, method, path, scope_type="http", headers=None, real_ip=AP_CLIENT):
    """(The status the guard answers, or "reached" when it handed the request on;
    the body it answered, parsed, or None.)

    Sent as nginx sends it: from loopback, the client named in X-Real-IP.
    """
    reached = []
    statuses = []
    body = []

    async def sentinel(scope, receive, send):
        reached.append(scope["path"])

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            statuses.append(message["status"])
        elif message["type"] == "http.response.body":
            body.append(message.get("body", b""))

    scope = {
        "type": scope_type,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (b"x-real-ip", real_ip.encode()),
            *((k.lower().encode(), v.encode()) for k, v in (headers or {}).items()),
        ],
        "client": ("127.0.0.1", 41000),
    }
    if scope_type == "http":
        scope["method"] = method
    await guard(sentinel)(scope, receive, send)
    if reached:
        return "reached", None
    raw = b"".join(body)
    return statuses[0], json.loads(raw) if raw else None


async def _send(guard, method, path, scope_type="http", headers=None, real_ip=AP_CLIENT):
    """The status the guard answers, or "reached" when it handed the request on."""
    return (await _exchange(guard, method, path, scope_type, headers, real_ip))[0]


@pytest.mark.parametrize("setup_completed", [False, True], ids=["first-boot", "set-up"])
async def test_every_write_but_the_wizards_is_refused_to_a_client_of_the_ap(setup_completed):
    """A route reached here is a write anyone in radio range of an outage can send:
    WiFi pointed at their network, SSH opened, the unit rebooted or updated."""
    guard = _installed_guard(setup_completed)
    wrong = []
    for method, template in WRITES:
        if not setup_completed and (method, template) in PRE_SETUP_WRITES:
            expected = "reached"
        elif setup_completed and (method, template) in SET_UP_WRITES:
            expected = "reached"
        else:
            expected = 403
        answer = await _send(guard, method, _concrete(template))
        if answer != expected:
            wrong.append((method, template, answer))

    assert wrong == []


async def test_a_speakers_hotspot_saves_nothing():
    """While an adoption holds this unit on a speaker's open hotspot, a 10.42.0.x
    client is a stranger on that hotspot: the network saved would be theirs,
    under the home name, and `save_network` deletes that name's other profiles."""
    guard = _installed_guard(setup_completed=True, own_ap=False)
    for method, path in SET_UP_WRITES:
        assert await _send(guard, method, path) == 403, (method, path)


async def test_the_kiosk_and_the_lan_save_a_wifi_on_a_speakers_hotspot_too():
    """The guard is the AP's clients' alone: on the unit's own screen and on the
    home network, saving a WiFi network is a setting like any other."""
    guard = _installed_guard(setup_completed=True, own_ap=False)
    for method, path in SET_UP_WRITES:
        for real_ip in ("127.0.0.1", "192.168.1.20"):
            assert await _send(guard, method, path, real_ip=real_ip) == "reached", real_ip


@pytest.mark.parametrize(
    "writes", [PRE_SETUP_WRITES, SET_UP_WRITES], ids=["pre-setup", "set-up"],
)
def test_every_listed_write_is_one_route_spelled_as_the_router_matches_it(writes):
    """The guard compares the decoded path verbatim, which is the router's own
    match only for a path with no parameter; and an entry naming no route is
    either a typo or a write renamed under its caller, now refused to it."""
    assert writes, "an empty list proves nothing"
    for method, path in writes:
        assert "{" not in path, f"{method} {path}: a parameter defeats the verbatim match"
        assert WRITES.count((method, path)) == 1, f"{method} {path} is not one route"


_IMPORT = re.compile(r"""^\s*import\s+(?:[\w{}\s,*]+?\s+from\s+)?['"]([^'"]+)['"]""", re.M)
_WRITE = re.compile(
    r"""(?:apiCall\.(post|put|patch|delete)\(\s*|\.request\(\s*['"](post|put|patch|delete)['"]\s*,\s*)"""
    r"""[`'"]([^`'"]+)[`'"]"""
)
_FUNCTION = re.compile(r"(?:function\s+(\w+)\s*(?=\()|(\w+)\s*=\s*(?:async\s*)?(?=\())")


def _resolve(spec, importer):
    """The file an import names inside frontend/src, or None (a package)."""
    if spec.startswith("@/"):
        path = FRONTEND / spec[2:]
    elif spec.startswith("."):
        path = (importer.parent / spec).resolve()
    else:
        return None
    if not path.suffix:
        path = path.with_suffix(".js")
    return path if path.is_file() else None


def _writes_in(text):
    return {((verb or request_verb).upper(), path) for verb, request_verb, path in _WRITE.findall(text)}


def _bodies(text):
    """{function name: its own body}, every function the module declares, nested
    ones included (a store's are inside `defineStore`, a composable's inside
    `useNetwork()`) — and each without the functions nested in it, or the
    composable that holds them all would write everything they write."""
    spans = {}
    for match in _FUNCTION.finditer(text):
        # Past the parameters, which may hold braces of their own (`{ errorRef } = {}`).
        depth, i = 0, match.end()
        for i in range(match.end(), len(text)):
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            if depth == 0:
                break
        head = re.match(r"\s*(?:=>\s*)?\{", text[i + 1:])
        if head is None:  # a call (`x = useFoo()`), or an arrow with no block
            continue
        start = i + head.end()
        depth = 0
        for i in range(start, len(text)):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                spans[match[1] or match[2]] = (match.start(), i + 1)
                break
    bodies = {}
    for name, (start, end) in spans.items():
        inner = sorted((a, b) for other, (a, b) in spans.items() if other != name and start < a and b <= end)
        own, at = [], start
        for a, b in inner:
            if a >= at:
                own.append(text[at:a])
                at = b
        own.append(text[at:end])
        bodies[name] = "".join(own)
    return bodies


def _wizard_writes():
    """(method, path) of every write the wizard can send: written in a component
    it renders (SetupWizard and down, through its imports), or in a function of a
    module they import that one of them names — never every write such a module
    holds: useNetwork is the Settings page's too, and a write only Settings calls
    is none of the wizard's.

    One hop only: a module's functions are followed within that module, never
    into a module it imports. A wizard-named function that hands its write to
    another module's function (a composable calling a store action) is not
    traced, so such a write must be sent from the module the wizard
    imports, or this extractor taught to follow it."""
    components, modules, todo = {}, set(), [WIZARD_ENTRY]
    while todo:
        component = todo.pop()
        if component in components:
            continue
        components[component] = component.read_text(encoding="utf-8")
        for spec in _IMPORT.findall(components[component]):
            path = _resolve(spec, component)
            if path is None or path.name == "apiCall.js":
                continue
            (todo.append(path) if path.suffix == ".vue" else modules.add(path))
    assert len(components) > 8 and modules, f"wizard tree not found: {sorted(c.name for c in components)}"

    sent = set().union(*(_writes_in(text) for text in components.values()))
    named = "\n".join(components.values())
    for module in modules:
        bodies = _bodies(module.read_text(encoding="utf-8"))
        writes = {name: _writes_in(body) for name, body in bodies.items()}
        # A function that calls one of its module's writing functions writes too.
        changed = True
        while changed:
            changed = False
            for name, body in bodies.items():
                for other, other_writes in writes.items():
                    if other != name and other_writes - writes[name] and re.search(rf"\b{other}\(", body):
                        writes[name] |= other_writes
                        changed = True
        for name, function_writes in writes.items():
            if function_writes and re.search(rf"\b{name}\b", named):
                sent |= function_writes
    return sent


WIZARD_WRITES = _wizard_writes()


def test_every_write_the_wizard_sends_is_one_the_ap_admits_before_setup():
    """A write added to the wizard and missing from PRE_SETUP_WRITES is refused
    (403) to every phone on the AP: the first setup of a WiFi-only unit stops
    there, and nothing in CI or on a unit set up over Ethernet ever sees it."""
    assert {("POST", "/api/setup/complete"), ("POST", "/api/network/wifi/save")} <= WIZARD_WRITES, (
        f"the extractor reads no wizard write: {sorted(WIZARD_WRITES)}"
    )
    for write in OFF_AP_WIZARD_WRITES:
        assert write in WIZARD_WRITES, f"{write} is no longer sent by the wizard: drop it from OFF_AP_WIZARD_WRITES"

    refused = WIZARD_WRITES - PRE_SETUP_WRITES - OFF_AP_WIZARD_WRITES.keys()
    assert refused == set(), f"the wizard sends writes the AP refuses before setup: {sorted(refused)}"


def test_every_pre_setup_write_is_one_the_wizard_or_the_adoption_sends():
    """An entry with no caller is a write handed to the AP's neighbors for nothing."""
    adoption = ADOPTION.read_text()
    for method, path in PRE_SETUP_WRITES:
        sent = (method, path) in WIZARD_WRITES or path in adoption
        assert sent, f"{method} {path} has no caller left: drop it from PRE_SETUP_WRITES"


def _frontend_sources():
    sources = {
        f: f.read_text(encoding="utf-8")
        for f in sorted(FRONTEND.rglob("*")) if f.suffix in (".js", ".vue")
    }
    assert len(sources) > 100, f"only {len(sources)} frontend sources found"
    return sources


def _quoted(path):
    return rf"""['"`]{re.escape(path)}['"`]"""


def test_every_set_up_write_has_a_caller():
    """An entry no page sends is a write handed to the AP's neighbors for nothing."""
    sources = _frontend_sources()

    for method, path in SET_UP_WRITES:
        sent = re.compile(rf"""apiCall\.{method.lower()}\(\s*{_quoted(path)}""")
        callers = [f.name for f, text in sources.items() if sent.search(text)]
        assert callers, f"{method} {path} has no caller left: drop it from SET_UP_WRITES"


@pytest.mark.parametrize("spell", [
    pytest.param(lambda p: p + "/", id="trailing-slash"),
    pytest.param(lambda p: "/" + p, id="doubled-slash"),
    pytest.param(str.upper, id="upper-case"),
    pytest.param(lambda p: p.replace("/", "//", 2), id="inner-doubled-slash"),
])
async def test_another_spelling_of_a_listed_write_is_refused(spell):
    """Starlette may redirect or 404 these, and a redirect the client follows lands
    on the exact spelling the guard judges again; admitted as spelled, a path
    the router normalizes some day would be a write let through unjudged."""
    guard = _installed_guard(setup_completed=False)
    for method, path in PRE_SETUP_WRITES:
        assert await _send(guard, method, spell(path)) == 403, (method, spell(path))
    guard = _installed_guard(setup_completed=True)
    for method, path in SET_UP_WRITES:
        assert await _send(guard, method, spell(path)) == 403, (method, spell(path))


@pytest.mark.parametrize("method", sorted(READS))
async def test_a_client_of_the_ap_still_reads_on_a_unit_set_up(method):
    guard = _installed_guard(setup_completed=True)
    assert await _send(guard, method, "/api/system/ssh") == "reached"


async def test_the_socket_is_not_judged():
    """`/ws` takes no command: `ws/manager.py` discards every frame after `ready`.
    The setup page and the repair screen live on it."""
    guard = _installed_guard(setup_completed=True)
    assert await _send(guard, None, "/ws", scope_type="websocket") == "reached"
