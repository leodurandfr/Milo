# backend/tests/test_qobuz_adapter.py
"""The gate that decides whether a qobuz-proxy release can be shipped.

`rootfs/usr/local/bin/milo-qobuz` adds position/duration and the session's
state to the sidecar's status, holds its stream at unity gain, tells the app
when its slider is inert, puts the web UI behind the backend's request gate
and lets one login run at a time — things upstream offers no setting for.
They bind to names, and `--check` is what refuses a release that moved one,
before `_update_qobuz_proxy` restarts the service onto it. qobuz-proxy is not in
the backend's environment, so `fake_qobuz_proxy` stands in for it, in the shape
the bindings read: the check, the adaptation and the hand-over run against it,
and the assertions are what they did to it.

What breaks when this fails: the check answers "moved" for a release that is
fine (every qobuz-proxy update refuses, which is the bug that replaced the
source patches it succeeded), or answers "fine" for one that is not (the sidecar
starts, plays, and reports a progress bar that never moves — no error anywhere).
The adaptation failing is the same silence on the unit: a stream at upstream's
50 % before CamillaDSP, or an app slider that attenuates when the setting says
it must not.
"""
import ast
import asyncio
import configparser
import enum
import inspect
import sys
import textwrap
import types
from pathlib import Path

import pytest

ADAPTER = Path(__file__).resolve().parents[2] / "rootfs" / "usr" / "local" / "bin" / "milo-qobuz"


@pytest.fixture(scope="module")
def adapter():
    """The launcher, executed into a module namespace.

    Compiled here rather than imported through a file loader: a loader writes
    `__pycache__` beside the script, inside the tree `sync-system-files` copies
    to the fleet — which is how a .pyc came to be deployed to every unit until
    `test_rootfs_deployment` refused it.
    """
    module = types.ModuleType("milo_qobuz")
    module.__file__ = str(ADAPTER)
    exec(compile(ADAPTER.read_text(encoding="utf-8"), str(ADAPTER), "exec"), module.__dict__)
    return module


def test_the_adapter_is_the_one_the_unit_runs(adapter):
    """Guards the rules below: a stub with no bindings would pass them all."""
    assert ADAPTER.is_file(), "the launcher moved — the unit's ExecStart points at this path"
    assert callable(adapter._mentions_key)
    assert callable(adapter.check)


def test_a_key_parked_in_a_const_tuple_is_found(adapter):
    """Constant dict keys compile to one tuple in `co_consts`, not to loose strings.

    A flat membership test misses every one of them, so the check reports the
    key as renamed on a release that never touched it — measured on 1.5.0, which
    builds `now_playing` and was reported broken.
    """
    def builds_status():
        return {"id": 1, "now_playing": None, "config": {}}

    assert adapter._mentions_key(builds_status, "now_playing") is True
    assert adapter._mentions_key(builds_status, "position_ms") is False


def test_a_key_assembled_at_runtime_is_not_seen(adapter):
    """The limit of the reading, stated: only literals are constants.

    A release that computed its keys would read as having dropped every one of
    them — the check would refuse it, which is the safe direction, but this is
    why it says "re-derive" rather than naming a rename.
    """
    def builds_from_argument(key):
        return {key: None}

    assert adapter._mentions_key(builds_from_argument, "now_playing") is False


@pytest.mark.parametrize("flag,expected", [("1", True), ("0", False), ("", False), (None, False)])
def test_the_app_slider_is_honoured_only_behind_the_flag(adapter, tmp_path, monkeypatch, flag, expected):
    """CamillaDSP is the only attenuation stage unless the setting says otherwise.

    A missing flag file is the normal state — the setting defaults to off — so
    it must read as "not allowed" rather than raise into the audio callback.
    """
    monkeypatch.setenv("QOBUZPROXY_DATA_DIR", str(tmp_path))
    if flag is not None:
        (tmp_path / adapter.VOLUME_FLAG).write_text(flag)

    assert adapter._app_volume_allowed() is expected


