# backend/tests/test_spotify_failure_arms.py
"""What SpotifySource does when go-librespot does not answer.

`test_spotify_source.py` drives the happy paths, including the multiroom
reroute. Every *fallback* below it was uncovered at 39ff9daf, and the reroute
fallbacks are the ones that matter most: the source's own docstring names the
outcome they exist to prevent — "a source still holding the loopback would
block snapclient, which is the one outcome worse than a dropped session".

So the rule these tests pin is: **any reroute step that cannot be confirmed
must fall back to the real stop, never to a silent success.** A soft reroute
that reports True without having parked the output leaves `milo_spotify` open
on the loopback and multiroom comes up mute.

Also here: GET /status's failure arms (the ground truth the reroute reads
before pausing, and what a state request reads), `_send_api_command`'s, the
journal bridge's loop-body guard, and the start path's teardown-on-crash.

The daemon double is stateful — /status reports what the POSTs did to it, so a
release that never actually pauses cannot pass. Where a live session is needed
the scenario runs on `SpotifyWorld` (tests/spotify_world.py) instead.
"""
import asyncio
import math
import time
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp.client_reqrep import ConnectionKey
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from backend.core.models.audio_state import AudioSource
from backend.sources.spotify.source import SpotifySource
from backend.tests.golden.harness import make_state_machine
from backend.tests.spotify_world import PARAPLUIE, SpotifyWorld


def _refused():
    """The exception aiohttp raises when nothing is listening on the port.

    Built with a real `ConnectionKey` because `ClientConnectorError.__str__`
    dereferences it — a hand-made one explodes in the logging call rather than
    in the branch under test.
    """
    key = ConnectionKey(
        host="localhost", port=3678, is_ssl=False, ssl=None, proxy=None,
        proxy_auth=None, proxy_headers_hash=None, server_hostname=None,
    )
    return aiohttp.ClientConnectorError(key, OSError(111, "Connection refused"))


@pytest.fixture
def config(tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        "server:\n  address: localhost\n  port: 3678\naudio_device: milo_spotify\n"
    )
    return {"config_path": str(config_file)}


@pytest.fixture
async def source(config):
    src = SpotifySource(config)
    src._service_manager = Mock()
    src._service_manager.start = AsyncMock(return_value=True)
    src._service_manager.stop = AsyncMock(return_value=True)
    src._service_manager.restart = AsyncMock(return_value=True)
    src._service_manager.is_active = AsyncMock(return_value=True)
    # A daemon that cannot be named is not watched: no pidfd on a test host.
    src._service_manager.main_pid = AsyncMock(return_value=None)
    yield src
    await src.shutdown()


def daemon(source, *, paused=True, post_status=200, get_status=200, get_raises=None,
           refuse=(), pause_lag=0):
    """go-librespot's HTTP API — the outside world of this source.

    Stateful on purpose: /status answers what the POSTs did to it, so a release
    that never pauses observes an unpaused daemon. `get_raises` stands for a
    daemon that is not listening at all; `get_status` is one HTTP status for
    every read, or a list answered in order (the last one repeating);
    `pause_lag` is how many reads after an accepted pause still report it
    playing (`math.inf`: it never says paused); `refuse` names the commands it
    answers 500 while the others succeed.
    """
    state = {"paused": paused, "lag": 0}
    statuses = list(get_status) if isinstance(get_status, (list, tuple)) else [get_status]

    session = MagicMock()
    session.close = AsyncMock()
    source._http = session
    source._api_url = "http://localhost:3678"

    def exchange(response=None, error=None):
        cm = AsyncMock()
        if error is not None:
            cm.__aenter__.side_effect = error
        else:
            cm.__aenter__.return_value = response
        return cm

    def get(url, *args, **kwargs):
        if get_raises is not None:
            return exchange(error=get_raises)
        status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
        paused_now = state["paused"]
        if state["lag"] > 0:
            state["lag"] -= 1
            paused_now = False
        response = MagicMock()
        response.status = status
        response.json = AsyncMock(return_value={
            "paused": paused_now,
            "track": {
                "name": "Track", "artist_names": ["Artist"], "album_name": "Album",
                "album_cover_url": None, "duration": 200000, "position": 76611,
            },
        })
        return exchange(response)

    def post(url, json=None):
        command = url.rsplit("/player/", 1)[-1]
        status = 500 if command in refuse else post_status
        if status == 200 and command in ("pause", "resume"):
            state["paused"] = command == "pause"
            state["lag"] = pause_lag if command == "pause" else 0
        response = MagicMock()
        response.status = status
        return exchange(response)

    session.get = Mock(side_effect=get)
    session.post = Mock(side_effect=post)
    return session


