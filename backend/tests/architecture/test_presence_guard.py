# backend/tests/architecture/test_presence_guard.py
"""Structural guardrail: every route that reaches root waits for a gesture on the unit.

Nothing on this API authenticates, and on the server the backend is root by
construction (CLAUDE.md, invariant 5), so these routes were, until this file,
root for anyone on the LAN: open SSH on the factory password, or set the sudo
password, then log in; install as root whatever GitHub serves; send the server
back to its first boot. The same two doors on a satellite, relayed by this
unit, are root on the speaker: the satellite answers its server only
(`core/multiroom/satellite_http.py`), so the relay is the way in, and it waits
for the same gesture — as does a push that has a speaker install, as root, a
release the manifest declares no digest for. Owner decision, 2026-11-13: only
the paths to root wait for a proof of presence (`core/system/presence.py`);
reading and settings stay open to the LAN.

`GUARDED` is the list, one row per guarded route, and each row is driven
through the real router: refused without the gesture — from 127.0.0.1 too,
which every process on the unit has — with nothing done to the box, then let
through once with the token its 428 carried, after the gesture. Both directions
are held from source as well: an action no row drives fails, and so does a
`require_presence` call naming an action no row lists. A route that drops its
guard fails the first test; a route guarded under a new action, with no row
here, fails the last.
"""
import ast
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import system as api_system
from backend.api.middleware import PRESENCE_WRITES, RecoveryAccessPointGuard
from backend.api.network import create_network_router
from backend.api.multiroom import create_multiroom_router
from backend.api.programs import create_programs_router
from backend.api.system import create_system_router
from backend.core.models.presence import Gesture, PresenceAction
from backend.core.multiroom.models import Client
from backend.core.multiroom.satellite_http import SatelliteSecret
from backend.core.system.presence import PRESENCE_GESTURES_HEADER
from backend.core.updates.catalog import PROGRAMS
from backend.core.updates.update import UpdateService
from backend.tests.presence_world import World

BACKEND = Path(__file__).resolve().parents[2]

BUREAU = "d8:3a:dd:68:e7:e4"
BUREAU_IP = "192.168.1.60"

# (action, method, path, body) — the request that reaches root through it.
GUARDED = [
    (PresenceAction.SSH_OPEN, "put", "/api/system/ssh", {"enabled": True}),
    (PresenceAction.PASSWORD, "post", "/api/system/password", {"password": "correct horse"}),
    (PresenceAction.PROGRAM_UPSTREAM, "post", "/api/programs/go-librespot/update", {"target": "upstream"}),
    (PresenceAction.RESET_SETUP, "post", "/api/system/reset-setup", None),
    # Over the recovery AP only, on a unit set up: the guard asks it, not the route.
    (PresenceAction.WIFI_SAVE, "post", "/api/network/wifi/save", {"ssid": "Maison", "password": "correct horse"}),
    (PresenceAction.SATELLITE_SSH_OPEN, "put", f"/api/multiroom/clients/{BUREAU}/ssh", {"enabled": True}),
    (PresenceAction.SATELLITE_PASSWORD, "post", f"/api/multiroom/clients/{BUREAU}/password",
     {"password": "correct horse"}),
    # The server on a trial past the manifest, which the fleet follows (`_Box`).
    (PresenceAction.SATELLITE_PROGRAM_UPSTREAM, "post", f"/api/programs/satellites/{BUREAU}/update", None),
    (PresenceAction.SATELLITE_PROGRAM_UPSTREAM, "post",
     f"/api/programs/satellites/{BUREAU}/update-camilladsp", None),
]

# The halves of the same routes that do not reach root, and stay open.
OPEN = [
    ("put", "/api/system/ssh", {"enabled": False}),
    ("post", "/api/programs/go-librespot/update", {"target": "validated"}),
    ("put", f"/api/multiroom/clients/{BUREAU}/ssh", {"enabled": False}),
]

# Every process on the unit has this address, and nginx names it for the kiosk.
LOOPBACK = {"client": ("127.0.0.1", 41000)}
THROUGH_NGINX = {"X-Real-IP": "127.0.0.1"}
# The paths guarded for a client of the recovery AP alone, sent from one.
FROM_THE_AP = {"/api/network/wifi/save": {"X-Real-IP": "10.42.0.5"}}