@pytest.mark.parametrize("flag,upstream,expected", [
    ("1", True, True), ("0", True, False), (None, True, False), ("1", False, False),
])
def test_the_app_is_offered_the_slider_only_behind_the_flag(adapter, tmp_path, monkeypatch,
                                                           flag, upstream, expected):
    """JOIN_SESSION is where the app learns whether its slider does anything.

    Locked, the app must be told the volume is the device's, or it shows a
    slider the stream ignores; and the flag never re-grants a control upstream
    itself withheld (a fixed-volume DLNA renderer).
    """
    monkeypatch.setenv("QOBUZPROXY_DATA_DIR", str(tmp_path))
    if flag is not None:
        (tmp_path / adapter.VOLUME_FLAG).write_text(flag)

    assert adapter.advertised_volume_control(upstream) is expected


def test_the_join_carries_the_policy_and_keeps_upstream_s_refusal(adapter, tmp_path,
                                                                   monkeypatch):
    """The wrapper is what reaches the wire: with the flag on, a fixed-volume
    renderer's False from upstream must survive, and with it off every join
    says the volume is the device's."""
    monkeypatch.setenv("QOBUZPROXY_DATA_DIR", str(tmp_path))
    sent = []

    def encode_join_session(self, device_uuid, *, supports_volume_control=True, is_active=False):
        sent.append(supports_volume_control)
        return b"frame"

    encode = adapter.with_volume_policy(encode_join_session)
    (tmp_path / adapter.VOLUME_FLAG).write_text("1")
    assert encode(None, "uuid", supports_volume_control=True, is_active=True) == b"frame"
    encode(None, "uuid", supports_volume_control=False)
    (tmp_path / adapter.VOLUME_FLAG).write_text("0")
    encode(None, "uuid", supports_volume_control=True)

    assert sent == [True, False, False]


def test_a_positional_volume_argument_is_refused_by_the_check(adapter):
    """The wrapper sets the value by keyword; a release taking it positionally
    would fail every join with "multiple values", so the check must say so."""
    def keyword_only(self, *, supports_volume_control=True): ...
    def positional(self, supports_volume_control=True): ...

    assert adapter._keyword_only(keyword_only, "supports_volume_control") is True
    assert adapter._keyword_only(positional, "supports_volume_control") is False
    assert adapter._keyword_only(None, "supports_volume_control") is False


class _State:
    def __init__(self, name):
        self.name = name


class _Player:
    def __init__(self, state):
        self.state = _State(state)


class _WsManager:
    """The upstream shape: `is_renderer_active` also requires a connected,
    confirmed WebSocket; `_renderer_active` is the cloud's last SET_ACTIVE."""

    def __init__(self, active, connected=True):
        self._renderer_active = active
        self.is_renderer_active = active and connected


def test_the_session_fields_are_what_the_source_reads(adapter):
    """The contract between the two trees: every state the launcher refuses to
    run without is one the source maps to a phase, spelled the way the launcher
    writes it. A state the source has no entry for would read as loading for
    ever — no error, a spinner over silence."""
    from backend.sources.qobuz.source import _PHASES

    written = {adapter.session_fields(_Player(state), None)["player_state"]
               for state in adapter.PLAYER_STATES}
    assert len(written) == 5
    assert written == set(_PHASES)


def test_a_speaker_not_started_yet_is_nobody_s_output(adapter):
    """Either component can be missing while a speaker starts: no state, not
    the app's output — never an AttributeError into the status API."""
    assert adapter.session_fields(None, None) == {"player_state": None, "renderer_active": False}
    assert adapter.session_fields(_Player("PAUSED"), _WsManager(True)) == {
        "player_state": "paused", "renderer_active": True,
    }


def test_a_cloud_reconnect_is_not_the_app_leaving(adapter):
    """`is_renderer_active` drops while the cloud WebSocket reconnects (a token
    renewal, a network blip) with the audio still playing: read, it ended the
    session over music. The app leaving is the cloud's SET_ACTIVE(false)."""
    fields = adapter.session_fields(_Player("PLAYING"), _WsManager(True, connected=False))
    assert fields["renderer_active"] is True
    assert adapter.session_fields(_Player("STOPPED"), _WsManager(False))["renderer_active"] is False


# ----------------------------------------------------------------------
# The adaptation itself, against a qobuz_proxy shaped like the one it binds to
# ----------------------------------------------------------------------

_UPSTREAM_EXIT = 3


def _move_volume_attribute(fake):
    def __init__(self, device_index=0):
        self._gain = 0.5

    fake.AudioOutputStream.__init__ = __init__


def _move_duration(fake):
    del fake.QobuzPlayer.duration_ms