def stepping_clock(step):
    """This module's `time`, each monotonic read `step` seconds after the last,
    so a bounded wait reaches its cap in a few reads rather than real seconds."""
    now = [0.0]

    def monotonic():
        value = now[0]
        now[0] += step
        return value

    return SimpleNamespace(monotonic=monotonic, time=time.time)


def commands(session):
    return [
        (call.args[0].rsplit("/player/", 1)[-1], call.kwargs.get("json"))
        for call in session.post.call_args_list
    ]


class TestTheReleaseThatCannotBeConfirmed:
    """Each arm ends in the base `release_for_reroute()`, which stops the unit.

    A silent True here is the failure mode the source's docstring names:
    `milo_spotify` stays open on the loopback and snapclient cannot take it.
    """

    async def test_a_source_with_no_session_stops_outright(self, source):
        """Before the daemon has ever been reached there is no HTTP session and
        no Connect session to preserve, so there is nothing to park — but the
        unit still has to go down, or the reroute proceeds around a source it
        never released.

        Measured constat: the `not self._http or not self._api_url` guard at
        the top is **inert**, shadowed by the identical guard inside
        `_read_status` one line below — remove it and the next arm falls back
        to the same full stop anyway, differing only in which warning is
        logged. Left in place (it says the precondition, and B7-15's rule is
        that a change with no reachable effect is worth less than a constat);
        the assertion is on the outcome, which holds either way."""
        source._api_url = "http://localhost:3678"

        assert await source.release_for_reroute() is True

        source._service_manager.stop.assert_called_once_with("milo-spotify.service")

    async def test_a_source_with_no_api_url_stops_outright(self, source):
        http = MagicMock()
        http.close = AsyncMock()  # the fallback stop closes it
        source._http = http
        source._api_url = None

        assert await source.release_for_reroute() is True

        http.post.assert_not_called()
        source._service_manager.stop.assert_called_once_with("milo-spotify.service")

    async def test_a_daemon_that_will_not_answer_status_stops_outright(self, source):
        """The ground truth is read *before* pausing, deliberately: a stale
        session phase (a WS that dropped mid-playback) would skip the pause and
        hand a live stream to a sink that does not rate-limit."""
        session = daemon(source, get_status=500)

        assert await source.release_for_reroute() is True

        assert ("output", {"device": "null"}) not in commands(session)
        source._service_manager.stop.assert_called_once_with("milo-spotify.service")

    async def test_an_unreachable_daemon_stops_outright(self, source):
        session = daemon(source, get_raises=_refused())

        assert await source.release_for_reroute() is True

        assert ("output", {"device": "null"}) not in commands(session)
        source._service_manager.stop.assert_called_once_with("milo-spotify.service")

    async def test_a_pause_the_daemon_never_confirms_stops_outright(self, source, monkeypatch):
        """The output is only parked once the daemon says it stopped pulling
        samples. Parking it on an unconfirmed pause is what runs the track to
        its end in seconds on RELEASE_DEVICE."""
        session = daemon(source, paused=False, pause_lag=math.inf)
        monkeypatch.setattr("backend.sources.spotify.source.time", stepping_clock(0.5))

        assert await source.release_for_reroute() is True

        assert ("pause", {}) in commands(session)
        assert ("output", {"device": "null"}) not in commands(session)
        source._service_manager.stop.assert_called_once_with("milo-spotify.service")