class _Box:
    """What the guarded routes do to the unit, recorded."""

    def __init__(self, tmp_path, monkeypatch):
        self.reset_marker = tmp_path / "reset-setup"
        self.password_marker = tmp_path / "password-changed"
        self.password_marker.touch()
        monkeypatch.setattr(api_system, "RESET_SETUP_MARKER", self.reset_marker)
        monkeypatch.setattr(api_system, "PASSWORD_CHANGED_MARKER", self.password_marker)

        self.spawned = []

        class _Helper:
            returncode = 0

            async def communicate(_self, payload=None):
                return b"", b""

        async def _spawn(*argv, **kwargs):
            self.spawned.append(argv)
            return _Helper()

        monkeypatch.setattr(api_system.asyncio, "create_subprocess_exec", _spawn)

        self.systemd = Mock()
        self.systemd.power = AsyncMock(return_value=True)
        self.systemd.set_enabled = AsyncMock(return_value=True)
        self.systemd.is_enabled = AsyncMock(return_value=True)
        self.systemd.is_active = AsyncMock(return_value=True)

        self.updates = MagicMock()
        self.updates.milo_update_active = False
        self.updates.milo_update_holds_installs = False
        self.updates.milo_update_state = MagicMock(return_value=None)
        self.updates.can_update_program = AsyncMock(
            return_value={"can_update": True, "available_version": "0.9.1"}
        )
        self.updates.update_program = AsyncMock(return_value={"success": True})
        # The version the fleet follows is one past the manifest — the server on
        # a trial of it — so the catalog declares no digest for its files.
        self.updates.get_latest_github_version = AsyncMock(
            return_value={"status": "success", "version": "99.0.0"}
        )
        self.updates.declared_sha256 = partial(UpdateService.declared_sha256, SimpleNamespace(programs=PROGRAMS))
        self.satellites = MagicMock()
        self.satellites.update_satellite = AsyncMock(return_value={"success": True})
        self.satellites.update_satellite_camilladsp = AsyncMock(return_value={"success": True})

        # A set-up unit with its recovery AP up.
        self.network = Mock(open_ap_reachable=True, recovery_ap_possible=True, save_network=AsyncMock())
        self.settings = Mock(get_setting=AsyncMock(return_value=True))
        # A satellite, Bureau, over its HTTP surface: its SSH state answers the
        # relay's read, and every write is recorded.
        self.satellite_state = {"login_user": "milo", "password_is_default": False}
        self.satellite_writes = []
        self.registry = Mock()
        self.registry.get_client = Mock(side_effect={
            BUREAU: Client(mac_id=BUREAU, name="Bureau", ip=BUREAU_IP, online=True),
        }.get)
        box = self

        class _Answer:
            status = 200

            def __init__(self, payload):
                self._payload = payload

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def json(self):
                return self._payload

        class _Satellite:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def get(self, url, **kwargs):
                return _Answer({"status": "success", "data": dict(box.satellite_state)})

            def _write(self, verb, url, **kwargs):
                box.satellite_writes.append((verb, url.split(":8001", 1)[1]))
                # The hand-over each relay sends first: it holds this server's secret.
                return _Answer({"status": "success", "data": {"changed": False}})

            def put(self, url, **kwargs):
                return self._write("PUT", url, **kwargs)

            def post(self, url, **kwargs):
                return self._write("POST", url, **kwargs)

        monkeypatch.setattr("backend.api.multiroom.aiohttp.ClientSession", _Satellite)
        self.satellite_secret = SatelliteSecret(key_file=tmp_path / "satellite-key")

    def touched(self, action) -> bool:
        return {
            PresenceAction.SSH_OPEN: lambda: self.systemd.set_enabled.await_count > 0,
            PresenceAction.PASSWORD: lambda: bool(self.spawned),
            # The check before it only reads: the install is what reaches root.
            PresenceAction.PROGRAM_UPSTREAM: lambda: self.updates.update_program.await_count > 0,
            PresenceAction.RESET_SETUP: lambda: (
                self.reset_marker.exists() or self.systemd.power.await_count > 0
            ),
            PresenceAction.WIFI_SAVE: lambda: self.network.save_network.await_count > 0,
            # The read of its state before is open: the write is what reaches root.
            PresenceAction.SATELLITE_SSH_OPEN: lambda: ("PUT", "/api/system/ssh") in self.satellite_writes,
            PresenceAction.SATELLITE_PASSWORD: lambda: ("POST", "/api/system/password") in self.satellite_writes,
            # One row per push, each on a box of its own: either is the row's.
            PresenceAction.SATELLITE_PROGRAM_UPSTREAM: lambda: (
                self.satellites.update_satellite.await_count
                + self.satellites.update_satellite_camilladsp.await_count > 0
            ),
        }[action]()


@pytest.fixture
def unit(tmp_path, monkeypatch):
    yield from _unit(tmp_path, monkeypatch, rotary=True)


@pytest.fixture
def bare_unit(tmp_path, monkeypatch):
    """Neither an encoder nor a Pi 5: the plug is the gesture."""
    yield from _unit(tmp_path, monkeypatch)