def _move_volume_argument(fake):
    def encode_join_session(self, device_uuid, supports_volume_control=True, is_active=False):
        return supports_volume_control

    fake.ProtocolCodec.encode_join_session = encode_join_session


def _move_register_routes(fake):
    del fake.app.register_routes


def _move_route_registration(fake):
    async def _start_web_server(self):
        self._web_app = {"on_auth_token": self._on_auth_token}

    fake.app.QobuzProxy._start_web_server = _start_web_server


def _move_login_guard(fake):
    async def _on_auth_token(self, user_id, auth_token, profile=None, **_kwargs):
        await self._start_speakers()
        return True

    fake.app.QobuzProxy._on_auth_token = _on_auth_token


def _move_login_callback(fake):
    async def _start_web_server(self):
        fake.app.register_routes(None)

    fake.app.QobuzProxy._start_web_server = _start_web_server


# A release that moved one binding, keyed by the name the binding carries.
MOVES = {
    "_volume": _move_volume_attribute,
    "duration_ms": _move_duration,
    "supports_volume_control": _move_volume_argument,
    "register_routes": _move_register_routes,
    "_start_web_server": _move_route_registration,
    "_speakers": _move_login_guard,
    "login callback": _move_login_callback,
}

# `qobuz_proxy/app.py` reduced to its web UI: compiled as a module rather than
# nested in the builder, so `_start_web_server` reaches `register_routes` as a
# global, the way upstream's does — a closure would hide the name the check reads.
_FAKE_APP_SOURCE = """
import asyncio

from aiohttp import web

reached = []


async def _handle_status(request):
    reached.append("status")
    return web.json_response({"status": "ok"})


async def _handle_logout(request):
    reached.append("logout")
    return web.json_response({"status": "ok"})


def register_routes(app):
    app.router.add_get("/api/status", _handle_status)
    app.router.add_post("/api/auth/logout", _handle_logout)


class QobuzProxy:
    def __init__(self):
        self._speakers = []
        self.saved = 0
        self.starts = 0

    async def _start_web_server(self):
        self._web_app = web.Application()
        # Upstream stores it as an app item, `_web_app["on_auth_token"]`.
        self.login_handler = self._on_auth_token
        register_routes(self._web_app)

    async def _on_auth_token(self, user_id, auth_token, profile=None, **_kwargs):
        self.saved += 1
        if not self._speakers:
            await self._start_speakers()
        return True

    async def _start_speakers(self):
        # Measured on the unit: 1.7 s from the start to the mDNS registration,
        # with the discovery port already bound — a second start meanwhile
        # finds it taken and retries forever.
        self.starts += 1
        await asyncio.sleep(0.01)
        self._speakers.append("Milo")
"""


