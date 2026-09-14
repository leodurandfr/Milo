"""The wire between Milō and the CamillaDSP daemon, frame by frame.

`CamillaDspClient` replaced `pycamilladsp`, and with it Milō took ownership of a
protocol it does not define. The daemon is the consumer here and it is not ours
to change, so the frame sent *is* the contract — exactly as argv is for systemd —
and every call below asserts the frame, not merely that something was sent.

The shapes asserted were captured from CamillaDSP 4.1.3 on 2026-09-14, not
reconstructed: a param-less command is a bare JSON string, a parameterised one an
object, `SetConfigJson` takes the config as a *string* (an object is refused),
`GetConfigJson` answers with a string too, and `ReadConfigFile` answers YAML.

The double is a real `websockets` server on loopback — the outside world, the way
the daemon is — so what is exercised is the client's own send/recv/parse, never a
mock of it. What it cannot cover is CamillaDSP itself changing its protocol; that
is what the version handshake and the manual checklist are for.
"""
import asyncio
import json

import pytest
from websockets.asyncio.server import serve

from backend.core.equalizer.camilladsp_client import CamillaDspClient, CamillaDspError

CONFIG = {
    "filters": {"eq_band_00": {"type": "Biquad", "parameters": {"freq": 31.0}}},
    "pipeline": [{"type": "Filter", "channels": [0], "names": ["eq_band_00"]}],
}


def ok(command: str, value=None) -> str:
    """The daemon's success frame."""
    return json.dumps({command: {"result": "Ok", "value": value}})


class DaemonDouble:
    """A WebSocket server that answers like CamillaDSP, and keeps what it heard."""

    def __init__(self):
        self.frames: list = []
        self.replies = {"GetVersion": ok("GetVersion", "4.1.3")}
        self.silent: set = set()
        self.hang_up: set = set()
        self.port = None

    def answers(self, command: str, value=None) -> None:
        self.replies[command] = ok(command, value)

    def answers_raw(self, command: str, frame: str) -> None:
        self.replies[command] = frame

    def sent(self, index: int = -1):
        """What the client put on the wire, parsed back off it."""
        assert self.frames, "the client sent nothing"
        return json.loads(self.frames[index])

    def command_of(self, frame: str) -> str:
        parsed = json.loads(frame)
        return parsed if isinstance(parsed, str) else next(iter(parsed))

    async def _handle(self, ws):
        async for frame in ws:
            self.frames.append(frame)
            command = self.command_of(frame)
            if command in self.hang_up:
                await ws.close()
                return
            if command in self.silent:
                continue
            await ws.send(self.replies.get(command, ok(command)))


@pytest.fixture
async def daemon():
    double = DaemonDouble()
    async with await serve(double._handle, "127.0.0.1", 0) as server:
        double.port = server.sockets[0].getsockname()[1]
        yield double


@pytest.fixture
async def client(daemon):
    """A connected client. Connecting is itself the `GetVersion` handshake."""
    client = CamillaDspClient("127.0.0.1", daemon.port, timeout=2.0)
    await client.connect()
    daemon.frames.clear()
    yield client
    await client.disconnect()