class TestConfirmingThePause:
    """`_pause_and_confirm` — the command returns before the player stops."""

    async def test_a_pause_the_daemon_refuses_never_enters_the_poll(self, source):
        """A refused pause has to answer at once, not wait the confirmation out.

        Asserting the poll never starts rather than the return value: with the
        early return removed the loop still times out to False, so the answer
        alone cannot separate the two — the mutation makes the suite *slow*
        instead of red (the B1(d) family). What the two versions really differ
        on is whether the daemon is polled at all."""
        session = daemon(source, paused=False, refuse={"pause"})

        assert await source._pause_and_confirm() is False
        session.get.assert_not_called()

    async def test_a_daemon_that_stops_answering_mid_wait_is_not_confirmed(
        self, source
    ):
        """A daemon that dies between the pause and the confirmation must not
        be read as "paused" — the reroute would park an output on a process
        that is gone."""
        session = daemon(source, paused=False, get_status=[200, 503], pause_lag=1)

        assert await source._pause_and_confirm(timeout=2.0, interval=0.001) is False
        assert session.get.call_count == 2

    async def test_a_daemon_that_never_reports_paused_times_out(self, source):
        """The bound is what stops a wedged daemon holding the whole multiroom
        toggle open."""
        session = daemon(source, paused=False, pause_lag=math.inf)

        assert await source._pause_and_confirm(timeout=0.05, interval=0.01) is False
        assert session.get.call_count > 1

    async def test_a_daemon_that_confirms_late_is_still_confirmed(self, source):
        """The poll has to keep asking rather than answer on the first read:
        go-librespot reports the old state for a tick or two after the command."""
        session = daemon(source, paused=False, pause_lag=2)

        assert await source._pause_and_confirm(timeout=2.0, interval=0.001) is True
        assert session.get.call_count == 3


class TestTheReAcquireThatCannotBeConfirmed:
    async def test_a_resume_the_daemon_refuses_does_not_fail_the_reroute(
        self, source, monkeypatch
    ):
        """Measured asymmetry, and it is the right one: the output is already
        reopened, so the session is back and the owner can press play. Falling
        back to a restart here would throw away a working session over a
        transport command."""
        monkeypatch.setenv("MILO_MODE", "direct")
        session = daemon(source, paused=False, refuse={"resume"})

        assert await source.release_for_reroute() is True
        assert await source.acquire_after_reroute() is True

        assert commands(session) == [
            ("pause", {}),
            ("output", {"device": "null"}),
            ("output", {"device": "milo_spotify_direct"}),
            ("resume", {}),
        ]
        source._service_manager.stop.assert_not_awaited()
        source._service_manager.start.assert_not_awaited()


class TestReadingTheDaemonsStatus:
    """GET /status, read by a state request (`refresh_metadata`) — the same
    read the reroute takes before pausing and the /events handler after every
    burst."""

    async def test_a_source_with_no_http_session_reports_no_refresh(self, source):
        assert await source.refresh_metadata() is False

    async def test_a_source_with_no_api_url_reports_no_refresh(self, source):
        source._http = MagicMock()
        source._api_url = None

        assert await source.refresh_metadata() is False

    async def test_a_non_200_answer_is_not_a_refresh(self, source):
        daemon(source, get_status=503)

        assert await source.refresh_metadata() is False

    async def test_a_daemon_that_is_not_listening_is_logged_at_debug(
        self, source, caplog
    ):
        """go-librespot being down is an ordinary state — the source polls it
        during every start. At error level this raises the WebSocket error
        banner for a daemon systemd is already restarting.

        Constat measured here: the handler names
        `(ClientConnectorError, ClientOSError)`, and the first is a *subclass*
        of the second — aiohttp 3.14.1 gives ClientConnectorError -> ClientOSError
        -> ClientConnectionError -> ClientError -> OSError — so naming it buys
        nothing. Not removed: it documents the case that actually happens here
        and costs nothing (family B1-10 / B7-13)."""
        daemon(source, get_raises=_refused())

        with caplog.at_level("DEBUG", logger="source.spotify"):
            assert await source.refresh_metadata() is False

        assert not [r for r in caplog.records if r.levelname == "ERROR"]

    async def test_an_unexpected_failure_is_logged_at_error(self, source, caplog):
        """The other half of the pair: anything that is *not* the daemon being
        down is a real failure and must reach the banner."""
        daemon(source, get_raises=RuntimeError("json exploded"))

        with caplog.at_level("ERROR", logger="source.spotify"):
            assert await source.refresh_metadata() is False

        assert "json exploded" in caplog.text

    async def test_a_state_request_that_finds_no_session_empties_the_card(
        self, monkeypatch, tmp_path
    ):
        """go-librespot answers 204 once no phone holds the speaker (measured).
        Keeping the previous track leaves it on screen for a session that
        ended — here one whose `inactive` was never heard."""
        world = SpotifyWorld(monkeypatch, tmp_path)
        try:
            await world.select()
            await world.phone_plays(PARAPLUIE)
            world.daemon.session, world.daemon.track = False, None

            state = await world.get_state()

            assert state["session"] is None
        finally:
            await world.source.shutdown()