def _unit(tmp_path, monkeypatch, **board):
    box = _Box(tmp_path, monkeypatch)
    world = World(tmp_path, monkeypatch, **board)
    presence = world.service()
    state_machine = Mock()
    state_machine.broadcast = AsyncMock()

    app = FastAPI()
    app.include_router(
        create_system_router(box.systemd, update_service=box.updates, presence_service=presence),
        prefix="/api/system",
    )
    app.include_router(
        create_programs_router(box.updates, box.satellites, state_machine, presence_service=presence)
    )
    app.include_router(create_network_router(box.network))
    app.add_middleware(
        RecoveryAccessPointGuard,
        network_service=box.network, settings_service=box.settings, presence_service=presence,
    )
    app.include_router(create_multiroom_router(
        box.registry, presence_service=presence, satellite_secret=box.satellite_secret,
    ))
    with TestClient(app, **LOOPBACK) as client:
        client.portal.call(presence.initialize)
        yield box, presence, client


def _send(client, method, path, body, headers=None):
    return getattr(client, method)(
        path, **({"json": body} if body is not None else {}),
        headers={**THROUGH_NGINX, **FROM_THE_AP.get(path, {}), **(headers or {})},
    )


@pytest.mark.parametrize("action,method,path,body", GUARDED, ids=[row[0].value for row in GUARDED])
def test_without_the_gesture_nothing_reaches_root(unit, action, method, path, body):
    """A 200 here is root for anyone on the LAN — or for any process on the
    unit, since the request comes from 127.0.0.1 through nginx."""
    box, presence, client = unit

    response = _send(client, method, path, body)

    assert response.status_code == 428, response.text
    challenge = response.json()["detail"]["challenge"]
    assert (challenge["action"], challenge["status"]) == (action.value, "pending")
    assert not box.touched(action)


@pytest.mark.parametrize("action,method,path,body", GUARDED, ids=[row[0].value for row in GUARDED])
def test_the_gesture_lets_that_request_through_once(unit, action, method, path, body):
    box, presence, client = unit
    refused = _send(client, method, path, body).json()["detail"]
    answer = {refused["header"]: refused["token"]}

    client.portal.call(presence.on_gesture, Gesture.ROTARY)
    response = _send(client, method, path, body, answer)

    assert response.status_code == 200, response.text
    assert box.touched(action)
    assert _send(client, method, path, body, answer).status_code == 428, "a proof is spent once"


@pytest.mark.parametrize("method,path,body", OPEN, ids=[row[1] + str(row[2]) for row in OPEN])
def test_the_halves_that_do_not_reach_root_stay_open(unit, method, path, body):
    """Closing SSH and returning to the manifest's pin must never wait for a
    gesture: the decision guards the paths to root, nothing else."""
    _, presence, client = unit

    response = _send(client, method, path, body)

    assert response.status_code == 200, response.text
    assert presence.view() is None


def test_ssh_is_not_opened_on_the_factory_password(unit):
    """Same value on every unit, published with the source: a door opened on
    it is open to whoever reads the repository. Refused before any challenge,
    so the owner is never asked for a gesture the route then refuses."""
    box, presence, client = unit
    box.password_marker.unlink()

    response = _send(client, "put", "/api/system/ssh", {"enabled": True})

    assert response.status_code == 409
    assert presence.view() is None
    box.systemd.set_enabled.assert_not_awaited()


# Each request a route refuses whatever the gesture, and how the unit gets there.
REFUSED_ANYWAY = {
    "ssh on the factory password": (
        "put", "/api/system/ssh", {"enabled": True},
        lambda box: box.password_marker.unlink(),
    ),
    "upstream with no such release": (
        "post", "/api/programs/go-librespot/update", {"target": "upstream"},
        lambda box: setattr(box.updates.can_update_program, "return_value", {"can_update": False}),
    ),
    "reset during a Milō update": (
        "post", "/api/system/reset-setup", None,
        lambda box: setattr(box.updates, "milo_update_active", True),
    ),
    "ssh on a satellite's factory password": (
        "put", f"/api/multiroom/clients/{BUREAU}/ssh", {"enabled": True},
        lambda box: box.satellite_state.update(password_is_default=True),
    ),
    "password on a satellite with no milo account": (
        "post", f"/api/multiroom/clients/{BUREAU}/password", {"password": "correct horse"},
        lambda box: box.satellite_state.update(login_user=None),
    ),
    "a satellite push with no version to name": (
        "post", f"/api/programs/satellites/{BUREAU}/update", None,
        lambda box: setattr(box.updates.get_latest_github_version, "return_value",
                            {"status": "error", "message": "API rate limit exceeded"}),
    ),
    "a satellite push during a Milō update": (
        "post", f"/api/programs/satellites/{BUREAU}/update-camilladsp", None,
        lambda box: setattr(box.updates, "milo_update_holds_installs", True),
    ),
}