class TestTheTwelveCalls:
    """One per call the equalizer service makes. The frame is the assertion."""

    async def test_the_handshake_reads_the_daemon_version_back(self, daemon):
        """`connect` is not just a socket: a port that answers but is not
        CamillaDSP has to fail at connect, not at the first volume command."""
        client = CamillaDspClient("127.0.0.1", daemon.port, timeout=2.0)

        await client.connect()

        assert daemon.sent() == "GetVersion"
        assert client.version == "4.1.3"
        assert client.connected is True
        await client.disconnect()

    async def test_get_state(self, daemon, client):
        daemon.answers("GetState", "Paused")

        assert await client.get_state() == "Paused"

        assert daemon.sent() == "GetState"

    async def test_get_volume(self, daemon, client):
        daemon.answers("GetVolume", -48.944443)

        assert await client.get_volume() == pytest.approx(-48.944443)

        assert daemon.sent() == "GetVolume"

    async def test_set_volume(self, daemon, client):
        """dB as a float. The daemon refuses a string (`expected f32`), so the
        conversion is not decoration — a percentage string would silently be a
        refusal, and volume is the one thing that must never be."""
        await client.set_volume(-12)

        assert daemon.sent() == {"SetVolume": -12.0}

    async def test_get_mute(self, daemon, client):
        daemon.answers("GetMute", True)

        assert await client.get_mute() is True

        assert daemon.sent() == "GetMute"

    async def test_set_mute(self, daemon, client):
        await client.set_mute(True)

        assert daemon.sent() == {"SetMute": True}

    async def test_get_config_parses_the_json_string_the_daemon_answers(
        self, daemon, client
    ):
        """`GetConfigJson`'s value is a *string* of JSON, not an object. Handing
        that string on unparsed would make every `config["filters"]` a TypeError."""
        daemon.answers("GetConfigJson", json.dumps(CONFIG))

        assert await client.get_config() == CONFIG

        assert daemon.sent() == "GetConfigJson"

    async def test_get_config_answers_none_while_the_daemon_holds_no_graph(
        self, daemon, client
    ):
        """An inactive daemon has no active config, and that is not an error —
        it is the fallback to the config file the service depends on."""
        daemon.answers("GetConfigJson", None)

        assert await client.get_config() is None

    async def test_set_config_sends_the_graph_as_a_string(self, daemon, client):
        """Measured: `{"SetConfigJson": {...}}` is refused with *invalid type:
        map, expected a string*. The object has to be serialised first."""
        await client.set_config(CONFIG)

        frame = daemon.sent()
        assert isinstance(frame["SetConfigJson"], str)
        assert json.loads(frame["SetConfigJson"]) == CONFIG

    async def test_get_config_file_path(self, daemon, client):
        daemon.answers("GetConfigFilePath", "/var/lib/milo/camilladsp/config.yml")

        assert await client.get_config_file_path() == "/var/lib/milo/camilladsp/config.yml"

        assert daemon.sent() == "GetConfigFilePath"

    async def test_read_config_file_parses_yaml_not_json(self, daemon, client):
        """`ReadConfigFile` hands back the file as YAML text, where
        `GetConfigJson` hands back JSON. Parsing this one as JSON fails on every
        real config file."""
        daemon.answers("ReadConfigFile", "filters:\n  eq_band_00:\n    type: Biquad\n")

        config = await client.read_config_file("/var/lib/milo/camilladsp/config.yml")

        assert config == {"filters": {"eq_band_00": {"type": "Biquad"}}}
        assert daemon.sent() == {"ReadConfigFile": "/var/lib/milo/camilladsp/config.yml"}

    async def test_get_playback_peak(self, daemon, client):
        daemon.answers("GetPlaybackSignalPeak", [-12.5, -13.5])

        assert await client.get_playback_peak() == [-12.5, -13.5]

        assert daemon.sent() == "GetPlaybackSignalPeak"

    async def test_get_capture_peak(self, daemon, client):
        daemon.answers("GetCaptureSignalPeak", [-30.0, -31.0])

        assert await client.get_capture_peak() == [-30.0, -31.0]

        assert daemon.sent() == "GetCaptureSignalPeak"

    @pytest.mark.parametrize(
        "measured, expected",
        [
            (48127, 48000),   # what this unit answers while playing 48 kHz
            (44100, 44100),
            (0, None),        # nothing captured: no rate to show
            (60000, None),    # too far from any standard rate to name one
        ],
    )
    async def test_get_capture_rate_is_rounded_to_a_rate_that_exists(
        self, daemon, client, measured, expected
    ):
        """The daemon reports what it counted, not what the stream declares. The
        raw figure on screen reads as a fault, so it is rounded to the nearest
        standard rate — and refused rather than invented when nothing is close."""
        daemon.answers("GetCaptureRate", measured)

        assert await client.get_capture_rate() == expected

        assert daemon.sent() == "GetCaptureRate"


class TestWhenTheDaemonWillNotAnswer:
    """Four ways a call fails. None of them may end in a plausible default."""

    async def test_a_refusal_raises_instead_of_answering_with_a_default(
        self, daemon, client
    ):
        """Both measured refusal shapes, because they differ on the wire and a
        parser that requires `value` breaks on the first.

        Returning a default here is the failure that matters: `_get_config`
        would take an empty graph for the live one and push it back, erasing
        every band the operator set.
        """
        daemon.answers_raw(
            "ReadConfigFile",
            json.dumps({"ReadConfigFile": {"result": {"ConfigReadError": "no such file"}}}),
        )
        daemon.answers_raw(
            "GetVolume", json.dumps({"Invalid": {"error": "unknown variant `GetVolume`"}})
        )

        with pytest.raises(CamillaDspError, match="ConfigReadError.*no such file"):
            await client.read_config_file("/nope.yml")
        with pytest.raises(CamillaDspError, match="unknown variant"):
            await client.get_volume()

        # A refusal is the daemon talking, so the connection stays up.
        assert client.connected is True

    async def test_a_reply_to_another_command_is_not_taken_as_the_answer(
        self, daemon, client
    ):
        """One socket, replies in order: a reply carrying someone else's name
        means the stream is a reply out of step, and every later command would
        read the previous one's answer — a volume that answers with a mute.

        So the socket goes, not just the command.
        """
        daemon.answers_raw("GetVolume", ok("GetMute", False))

        with pytest.raises(CamillaDspError, match="reply to something else"):
            await client.get_volume()

        assert client.connected is False

    async def test_a_socket_that_closes_mid_call_surfaces_as_a_disconnect(
        self, daemon, client
    ):
        """`CamillaDSPService._run` demotes the service on any exception, and the
        connection loop then reconnects and re-pushes volume and EQ. What it
        cannot do is act on a call that answered normally with nothing."""
        daemon.hang_up.add("GetState")

        with pytest.raises(ConnectionError):
            await client.get_state()

        assert client.connected is False

    async def test_a_silent_daemon_times_out_rather_than_hanging(self, daemon):
        """A daemon that accepted the frame and says nothing (the silence-pause
        failure's own signature) must not park a request for ever: the volume
        ramp behind it is a physical control someone is turning."""
        client = CamillaDspClient("127.0.0.1", daemon.port, timeout=0.2)
        await client.connect()
        daemon.silent.add("GetState")

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(client.get_state(), timeout=2.0)

        # The late reply would answer the next command, so the socket is dropped.
        assert client.connected is False