def _build_qobuz_proxy(move=None):
    """qobuz-proxy 1.7.7 reduced to what the bindings read. Fresh classes on
    every call, since `apply()` rewrites them in place."""
    handed_over = []

    class PlaybackState(enum.IntEnum):
        STOPPED = 1
        PLAYING = 2
        PAUSED = 3
        LOADING = 4
        ERROR = 5

    class AudioOutputStream:
        def __init__(self, device_index=0):
            self._volume = 0.5

        def set_volume(self, level):
            self._volume = max(0.0, min(1.0, level / 100.0))

    class WsManager:
        def __init__(self, renderer_active=True):
            self._renderer_active = renderer_active

    class ProtocolCodec:
        def encode_join_session(self, device_uuid, *, supports_volume_control=True, is_active=False):
            return supports_volume_control

    class QobuzPlayer:
        def __init__(self, state, current_track=None, position_ms=0, duration_ms=0):
            self._state = state
            self.current_track = current_track
            self._position_ms = position_ms
            self._duration_ms = duration_ms

        @property
        def state(self):
            return self._state

        @property
        def current_position_ms(self):
            return self._position_ms

        @property
        def duration_ms(self):
            return self._duration_ms

    class Speaker:
        def __init__(self, player=None, ws_manager=None):
            self._player = player
            self._ws_manager = ws_manager

        def get_status(self):
            now_playing = None
            if self._player and self._player.current_track:
                track = self._player.current_track
                now_playing = {
                    "title": track["title"],
                    "artist": track["artist"],
                    "album": track["album"],
                    "album_art_url": track["artwork_url"],
                    "quality": "CD",
                }
            return {"name": "Milo", "status": "playing" if now_playing else "idle",
                    "now_playing": now_playing}

    def main():
        handed_over.append(True)
        return _UPSTREAM_EXIT

    def package(name, **attributes):
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        return module

    app = package("qobuz_proxy.app")
    exec(compile(_FAKE_APP_SOURCE, "qobuz_proxy/app.py", "exec"), app.__dict__)

    fake = types.SimpleNamespace(
        PlaybackState=PlaybackState, AudioOutputStream=AudioOutputStream, WsManager=WsManager,
        ProtocolCodec=ProtocolCodec, QobuzPlayer=QobuzPlayer, Speaker=Speaker, app=app,
        handed_over=handed_over, upstream_set_volume=AudioOutputStream.set_volume,
    )
    if move is not None:
        MOVES[move](fake)

    stream = package("qobuz_proxy.backends.local.stream", AudioOutputStream=AudioOutputStream)
    local = package("qobuz_proxy.backends.local", stream=stream)
    backends = package("qobuz_proxy.backends", local=local, PlaybackState=PlaybackState)
    protocol = package("qobuz_proxy.connect.protocol", ProtocolCodec=ProtocolCodec)
    connect = package("qobuz_proxy.connect", protocol=protocol, WsManager=WsManager)
    player = package("qobuz_proxy.playback.player", QobuzPlayer=QobuzPlayer)
    playback = package("qobuz_proxy.playback", player=player)
    speaker = package("qobuz_proxy.speaker", Speaker=Speaker)
    cli = package("qobuz_proxy.cli", main=main)
    root = package("qobuz_proxy", cli=cli, backends=backends, connect=connect,
                   playback=playback, speaker=speaker, app=app)
    fake.modules = [root, cli, backends, local, stream, connect, protocol, playback, player,
                    speaker, app]
    return fake


@pytest.fixture
def fake_qobuz_proxy(monkeypatch, tmp_path):
    """Installs a fresh fake qobuz_proxy, with `move` naming a binding a
    release moved (a key of MOVES). In memory only: a module file on disk
    would leave `__pycache__` inside the deployed tree."""
    monkeypatch.setenv("QOBUZPROXY_DATA_DIR", str(tmp_path))

    def install(move=None):
        fake = _build_qobuz_proxy(move)
        for module in fake.modules:
            monkeypatch.setitem(sys.modules, module.__name__, module)
        fake.data_dir = tmp_path
        return fake

    return install


def _keys_read(func, mapping):
    """The literal keys `func` reads from the variable named `mapping`, by
    `.get("…")` or `["…"]`."""
    keys = set()
    for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(func)))):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == mapping and node.args
                and isinstance(node.args[0], ast.Constant)):
            keys.add(node.args[0].value)
        elif (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id == mapping and isinstance(node.slice, ast.Constant)):
            keys.add(node.slice.value)
    return keys


def test_check_passes_on_the_shape_it_binds_to(adapter, fake_qobuz_proxy, capsys):
    """The fake is the shape every other test below relies on: if the check
    refused it, they would be exercising a release the unit would never run."""
    fake_qobuz_proxy()

    bindings = adapter._bindings()
    assert bindings and all(ok for _, ok, _ in bindings)
    assert adapter.check() == 0
    assert "bind cleanly" in capsys.readouterr().out


@pytest.mark.parametrize("move", sorted(MOVES))
def test_check_names_a_moved_binding(adapter, fake_qobuz_proxy, capsys, move):
    """A release that moved one name is refused, and the refusal says which —
    the maintainer re-derives that adaptation, not all of them."""
    fake_qobuz_proxy(move=move)

    moved = [name for name, ok, _ in adapter._bindings() if not ok]
    assert len(moved) == 1 and move in moved[0]
    assert adapter.check() == 1
    assert moved[0] in capsys.readouterr().err


def test_a_new_stream_starts_at_unity(adapter, fake_qobuz_proxy):
    """Upstream opens every stream at 50 %: kept, CamillaDSP would no longer be
    the only attenuation stage (invariant 7), and every Qobuz track would play
    6 dB down with the bits lost before the DSP."""
    fake = fake_qobuz_proxy()
    assert fake.AudioOutputStream()._volume == 0.5

    adapter.apply()

    assert fake.AudioOutputStream()._volume == 1.0