class TestSendingACommand:
    async def test_a_command_with_no_http_session_is_refused_not_crashed(self, source):
        result = await source._send_api_command("pause")

        assert result["success"] is False
        assert "Session not active" in result["error"]

    async def test_a_daemon_that_cannot_be_reached_is_a_refusal(self, source):
        session = daemon(source)
        session.post = Mock(side_effect=_refused())

        result = await source._send_api_command("pause")

        assert result["success"] is False

    async def test_a_non_200_answer_is_a_refusal(self, source):
        daemon(source, post_status=500)

        assert (await source._send_api_command("pause"))["success"] is False


class TestTheJournalBridge:
    """`_monitor_logs` — the only thing that turns a go-librespot failure into
    the UI's error banner."""

    async def test_every_line_reaches_the_parser(self, source):
        seen = []

        async def follow(unit, *, consequence, logger=None):
            for line in ["authenticated AP", "loaded track"]:
                yield line

        source._handle_log_line = AsyncMock(side_effect=lambda ln: seen.append(ln))

        with patch("backend.sources.spotify.source.follow_unit", follow):
            await source._monitor_logs()

        assert seen == ["authenticated AP", "loaded track"]

    async def test_a_line_that_throws_costs_only_that_line(self, source, caplog):
        """Background-loop doctrine. Without the body guard, one unparsable
        journal line ends the bridge and Spotify stops reporting failures for
        the rest of the process — with nothing to say so."""
        seen = []

        async def follow(unit, *, consequence, logger=None):
            for line in ["bad", "authenticated AP"]:
                yield line

        async def handle(line):
            seen.append(line)
            if line == "bad":
                raise ValueError("unparsable")

        source._handle_log_line = handle

        with patch("backend.sources.spotify.source.follow_unit", follow):
            with caplog.at_level("ERROR", logger="source.spotify"):
                await source._monitor_logs()

        assert seen == ["bad", "authenticated AP"]

    async def test_the_journal_going_away_is_logged_not_raised(self, source, caplog):
        """The bridge runs as a bare task; an exception escaping it dies
        unobserved except for asyncio's own warning."""
        async def follow(unit, *, consequence, logger=None):
            raise RuntimeError("journalctl gone")
            yield  # pragma: no cover - generator marker

        with patch("backend.sources.spotify.source.follow_unit", follow):
            with caplog.at_level("ERROR", logger="source.spotify"):
                await source._monitor_logs()

        assert "journalctl gone" in caplog.text

    async def test_a_cancelled_bridge_ends_quietly(self, source, caplog):
        """`_stop_log_monitor` cancels it on every stop, so an error log here
        would put a banner up on an ordinary source switch."""
        async def follow(unit, *, consequence, logger=None):
            raise asyncio.CancelledError
            yield  # pragma: no cover - generator marker

        with patch("backend.sources.spotify.source.follow_unit", follow):
            with caplog.at_level("ERROR", logger="source.spotify"):
                await source._monitor_logs()

        assert caplog.records == []

    async def test_starting_the_bridge_twice_leaves_one_task(self, source):
        """`_do_start` arms it; a restart that armed a second would double
        every error banner.

        The double ends on its own instead of parking on a long sleep: without
        the guard the second task is never cancelled by anything here, and a
        parked one keeps the event loop from closing — the mutation then hangs
        the run instead of failing it (T7-1's family, paid twice in this unit).
        Counting the tasks that were created is what separates the two."""
        started = []

        async def follow(unit, *, consequence, logger=None):
            started.append(True)
            return
            yield  # pragma: no cover - generator marker

        with patch("backend.sources.spotify.source.follow_unit", follow):
            source._start_log_monitor()
            first = source._log_monitor_task
            source._start_log_monitor()
            second = source._log_monitor_task
            try:
                assert second is first
                await asyncio.gather(first)
                assert started == [True]
            finally:
                source._stop_log_monitor()
                first.cancel()


