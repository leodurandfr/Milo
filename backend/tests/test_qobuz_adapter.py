# backend/tests/test_qobuz_adapter.py
"""The gate that decides whether a qobuz-proxy release can be shipped.

`rootfs/usr/local/bin/milo-qobuz` adds position/duration and the session's
state to the sidecar's status, holds its stream at unity gain and tells the app
when its slider is inert — things upstream offers no setting for.
They bind to names, and `--check` is what refuses a release that moved one,
before `_update_qobuz_proxy` restarts the service onto it.

What breaks when this fails: the check answers "moved" for a release that is
fine (every qobuz-proxy update refuses, which is the bug that replaced the
source patches it succeeded), or answers "fine" for one that is not (the sidecar
starts, plays, and reports a progress bar that never moves — no error anywhere).
"""
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
