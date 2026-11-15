# backend/tests/architecture/test_recovery_ap_guard.py
"""Every write the app serves, sent by a client of the recovery access point.

The AP is open, so its clients are anyone in radio range, and
`api/middleware.py::RecoveryAccessPointGuard` is the only thing between them and
the API's writes. It refuses by default, which is what this file holds against
the whole route table rather than a sample: before setup, the wizard's and the
adoption's writes reach their endpoint and nothing else does; once set up,
nothing does. A route added tomorrow is refused without anyone listing it.

The list itself is held the other way: every entry names a route the app
serves, under the one spelling the guard compares, and one that the wizard or
the adoption actually sends — an entry that outlived its caller is a write
handed to the neighbors for nothing.

Never through the live endpoints: this checkout is the appliance, and a guard
that let `POST /api/system/restart` through would reboot it. The installed guard
class wraps a sentinel that records what reached it.
"""
import re
from pathlib import Path

import pytest

from backend.api.middleware import PRE_SETUP_WRITES, RecoveryAccessPointGuard
from backend.config.constants import RECOVERY_AP_ADDRESS

REPO_ROOT = Path(__file__).resolve().parents[3]

READS = {"GET", "HEAD", "OPTIONS"}

# Where a pre-setup write may come from: the wizard's screens and the network
# composable they call, and the adopting server's request to a fresh unit.
CALLERS = (
    *sorted((REPO_ROOT / "frontend/src/components/setup").glob("*.vue")),
    REPO_ROOT / "frontend/src/composables/useNetwork.js",
    REPO_ROOT / "backend/core/multiroom/wifi_adoption.py",
)

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


def _installed_guard(setup_completed):
    """The guard class main.py installs, on a unit whose AP is up, as a wrapper."""
    from backend.main import app as main_app

    entry = next(m for m in main_app.user_middleware if m.cls is RecoveryAccessPointGuard)

    class Network:
        open_ap_reachable = True

    class Settings:
        async def get_setting(self, key):
            return {"setup_completed": setup_completed}[key]

    return lambda app: entry.cls(app, network_service=Network(), settings_service=Settings())


def _concrete(template):
    return re.sub(r"\{[^}]+\}", "x", template)


async def _send(guard, method, path, scope_type="http"):
    """The status the guard answers, or "reached" when it handed the request on.

    Sent as nginx sends it: from loopback, the AP client named in X-Real-IP.
    """
    reached = []
    statuses = []

    async def sentinel(scope, receive, send):
        reached.append(scope["path"])

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            statuses.append(message["status"])

    scope = {
        "type": scope_type,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"x-real-ip", AP_CLIENT.encode())],
        "client": ("127.0.0.1", 41000),
    }
    if scope_type == "http":
        scope["method"] = method
    await guard(sentinel)(scope, receive, send)
    return "reached" if reached else statuses[0]


@pytest.mark.parametrize("setup_completed", [False, True], ids=["first-boot", "set-up"])
async def test_every_write_but_the_wizards_is_refused_to_a_client_of_the_ap(setup_completed):
    """A route reached here is a write anyone in radio range of an outage can send:
    WiFi pointed at their network, SSH opened, the unit rebooted or updated."""
    guard = _installed_guard(setup_completed)
    wrong = []
    for method, template in WRITES:
        admitted = not setup_completed and (method, template) in PRE_SETUP_WRITES
        answer = await _send(guard, method, _concrete(template))
        if answer != ("reached" if admitted else 403):
            wrong.append((method, template, answer))

    assert wrong == []


def test_every_pre_setup_write_is_one_route_spelled_as_the_router_matches_it():
    """The guard compares the decoded path verbatim, which is the router's own
    match only for a path with no parameter; and an entry naming no route is
    either a typo or a write renamed under the wizard, now refused to it."""
    for method, path in PRE_SETUP_WRITES:
        assert "{" not in path, f"{method} {path}: a parameter defeats the verbatim match"
        assert WRITES.count((method, path)) == 1, f"{method} {path} is not one route"


def test_every_pre_setup_write_is_one_the_wizard_or_the_adoption_sends():
    sources = {f: f.read_text() for f in CALLERS}
    assert len(sources) > 3 and all(sources.values()), "wizard sources not found"

    for method, path in PRE_SETUP_WRITES:
        callers = [f.name for f, text in sources.items() if path in text]
        assert callers, f"{method} {path} has no caller left: drop it from PRE_SETUP_WRITES"


@pytest.mark.parametrize("spell", [
    pytest.param(lambda p: p + "/", id="trailing-slash"),
    pytest.param(lambda p: "/" + p, id="doubled-slash"),
    pytest.param(str.upper, id="upper-case"),
    pytest.param(lambda p: p.replace("/", "//", 2), id="inner-doubled-slash"),
])
async def test_another_spelling_of_a_pre_setup_write_is_refused(spell):
    """Starlette may redirect or 404 these, and a redirect the client follows lands
    on the exact spelling the guard judges again; admitted as spelled, a path
    the router normalizes some day would be a write let through unjudged."""
    guard = _installed_guard(setup_completed=False)
    for method, path in PRE_SETUP_WRITES:
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