class TestTheStartThatFails:
    async def test_a_config_that_will_not_load_stops_the_start(self):
        """The config is what names the daemon's API; continuing without it
        starts the daemon and then talks to nothing."""
        systemd = Mock(start=AsyncMock(return_value=True))
        source = SpotifySource({"config_path": "/nonexistent/config.yml"}, systemd_manager=systemd)

        assert await source.start() is False

        systemd.start.assert_not_awaited()
        await source.shutdown()

    async def test_a_service_that_will_not_start_stops_the_start(self, tmp_path):
        """A unit systemd refuses fails the selection: the state says the
        service failed to start, and nothing goes on to talk to a daemon that
        is not there."""
        config = tmp_path / "config.yml"
        config.write_text(
            "server:\n  address: localhost\n  port: 3678\ncrossfade_duration: 0\nexternal_volume: true\n"
        )
        systemd = Mock(start=AsyncMock(return_value=False), stop=AsyncMock(return_value=True))
        machine, _ = make_state_machine()
        source = SpotifySource(
            {"config_path": str(config)}, state_machine=machine, systemd_manager=systemd,
        )
        machine.register_source(AudioSource.SPOTIFY, source)

        with patch("aiohttp.ClientSession") as http:
            assert await machine.transition_to_source(AudioSource.SPOTIFY) is False

        systemd.start.assert_awaited_once_with("milo-spotify.service")
        http.assert_not_called()
        state = machine.get_current_state()
        assert state["service"] == "failed"
        assert state["service_error"]["reason"] == "start_failed"
        assert state["session"] is None
        await source.shutdown()

    async def test_a_crash_mid_start_tears_down_what_was_built(self, source):
        """Half a start leaves an aiohttp session and possibly a WebSocket task
        with no owner; the teardown is what closes them. Here the readiness
        poll meets a failure it does not expect (not a refused connection)."""
        http = MagicMock()
        http.close = AsyncMock()
        http.get = Mock(side_effect=RuntimeError("socket exploded"))

        with patch("aiohttp.ClientSession", return_value=http), \
                patch("backend.sources.spotify.source.LibrespotWebSocket", autospec=True) as ws_cls:
            assert await source.start() is False

        http.close.assert_awaited_once()
        ws_cls.assert_not_called()


class TestTheManagedConfigWrite:
    async def test_a_config_that_will_not_parse_is_logged_and_not_raised(
        self, source, caplog, tmp_path
    ):
        """`_apply_managed_config` runs inside `_do_start` before the daemon
        launches. Raising here would abort a start over a crossfade setting —
        the docstring calls it failing open.

        The file has to exist and be unparsable: an absent path returns at the
        guard above the `try` and never reaches this arm (an earlier guard
        shadowing the mutation, the B5 lesson)."""
        broken = tmp_path / "config.yml"
        broken.write_text("server: [unclosed\n  bad: : :\n")
        source._config_path = str(broken)
        source._get_crossfade_duration = AsyncMock(return_value=0)

        with caplog.at_level("ERROR", logger="source.spotify"):
            await source._apply_managed_config()

        assert "Failed to apply managed go-librespot config" in caplog.text

    async def test_a_config_path_that_is_not_there_is_left_alone(self, source, tmp_path):
        """The guard above the try. A unit whose image build never ran
        provisioning/go-librespot.sh::configure_go_librespot has no file, and writing
        one from here would create a config with none of the baked keys."""
        source._config_path = str(tmp_path / "does-not-exist.yml")
        source._get_crossfade_duration = AsyncMock()

        await source._apply_managed_config()

        source._get_crossfade_duration.assert_not_awaited()