@pytest.mark.parametrize("case", list(REFUSED_ANYWAY))
def test_a_request_the_route_refuses_anyway_asks_for_no_gesture(unit, case):
    """Asked to turn the knob or pull the plug for a request that is then
    refused, the owner has made the gesture for nothing — and on a power cut,
    rebooted the unit for nothing."""
    box, presence, client = unit
    method, path, body, arrange = REFUSED_ANYWAY[case]
    arrange(box)

    response = _send(client, method, path, body)

    assert response.status_code != 428, response.text
    assert presence.view() is None


def test_an_upstream_install_another_update_holds_asks_for_no_gesture(unit):
    box, presence, client = unit
    box.updates.milo_update_holds_installs = True

    response = _send(client, "post", "/api/programs/go-librespot/update", {"target": "upstream"})

    assert response.status_code == 409, response.text
    assert presence.view() is None


def test_only_the_request_that_asked_ends_its_challenge(unit):
    """The dialog's Cancel, through the same path the challenge is read at."""
    _, presence, client = unit
    refused = _send(client, "put", "/api/system/ssh", {"enabled": True}).json()["detail"]

    assert client.delete("/api/system/presence", headers={refused["header"]: "not-the-token"}).status_code == 404
    assert client.get("/api/system/presence").json()["data"]["challenge"]["id"] == refused["challenge"]["id"]

    assert client.delete("/api/system/presence", headers={refused["header"]: refused["token"]}).status_code == 200
    assert client.get("/api/system/presence").json()["data"]["challenge"] is None


def test_a_phone_on_the_ap_ends_the_challenge_its_save_opened(unit):
    """The dialog's Cancel, from the phone that asked over the recovery AP.
    Refused there, the unit stayed armed after the phone gave up: the knob
    swallowed as the gesture, a Pi 5's power key held, and a power cut proving
    a challenge nobody would spend."""
    _, presence, client = unit
    save = next(row for row in GUARDED if row[0] is PresenceAction.WIFI_SAVE)
    refused = _send(client, *save[1:]).json()["detail"]
    from_the_ap = FROM_THE_AP[save[2]]

    stranger = client.delete("/api/system/presence", headers={**from_the_ap, refused["header"]: "not-the-token"})
    assert stranger.status_code == 404, stranger.text
    assert client.get("/api/system/presence").json()["data"]["challenge"]["id"] == refused["challenge"]["id"]

    own = client.delete("/api/system/presence", headers={**from_the_ap, refused["header"]: refused["token"]})
    assert own.status_code == 200, own.text
    assert presence.view() is None


def test_the_units_own_screen_is_sent_elsewhere_for_a_power_cut(bare_unit):
    """The screen goes down with the unit: what it asks is answered at once,
    with nothing opened, so the phone it sends the owner to is not refused."""
    _, presence, client = bare_unit
    on_the_unit = {PRESENCE_GESTURES_HEADER: "rotary, power_button"}

    response = _send(client, "put", "/api/system/ssh", {"enabled": True}, on_the_unit)

    assert response.status_code == 428, response.text
    assert response.json()["detail"]["gesture"] == "power_cycle"
    assert "token" not in response.json()["detail"]
    assert presence.view() is None
    from_a_phone = _send(client, "put", "/api/system/ssh", {"enabled": True})
    assert from_a_phone.status_code == 428
    assert from_a_phone.json()["detail"]["challenge"]["gesture"] == "power_cycle"


def test_a_gesture_the_unit_does_not_know_is_refused(unit):
    """Read as no gesture at all, it would send the screen elsewhere with no
    word of why."""
    _, presence, client = unit

    response = _send(client, "put", "/api/system/ssh", {"enabled": True}, {PRESENCE_GESTURES_HEADER: "knob"})

    assert response.status_code == 400
    assert presence.view() is None


def _required_actions() -> set[str]:
    """The `PresenceAction` members named in `require_presence(...)` calls under backend/,
    and the ones the recovery AP guard asks from its table (`PRESENCE_WRITES`)."""
    named = set()
    files = [p for p in BACKEND.rglob("*.py") if "tests" not in p.relative_to(BACKEND).parts]
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "require_presence"
            ):
                for arg in ast.walk(node):
                    if (
                        isinstance(arg, ast.Attribute)
                        and isinstance(arg.value, ast.Name)
                        and arg.value.id == "PresenceAction"
                    ):
                        named.add(arg.attr)
    named |= {action.name for action in PRESENCE_WRITES.values()}
    assert len(named) >= 4, f"only {sorted(named)} found guarded — the extractor is broken"
    return named


def test_every_guard_in_the_code_is_a_row_here_and_every_row_a_guard():
    """A route guarded under an action no row drives is a guard nobody checks;
    an action no route requires is a row that proves nothing."""
    rows = {row[0].name for row in GUARDED}

    assert rows == {member.name for member in PresenceAction}
    assert _required_actions() == rows