def test_the_app_slider_is_inert_unless_the_flag_allows_it(adapter, fake_qobuz_proxy):
    """Off (the default), the app's slider leaves the stream at unity; on, it
    sets the level, bounded to what upstream itself accepts."""
    fake = fake_qobuz_proxy()
    adapter.apply()
    stream = fake.AudioOutputStream()

    stream.set_volume(30)
    assert stream._volume == 1.0

    (fake.data_dir / adapter.VOLUME_FLAG).write_text("1")
    stream.set_volume(30)
    assert stream._volume == pytest.approx(0.3)
    stream.set_volume(150)
    assert stream._volume == 1.0
    stream.set_volume(-10)
    assert stream._volume == 0.0


def test_the_status_carries_the_playhead_and_the_session(adapter, fake_qobuz_proxy):
    """What the adaptation owes the status is what the source reads from it
    and upstream does not write: the session at the top level, the playhead
    inside `now_playing`. Read off the consumer, so a key it starts reading
    that nothing provides fails here rather than on the unit."""
    from backend.sources.qobuz.source import QobuzSource

    fake = fake_qobuz_proxy()
    track = {"title": "On & On", "artist": "Erykah Badu", "album": "Baduizm",
             "artwork_url": "https://static.qobuz.example/on-and-on.jpg"}
    player = fake.QobuzPlayer(fake.PlaybackState.PLAYING, track,
                              position_ms=61_000, duration_ms=245_000)
    speaker = fake.Speaker(player, fake.WsManager(renderer_active=True))
    upstream = speaker.get_status()

    owed_top = _keys_read(QobuzSource._apply, "speaker") - set(upstream)
    owed_now = (_keys_read(QobuzSource._apply, "now")
                | _keys_read(QobuzSource._apply_track, "now")) - set(upstream["now_playing"])
    assert owed_top and owed_now

    adapter.apply()
    status = speaker.get_status()

    assert owed_top <= set(status)
    assert owed_now <= set(status["now_playing"])
    assert {key: status[key] for key in owed_top} == {"player_state": "playing",
                                                      "renderer_active": True}
    assert status["now_playing"]["position_ms"] == player.current_position_ms
    assert status["now_playing"]["duration_ms"] == player.duration_ms

    loading = fake.Speaker(fake.QobuzPlayer(fake.PlaybackState.LOADING), fake.WsManager())
    status = loading.get_status()
    assert status["now_playing"] is None
    assert status["player_state"] == "loading" and status["renderer_active"] is True


async def test_two_logins_at_once_start_the_speaker_once(adapter, fake_qobuz_proxy):
    """Measured 2026-11-05: the OAuth callback landed twice, 0.9 s apart, and
    each login found no speaker running yet — the first was still starting —
    so each started one. The second could not bind the discovery port the
    first held, and upstream's retry loop failed on it every 10/20/40 s until
    the sidecar was restarted, and the app did not show "Milo" again. One login at a
    time: the second finds the first's speaker, and is still recorded."""
    fake = fake_qobuz_proxy()
    adapter.apply()
    proxy = fake.app.QobuzProxy()

    assert await asyncio.gather(proxy._on_auth_token("1", "a-token"),
                                proxy._on_auth_token("1", "a-token")) == [True, True]

    assert proxy.starts == 1
    assert proxy._speakers == ["Milo"]
    assert proxy.saved == 2


def test_main_does_not_hand_over_when_a_binding_moved(adapter, fake_qobuz_proxy, monkeypatch):
    """Every start goes through the check: handed over to a release that moved
    a binding, the sidecar would run unadapted — at 50 %, with no progress —
    and say nothing."""
    monkeypatch.setattr(sys, "argv", ["milo-qobuz"])

    moved = fake_qobuz_proxy(move="duration_ms")
    assert adapter.main() == 1
    assert moved.handed_over == []
    assert moved.AudioOutputStream.set_volume is moved.upstream_set_volume

    sound = fake_qobuz_proxy()
    assert adapter.main() == _UPSTREAM_EXIT
    assert sound.handed_over == [True]
    assert sound.AudioOutputStream()._volume == 1.0


# ----------------------------------------------------------------------
# The web UI's request gate: :8689 listens on every interface for the OAuth flow
# ----------------------------------------------------------------------

