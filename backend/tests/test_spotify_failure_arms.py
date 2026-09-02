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

Also here: `refresh_metadata`'s two failure arms (it is the ground truth the
reroute reads before pausing), `_send_api_command`'s, the journal bridge's
loop-body guard, and the start path's teardown-on-crash.

The daemon double is the stateful one from `test_spotify_source.py` — /status
reports what the POSTs did to it, so a release that never actually pauses
cannot pass.
"""
import asyncio

import aiohttp
import pytest
from aiohttp.client_reqrep import ConnectionKey
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from backend.sources.spotify.source import SpotifySource


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
def source(config):
    src = SpotifySource(config)
    src._service_manager = Mock()
    src._service_manager.start = AsyncMock(return_value=True)
    src._service_manager.stop = AsyncMock(return_value=True)
    src._service_manager.restart = AsyncMock(return_value=True)
    src._service_manager.is_active = AsyncMock(return_value=True)
    return src


def daemon(source, *, paused=True, post_status=200, get_status=200, get_raises=None):
    """go-librespot's HTTP API — the outside world of this source.

    Stateful on purpose: /status answers what the POSTs did to it, so a release
    that never pauses observes an unpaused daemon. `get_raises` stands for a
    daemon that is not listening at all.
    """
    state = {"paused": paused}

    source._session = MagicMock()
    source._session.close = AsyncMock()
    source._api_url = "http://localhost:3678"

    async def status():
        return {
            "paused": state["paused"],
            "track": {
                "name": "Track", "artist_names": ["Artist"], "album_name": "Album",
                "album_cover_url": None, "duration": 200000, "position": 76611,
            },
        }

    get_response = MagicMock()
    get_response.status = get_status
    get_response.json = AsyncMock(side_effect=status)
    get_cm = AsyncMock()
    if get_raises is not None:
        get_cm.__aenter__.side_effect = get_raises
    else:
        get_cm.__aenter__.return_value = get_response
    source._session.get.return_value = get_cm

    post_response = MagicMock()
    post_response.status = post_status
    post_cm = AsyncMock()
    post_cm.__aenter__.return_value = post_response

    def post(url, json=None):
        command = url.rsplit("/player/", 1)[-1]
        if post_status == 200 and command in ("pause", "resume"):
            state["paused"] = command == "pause"
        return post_cm

    source._session.post = Mock(side_effect=post)
    return source._session


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
        """Before the daemon has ever been reached there is no Connect session
        to preserve, so there is nothing to park — but the unit still has to go
        down, or the reroute proceeds around a source it never released.

        Measured constat: the `not self._session or not self._api_url` guard at
        the top is **inert**, shadowed by the identical guard inside
        `refresh_metadata` one line below — remove it and the next arm falls
        back to the same `super()` anyway, differing only in which warning is
        logged. Left in place (it says the precondition, and B7-15's rule is
        that a change with no reachable effect is worth less than a constat);
        the assertion is on the outcome, which holds either way."""
        source._session = None
        source._api_url = "http://localhost:3678"

        assert await source.release_for_reroute() is True

        source._service_manager.stop.assert_called_once_with("milo-spotify.service")
        assert source._soft_reroute is False

    async def test_a_source_with_no_api_url_stops_outright(self, source):
        source._session = MagicMock()
        source._session.close = AsyncMock()  # the fallback stop awaits it
        source._api_url = None

        assert await source.release_for_reroute() is True

        source._service_manager.stop.assert_called_once_with("milo-spotify.service")

    async def test_a_daemon_that_will_not_answer_status_stops_outright(self, source):
        """The ground truth is read *before* pausing, deliberately: a stale
        cached `_is_playing=False` would skip the pause and hand a live stream
        to a sink that does not rate-limit."""
        daemon(source, get_status=500)

        assert await source.release_for_reroute() is True

        source._service_manager.stop.assert_called_once_with("milo-spotify.service")
        assert source._soft_reroute is False

    async def test_an_unreachable_daemon_stops_outright(self, source):
        daemon(source, get_raises=_refused())

        assert await source.release_for_reroute() is True

        source._service_manager.stop.assert_called_once_with("milo-spotify.service")

    async def test_a_pause_the_daemon_never_confirms_stops_outright(self, source):
        """The output is only parked once the daemon says it stopped pulling
        samples. Parking it on an unconfirmed pause is what runs the track to
        its end in seconds on RELEASE_DEVICE."""
        session = daemon(source, paused=False)
        source._pause_and_confirm = AsyncMock(return_value=False)

        assert await source.release_for_reroute() is True

        assert ("output", {"device": "null"}) not in commands(session)
        source._service_manager.stop.assert_called_once_with("milo-spotify.service")

    async def test_the_soft_flag_is_cleared_before_every_attempt(self, source):
        """`acquire_after_reroute` branches on it. A flag left over from a
        previous successful reroute would make the re-acquire try to reopen an
        output on a daemon that has since been stopped."""
        source._soft_reroute = True
        daemon(source, get_status=500)

        await source.release_for_reroute()

        assert source._soft_reroute is False


class TestConfirmingThePause:
    """`_pause_and_confirm` — the command returns before the player stops."""

    async def test_a_pause_the_daemon_refuses_never_enters_the_poll(self, source):
        """A refused pause has to answer at once, not wait the confirmation out.

        Asserting the poll never starts rather than the return value: with the
        early return removed the loop still times out to False, so the answer
        alone cannot separate the two — the mutation makes the suite *slow*
        instead of red (the B1(d) family). What the two versions really differ
        on is whether the daemon is polled at all."""
        daemon(source, paused=False, post_status=500)
        source.refresh_metadata = AsyncMock(return_value=True)

        assert await source._pause_and_confirm() is False
        source.refresh_metadata.assert_not_awaited()

    async def test_a_daemon_that_stops_answering_mid_wait_is_not_confirmed(
        self, source
    ):
        """A daemon that dies between the pause and the confirmation must not
        be read as "paused" — the reroute would park an output on a process
        that is gone."""
        daemon(source, paused=False)
        source.refresh_metadata = AsyncMock(return_value=False)

        assert await source._pause_and_confirm() is False

    async def test_a_daemon_that_never_reports_paused_times_out(self, source):
        """The bound is what stops a wedged daemon holding the whole multiroom
        toggle open."""
        daemon(source, paused=False)
        source.refresh_metadata = AsyncMock(return_value=True)
        source._is_playing = True

        assert await source._pause_and_confirm(timeout=0.05, interval=0.01) is False

    async def test_a_daemon_that_confirms_late_is_still_confirmed(self, source):
        """The poll has to keep asking rather than answer on the first read:
        go-librespot reports the old state for a tick or two after the command."""
        daemon(source, paused=False)
        answers = [True, True, True]
        source._is_playing = True

        async def refresh():
            if not answers.pop(0):
                return False
            if not answers:
                source._is_playing = False
            return True

        source.refresh_metadata = AsyncMock(side_effect=refresh)

        assert await source._pause_and_confirm(timeout=2.0, interval=0.001) is True


class TestTheReAcquireThatCannotBeConfirmed:
    async def test_a_reopen_the_daemon_refuses_restarts_the_source(
        self, source, monkeypatch
    ):
        """Without the fallback the source reports acquired with its output
        still parked on `null`: Spotify is selected, the phone still shows the
        speaker, and nothing plays."""
        monkeypatch.setenv("MILO_MODE", "direct")
        daemon(source, post_status=500)
        source._soft_reroute = True
        source._reroute_was_playing = False

        with patch.object(source, "start", new_callable=AsyncMock,
                          return_value=True) as start:
            assert await source.acquire_after_reroute() is True

        start.assert_awaited_once()

    async def test_a_resume_the_daemon_refuses_does_not_fail_the_reroute(
        self, source, monkeypatch
    ):
        """Measured asymmetry, and it is the right one: the output is already
        reopened, so the session is back and the owner can press play. Falling
        back to a restart here would throw away a working session over a
        transport command."""
        monkeypatch.setenv("MILO_MODE", "direct")
        session = daemon(source, paused=True)
        source._soft_reroute = True
        source._reroute_was_playing = True

        outputs = {"count": 0}
        real_post = session.post.side_effect

        def post(url, json=None):
            command = url.rsplit("/player/", 1)[-1]
            result = real_post(url, json=json)
            if command == "output":
                outputs["count"] += 1
            return result

        # Reopen succeeds, resume is refused: only the resume answers 500.
        refusing = MagicMock()
        refusing.status = 500
        refusing_cm = AsyncMock()
        refusing_cm.__aenter__.return_value = refusing

        def mixed(url, json=None):
            if url.endswith("/player/resume"):
                return refusing_cm
            return real_post(url, json=json)

        session.post = Mock(side_effect=mixed)

        with patch.object(source, "start", new_callable=AsyncMock) as start:
            assert await source.acquire_after_reroute() is True

        assert [c for c, _ in commands(session)] == ["output", "resume"]
        start.assert_not_awaited()

    async def test_the_soft_flag_is_cleared_so_a_second_reroute_starts_clean(
        self, source, monkeypatch
    ):
        monkeypatch.setenv("MILO_MODE", "direct")
        daemon(source)
        source._soft_reroute = True
        source._reroute_was_playing = False

        await source.acquire_after_reroute()

        assert source._soft_reroute is False


class TestReadingTheDaemonsStatus:
    """`refresh_metadata` — the ground truth the reroute and the handshake read."""

    async def test_a_source_with_no_session_reports_no_refresh(self, source):
        source._session = None

        assert await source.refresh_metadata() is False

    async def test_a_source_with_no_api_url_reports_no_refresh(self, source):
        source._session = MagicMock()
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

    async def test_a_session_with_no_track_empties_the_card(self, source):
        """go-librespot answers 200 with no `track` once the phone disconnects.
        Keeping the previous metadata leaves a track on screen for a session
        that ended."""
        source._session = MagicMock()
        source._api_url = "http://localhost:3678"
        response = MagicMock()
        response.status = 200
        response.json = AsyncMock(return_value={"paused": True})
        cm = AsyncMock()
        cm.__aenter__.return_value = response
        source._session.get.return_value = cm
        source._metadata = {"title": "Breathe"}

        assert await source.refresh_metadata() is True
        assert source._metadata == {}
        assert source._device_connected is False


class TestSendingACommand:
    async def test_a_command_with_no_session_is_refused_not_crashed(self, source):
        source._session = None

        result = await source._send_api_command("pause")

        assert result["success"] is False
        assert "Session not active" in result["error"]

    async def test_a_daemon_that_cannot_be_reached_is_a_refusal(self, source):
        daemon(source)
        source._session.post = Mock(side_effect=_refused())

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

        async def follow(unit, logger=None):
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

        async def follow(unit, logger=None):
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
        async def follow(unit, logger=None):
            raise RuntimeError("journalctl gone")
            yield  # pragma: no cover - generator marker

        with patch("backend.sources.spotify.source.follow_unit", follow):
            with caplog.at_level("ERROR", logger="source.spotify"):
                await source._monitor_logs()

        assert "journalctl gone" in caplog.text

    async def test_a_cancelled_bridge_ends_quietly(self, source, caplog):
        """`_stop_log_monitor` cancels it on every stop, so an error log here
        would put a banner up on an ordinary source switch."""
        async def follow(unit, logger=None):
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

        async def follow(unit, logger=None):
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
    async def test_a_config_that_will_not_load_stops_the_start(self, source):
        """`_load_config` is what sets `_api_url`; continuing without it starts
        the daemon and then talks to nothing."""
        source._load_config = AsyncMock(return_value=False)

        assert await source._do_start() is False

    async def test_a_service_that_will_not_start_stops_the_start(self, source):
        source._load_config = AsyncMock(return_value=True)
        source._apply_managed_config = AsyncMock()
        source._start_service = AsyncMock(return_value=False)

        assert await source._do_start() is False

    async def test_a_crash_mid_start_tears_down_what_was_built(self, source):
        """Half a start leaves an aiohttp session and possibly a WebSocket task
        with no owner; `_cleanup` is what closes them."""
        source._load_config = AsyncMock(return_value=True)
        source._apply_managed_config = AsyncMock()
        source._start_service = AsyncMock(return_value=True)
        source._wait_for_playback_ready = AsyncMock(
            side_effect=RuntimeError("socket exploded")
        )
        source._cleanup = AsyncMock()

        assert await source._do_start() is False
        source._cleanup.assert_awaited_once()
        await source._session.close()  # _cleanup is a double here, so close by hand

    async def test_a_daemon_that_never_answers_is_logged_at_banner_level(
        self, source, caplog
    ):
        """Not fatal — the WS loop reconnects — but the source is about to
        report ACTIVE over a daemon that never answered, so it has to leave a
        trace the owner sees."""
        source._load_config = AsyncMock(return_value=True)
        source._apply_managed_config = AsyncMock()
        source._start_service = AsyncMock(return_value=True)
        source._wait_for_playback_ready = AsyncMock(return_value=False)
        source._start_websocket = AsyncMock()
        source._start_log_monitor = Mock()
        source._update_connection_state = Mock()

        with caplog.at_level("ERROR", logger="source.spotify"):
            assert await source._do_start() is True

        assert "never answered" in caplog.text
        await source._session.close()


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
