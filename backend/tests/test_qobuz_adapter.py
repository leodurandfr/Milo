# backend/tests/test_qobuz_adapter.py
"""The gate that decides whether a qobuz-proxy release can be shipped.

`rootfs/usr/local/bin/milo-qobuz` adds position/duration and the session's
state to the sidecar's status, holds its stream at unity gain and tells the app
when its slider is inert — things upstream offers no setting for.
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


# A release that moved one binding, keyed by the name the binding carries.
MOVES = {
    "_volume": _move_volume_attribute,
    "duration_ms": _move_duration,
    "supports_volume_control": _move_volume_argument,
}


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

    fake = types.SimpleNamespace(
        PlaybackState=PlaybackState, AudioOutputStream=AudioOutputStream, WsManager=WsManager,
        ProtocolCodec=ProtocolCodec, QobuzPlayer=QobuzPlayer, Speaker=Speaker,
        handed_over=handed_over, upstream_set_volume=AudioOutputStream.set_volume,
    )
    if move is not None:
        MOVES[move](fake)

    def package(name, **attributes):
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        return module

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
                   playback=playback, speaker=speaker)
    fake.modules = [root, cli, backends, local, stream, connect, protocol, playback, player, speaker]
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