async def _gated_web_ui(adapter, fake):
    """The web app built the way upstream's `_start_web_server` builds it,
    after `apply()` — through the module attribute `app.py` calls."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    adapter.apply()
    web_app = web.Application()
    sys.modules["qobuz_proxy.app"].register_routes(web_app)
    return TestClient(TestServer(web_app))


async def test_the_webui_refuses_a_foreign_host_and_a_foreign_origin(adapter, fake_qobuz_proxy):
    """Without the gate, a page from the internet open on a LAN browser logs the
    household's Qobuz account out with a self-submitting form (a simple request,
    no preflight), and under DNS rebinding rewrites `config.yaml` through
    `/api/speakers`. The refusal must land before upstream's handler runs,
    while the backend's own calls (loopback, no Origin) and the OAuth page
    (same origin, on the unit's name) still pass."""
    fake = fake_qobuz_proxy()

    async with await _gated_web_ui(adapter, fake) as client:
        foreign_host = await client.get("/api/status", headers={"Host": "evil.example"})
        assert foreign_host.status == 403
        assert (await foreign_host.json())["status"] == "error"

        for origin in ("http://evil.example", "null"):
            response = await client.post("/api/auth/logout", headers={"Origin": origin})
            assert response.status == 403, origin
        assert fake.app.reached == []

        assert (await client.get("/api/status")).status == 200
        assert (await client.post("/api/auth/logout")).status == 200
        oauth_page = await client.post("/api/auth/logout", headers={
            "Host": "milo.local:8689", "Origin": "http://milo.local:8689"})
        assert oauth_page.status == 200

    assert fake.app.reached == ["status", "logout", "logout"]


_HOSTS = ["milo.local", "milo.local:8689", "MILO.LOCAL", "milo", "milo-2.local", "milo.lan",
          "milo.home.arpa", "localhost:8689", "127.0.0.1:8689", "192.168.1.5", "[::1]:8689",
          "10.42.0.1", "100.64.0.1", "milo.fritz.box", "milo.local.evil.example",
          "evil.example", "8.8.8.8", "[::1", ""]
_ORIGINS = [None, "null", "http://milo.local", "https://milo.local", "http://milo.local:8689",
            "http://localhost:5173", "http://milo-2.local", "http://192.168.1.5:8689",
            "http://[::1]:8689", "http://100.64.0.1", "http://milo.fritz.box",
            "http://evil.example", "file://"]
_METHODS = ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]


def test_the_qobuz_gate_agrees_with_the_backend_gate(adapter):
    """The launcher restates the backend's two rules rather than importing them
    (the sidecar's venv has neither `backend` nor starlette, and a rootfs script
    may not reach outside its tree), so this is what holds the copies equal: a
    name the backend comes to accept — an Avahi rename, a router's DNS suffix —
    must open the OAuth page too, and a host it refuses must not reach
    `/api/speakers`. The expected verdict is the backend's, never written here."""
    from starlette.datastructures import Headers

    from backend.api import middleware

    disagreements, refused = [], 0
    for host in _HOSTS:
        for origin in _ORIGINS:
            headers = {"host": host} | ({"origin": origin} if origin is not None else {})
            for method in _METHODS:
                backend = bool(middleware.RequestOriginGate._rejection(
                    Headers(headers), method in middleware._STATE_CHANGING_METHODS))
                qobuz = bool(adapter.rejection(method, host, origin))
                refused += backend
                if backend != qobuz:
                    disagreements.append((method, host, origin, backend))

    total = len(_HOSTS) * len(_ORIGINS) * len(_METHODS)
    assert 0 < refused < total, "the corpus must hold requests either gate passes and refuses"
    assert disagreements == []


def test_the_unit_runs_the_launcher_under_a_private_umask():
    """qobuz-proxy writes its OAuth token cache with a plain `open(..., "w")`
    and rewrites it in place, so the unit's umask is the file's mode for good.
    Without one masking group and others, `user_auth_token` is readable by
    every local account (nginx's, avahi's) — a bearer that opens the account."""
    unit = configparser.ConfigParser(strict=False, interpolation=None)
    unit.optionxform = str
    unit.read(Path(__file__).resolve().parents[2] / "system" / "milo-qobuz.service")

    umask = unit["Service"].get("UMask")
    assert umask is not None, "milo-qobuz.service declares no UMask"
    assert int(umask, 8) & 0o077 == 0o077
