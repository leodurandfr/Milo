# backend/tests/test_qobuz_monitor.py
"""`sources/qobuz/monitor.py` and the parts of `source.py` it drives.

`monitor.py` was at **21.4%** — the worst rate of the whole B8a zone — and the
shape is the familiar one: `test_qobuz_source.py` calls `_on_status` directly,
so the mapping is covered while the thing that *produces* a status had never
run. `start`, `stop`, `_loop` and `_fetch_status` were all at zero, i.e. no
qobuz-proxy payload had ever entered this source.

Two things live only here:

* **the speaker match.** qobuz-proxy hard-couples `id = slugify(name)` and the
  display name "Milō" slugifies to `mil`, so the monitor matches on the ALSA
  `audio_device` instead. Match the wrong speaker and Milō renders somebody
  else's playback; match none and the source is permanently idle.
* **`authenticated` staying unknown on a blip.** A non-200 answers `None`, and
  `_on_status` keeps the last value. Flapping it to False makes the idle card
  flash "connect your Qobuz account" every time the sidecar hiccups.

Two rule-5 precautions, both measured rather than assumed:

* `aiohttp.ClientSession` is made to **raise** in every test here. The conftest
  network guard only refuses connections *off this host*, and qobuz-proxy
  listens on localhost — so a double that slipped would poll the real sidecar
  (B4's Navidrome lesson).
* `QOBUZ_VOLUME_FLAG` is redirected to `tmp_path`. It is
  `/var/lib/milo/qobuz/allow_app_volume` on this appliance, and it decides
  whether the Qobuz app may move the volume. The conftest guard already refuses
  the write with EACCES, but a redirect is what lets the content be asserted at
  all.
"""
import asyncio
import contextlib

import pytest
from unittest.mock import AsyncMock, MagicMock, Mock

from backend.sources.qobuz.monitor import QobuzMonitor
from backend.sources.qobuz.source import QobuzSource


@pytest.fixture(autouse=True)
def never_the_real_sidecar(monkeypatch):
    """Any test that loses its double fails instead of polling qobuz-proxy."""
    def refuse(*_a, **_kw):
        raise AssertionError(
            "a test tried to open a real aiohttp session: qobuz-proxy listens "
            "on this host and the network guard only refuses off-host"
        )

    monkeypatch.setattr("backend.sources.qobuz.monitor.aiohttp.ClientSession", refuse)


class Payloads:
    """The `/api/status` bodies qobuz-proxy answers with.

    Bounded on purpose (the B4/B8a lesson): a mutation that removes a loop's
    exit must make the suite RED at once, not grow. `RunawayPoll` derives from
    BaseException so the loop's own `except Exception` cannot swallow it.
    """

    MAX_POLLS = 40

    def __init__(self, *answers, status=200):
        self._answers = list(answers)
        self.status = status
        self.polls = 0

    async def json(self):
        return self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]

    async def __aenter__(self):
        self.polls += 1
        if self.polls > self.MAX_POLLS:
            raise RunawayPoll(f"polled {self.polls} times: the loop lost its exit")
        return self

    async def __aexit__(self, *_exc):
        return False


class RunawayPoll(BaseException):
    """See `Payloads.MAX_POLLS`."""


def session_answering(payloads):
    session = MagicMock()
    session.closed = False
    session.get = Mock(return_value=payloads)
    session.close = AsyncMock()
    return session


def monitor(session, *, audio_device="milo_qobuz", on_status=None, interval=0.001):
    seen = []
    # Set on every delivery, so a test can await the loop's own progress
    # instead of guessing how many event-loop yields it takes. `poll_interval`
    # is a real timer: `asyncio.sleep(0)` hands control back without advancing
    # the clock, so any arm that has to cross that timer is waiting for the
    # wall clock, not for yields -- and a budget of yields outlasts 1 ms on a
    # Pi while finishing well inside it on a CI runner. That is a test that
    # fails on the machines that are *fast*.
    delivered = asyncio.Event()

    async def record(speaker, authenticated):
        seen.append((speaker, authenticated))
        delivered.set()

    mon = QobuzMonitor(
        status_url="http://127.0.0.1:9100/api/status",
        audio_device=audio_device,
        on_status=on_status or record,
        poll_interval=interval,
    )
    mon._session = session
    mon.seen = seen
    mon.delivered = delivered
    return mon


def body(*speakers, authenticated=True):
    """A `/api/status` body in the shape the sidecar really answers with."""
    return {"auth": {"authenticated": authenticated}, "speakers": list(speakers)}


def speaker(device="milo_qobuz", status="playing", **over):
    sp = {"id": "mil", "name": "Milō", "status": status,
          "config": {"audio_device": device}}
    sp.update(over)
    return sp


class TestPickingOurSpeaker:
    """`_fetch_status` — 12 lines, none of which had run."""

    async def test_our_speaker_is_the_one_matched_by_alsa_device(self):
        """Non-triviality plus the rule the module docstring exists for:
        qobuz-proxy slugifies "Milō" to `mil`, so the id cannot identify us —
        the ALSA output device can."""
        mine = speaker(device="milo_qobuz", name="Milō")
        theirs = speaker(device="other_card", name="Living room")
        mon = monitor(session_answering(Payloads(body(theirs, mine))))

        found, authenticated = await mon._fetch_status()

        assert found["name"] == "Milō"
        assert authenticated is True

    async def test_a_single_speaker_proxy_falls_back_to_the_first(self):
        """The sidecar normally publishes exactly one speaker; if its config
        names a device we do not recognise, taking it is better than reporting
        the source permanently idle."""
        only = speaker(device="renamed-after-an-update")
        mon = monitor(session_answering(Payloads(body(only))))

        found, _ = await mon._fetch_status()

        assert found is not None

    async def test_a_proxy_with_no_speakers_reports_none(self):
        mon = monitor(session_answering(Payloads(body())))

        found, authenticated = await mon._fetch_status()

        assert found is None
        assert authenticated is True

    async def test_a_payload_with_no_speakers_key_reports_none(self):
        mon = monitor(session_answering(Payloads({"auth": {"authenticated": True}})))

        assert (await mon._fetch_status())[0] is None

    async def test_a_speaker_with_no_config_block_is_not_a_crash(self):
        """The sidecar omits `config` for a speaker it has not configured yet."""
        mon = monitor(session_answering(Payloads(body({"id": "x", "status": "idle"}))))

        found, _ = await mon._fetch_status()

        assert found == {"id": "x", "status": "idle"}


class TestTheLoginState:
    async def test_a_logged_out_account_is_reported_false(self):
        """This is what raises the idle card's "connect account" CTA."""
        mon = monitor(session_answering(
            Payloads(body(speaker(), authenticated=False))
        ))

        assert (await mon._fetch_status())[1] is False

    async def test_a_payload_with_no_auth_block_reads_as_logged_out(self):
        mon = monitor(session_answering(Payloads({"speakers": [speaker()]})))

        assert (await mon._fetch_status())[1] is False

    async def test_a_non_200_is_not_an_answer_at_all(self):
        """A status that could not be read is not a status, and the two ways it
        can fail must not diverge: a sidecar that is *down* refuses the
        connection and the loop skips the tick, while one that is up and broken
        answers 5xx — and that used to come back as a speaker-less payload, i.e.
        as "no session, logged out". Three ticks of the source's idle grace
        later the full-screen player was replaced by the idle card over playing
        audio, with the "connect your account" CTA on it, at ~1 Hz.
        """
        mon = monitor(session_answering(Payloads(body(speaker()), status=503)))

        assert await mon._fetch_status() is None

    async def test_a_non_200_is_logged_so_a_broken_sidecar_is_visible(self, caplog):
        mon = monitor(session_answering(Payloads(body(speaker()), status=503)))

        with caplog.at_level("WARNING", logger="source.qobuz.monitor"):
            await mon._fetch_status()

        assert "503" in caplog.text


class TestThePollLoop:
    async def test_a_tick_the_sidecar_would_not_answer_reaches_no_one(self):
        """The other half of `test_a_non_200_is_not_an_answer_at_all`: nothing
        is delivered, so the source keeps the state it had — the same outcome
        as the raising path below, which is the point."""
        payloads = Payloads(body(speaker()), status=503)
        mon = monitor(session_answering(payloads))
        mon._running = True

        task = asyncio.create_task(mon._loop())
        for _ in range(200):
            await asyncio.sleep(0)
        mon._running = False
        task.cancel()

        assert mon.seen == []

    async def test_each_tick_hands_the_speaker_to_the_source(self):
        """Nothing had ever travelled from the sidecar into `_on_status`."""
        payloads = Payloads(body(speaker()))
        mon = monitor(session_answering(payloads))
        mon._running = True

        task = asyncio.create_task(mon._loop())
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(mon.delivered.wait(), timeout=5)
        mon._running = False
        task.cancel()

        assert mon.seen[0][0]["name"] == "Milō"

    async def test_a_failing_poll_does_not_kill_the_loop(self, caplog):
        """Background-loop doctrine, and it is the whole reason the source
        survives a sidecar restart: without the body guard one refused poll
        ends the feed and Qobuz stops updating until the source is reselected.
        """
        payloads = Payloads(body(speaker()))
        session = session_answering(payloads)
        calls = {"n": 0}

        def get(_url):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionResetError("sidecar restarted")
            return payloads

        session.get = Mock(side_effect=get)
        mon = monitor(session)
        mon._running = True

        with caplog.at_level("WARNING", logger="source.qobuz.monitor"):
            task = asyncio.create_task(mon._loop())
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(mon.delivered.wait(), timeout=5)
            mon._running = False
            task.cancel()

        assert mon.seen, "the loop died on the first failed poll"
        assert "sidecar restarted" in caplog.text

    async def test_a_callback_that_throws_does_not_kill_the_loop(self):
        async def boom(speaker, authenticated):
            raise RuntimeError("state machine busy")

        mon = monitor(session_answering(Payloads(body(speaker()))), on_status=boom)
        mon._running = True

        task = asyncio.create_task(mon._loop())
        for _ in range(200):
            await asyncio.sleep(0)
        running = not task.done()
        mon._running = False
        task.cancel()

        assert running

    async def test_cancelling_the_loop_is_not_swallowed_as_an_error(self):
        """`stop()` cancels it. Catching `CancelledError` in the body guard
        would make the task refuse to die and `stop()` hang on the await.

        Measured constat: the explicit `except asyncio.CancelledError: raise`
        arm is **inert on its own**. Replacing the `raise` with `pass` still
        ends the task — asyncio keeps `_must_cancel` set and re-delivers the
        cancellation at the next suspension point — so no assertion can
        separate the two versions. Same family as B5, where the pattern was
        measured inert three times in one unit. Kept: it states that the arm
        below must not swallow a cancel, and the test asserts the behaviour,
        which holds either way."""
        mon = monitor(session_answering(Payloads(body(speaker()))))
        mon._running = True
        task = asyncio.create_task(mon._loop())
        await asyncio.sleep(0)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_clearing_the_flag_ends_the_loop(self):
        mon = monitor(session_answering(Payloads(body(speaker()))))
        mon._running = False

        await asyncio.wait_for(mon._loop(), timeout=1)


class TestStartAndStop:
    async def test_starting_twice_does_not_open_a_second_session(self, monkeypatch):
        """Two poll loops against one sidecar is two callbacks per tick into a
        source that treats each as a fresh snapshot."""
        made = []
        monkeypatch.setattr(
            "backend.sources.qobuz.monitor.aiohttp.ClientSession",
            lambda **kw: made.append(kw) or MagicMock(closed=False, close=AsyncMock()),
        )
        mon = QobuzMonitor("http://127.0.0.1:9100/api/status", "milo_qobuz",
                           AsyncMock(), poll_interval=10)
        mon._session = None

        await mon.start()
        first = mon._task
        await mon.start()

        assert mon._task is first
        assert len(made) == 1
        await mon.stop()

    async def test_the_request_timeout_is_bounded(self, monkeypatch):
        """An unresponsive proxy must not wedge the loop; a slow tick just
        retries next interval."""
        made = []
        monkeypatch.setattr(
            "backend.sources.qobuz.monitor.aiohttp.ClientSession",
            lambda **kw: made.append(kw) or MagicMock(closed=False, close=AsyncMock()),
        )
        mon = QobuzMonitor("http://127.0.0.1:9100/api/status", "milo_qobuz",
                           AsyncMock(), poll_interval=10)

        await mon.start()
        await mon.stop()

        assert made[0]["timeout"].total == 3.0

    async def test_stop_cancels_the_loop_and_closes_the_session(self, monkeypatch):
        """A session left open is an aiohttp warning per source switch and a
        socket that outlives the source."""
        session = MagicMock(closed=False)
        session.close = AsyncMock()
        monkeypatch.setattr(
            "backend.sources.qobuz.monitor.aiohttp.ClientSession",
            lambda **kw: session,
        )
        mon = QobuzMonitor("http://127.0.0.1:9100/api/status", "milo_qobuz",
                           AsyncMock(), poll_interval=10)

        await mon.start()
        task = mon._task

        await mon.stop()

        assert task.done()
        assert mon._task is None
        assert mon._session is None
        session.close.assert_awaited_once()

    async def test_stop_clears_the_running_flag_first(self, monkeypatch):
        """The loop tests its flag between ticks; clearing it after the cancel
        would leave a tick in flight able to publish over a stopped source."""
        session = MagicMock(closed=False)
        session.close = AsyncMock()
        monkeypatch.setattr(
            "backend.sources.qobuz.monitor.aiohttp.ClientSession",
            lambda **kw: session,
        )
        mon = QobuzMonitor("http://127.0.0.1:9100/api/status", "milo_qobuz",
                           AsyncMock(), poll_interval=10)
        await mon.start()

        await mon.stop()

        assert mon._running is False

    async def test_stopping_a_monitor_that_never_started_is_harmless(self):
        """`_cleanup` calls it on every source stop, started or not."""
        mon = QobuzMonitor("http://127.0.0.1:9100/api/status", "milo_qobuz",
                           AsyncMock())

        await mon.stop()

        assert mon._task is None


@pytest.fixture
def flag(tmp_path, monkeypatch):
    """Redirect the volume-policy flag away from the live appliance.

    `/var/lib/milo/qobuz/allow_app_volume` decides whether the Qobuz app may
    move this speaker's volume. The conftest guard already refuses the write
    with EACCES; redirecting is what makes the content assertable.
    """
    path = tmp_path / "allow_app_volume"
    monkeypatch.setattr("backend.sources.qobuz.source.QOBUZ_VOLUME_FLAG", path)
    return path


@pytest.fixture
def source(flag):
    src = QobuzSource({})
    src._service_manager = Mock()
    src._service_manager.start = AsyncMock(return_value=True)
    src._service_manager.stop = AsyncMock(return_value=True)
    src._service_manager.is_active = AsyncMock(return_value=True)
    src._settings_service = Mock()
    src._settings_service.get_setting = AsyncMock(
        return_value={"allow_app_volume": False}
    )
    src.emit_connection_state = Mock()
    return src


class TestTheVolumePolicyFlag:
    """CamillaDSP is the appliance's only attenuation stage; this flag is what
    decides whether the Qobuz app is allowed to touch the level at all."""

    def test_allowing_the_app_writes_one(self, source, flag):
        source._write_volume_flag(True)

        assert flag.read_text() == "1"

    def test_refusing_the_app_writes_zero(self, source, flag):
        source._write_volume_flag(False)

        assert flag.read_text() == "0"

    def test_a_write_that_fails_leaves_the_source_running(self, source, monkeypatch,
                                                          caplog, tmp_path):
        """Fail-open by design: the sidecar's own default is unity, so a flag
        that cannot be written is safe. Failing the start over it would take
        Qobuz down for a file permission."""
        monkeypatch.setattr(
            "backend.sources.qobuz.source.QOBUZ_VOLUME_FLAG",
            tmp_path / "no-such-dir" / "allow_app_volume",
        )

        with caplog.at_level("WARNING", logger="source.qobuz"):
            source._write_volume_flag(True)

        assert "volume-policy flag" in caplog.text

    async def test_the_stored_setting_is_what_reaches_the_flag(self, source, flag):
        source._settings_service.get_setting = AsyncMock(
            return_value={"allow_app_volume": True}
        )

        await source._sync_volume_flag()

        assert flag.read_text() == "1"
        assert source._settings_service.get_setting.await_args.args[0] == "qobuz"

    async def test_a_stored_refusal_pins_unity(self, source, flag):
        """The negative half of the pair. Without it, `allowed = True` is
        indistinguishable from reading the setting — the 8th blind spot, a
        collision of values — and the appliance would hand volume control to
        the Qobuz app for every owner who turned it off."""
        source._settings_service.get_setting = AsyncMock(
            return_value={"allow_app_volume": False}
        )

        await source._sync_volume_flag()

        assert flag.read_text() == "0"

    async def test_a_source_with_no_settings_service_pins_unity(self, source, flag):
        """On a dev host the setter is never injected. Defaulting to "allowed"
        would let a remote app move a level Milō believes it owns."""
        source._settings_service = None

        await source._sync_volume_flag()

        assert flag.read_text() == "0"

    async def test_unlocking_is_honoured_without_bouncing_the_sidecar(self, source,
                                                                     flag):
        """The running stream re-reads the flag on the next app volume command,
        so unlocking is live — restarting would drop the Connect session for
        nothing."""
        assert await source.on_allow_app_volume_changed(True) is True

        assert flag.read_text() == "1"
        source._service_manager.restart.assert_not_called()

    async def test_locking_bounces_a_running_sidecar(self, source, flag):
        """Locking must reset an already-lowered stream to unity at once, and
        only a restart forces that. Without it the speaker stays at whatever
        level the app left it at, while the UI says Milō owns the volume."""
        source._is_service_active = AsyncMock(return_value=True)
        source._restart_service_and_wait = AsyncMock(return_value=True)

        assert await source.on_allow_app_volume_changed(False) is True

        assert flag.read_text() == "0"
        source._restart_service_and_wait.assert_awaited_once()

    async def test_locking_does_not_bounce_a_stopped_sidecar(self, source, flag):
        """There is no stream to reset, and starting one here would bring Qobuz
        up on a source nobody selected."""
        source._is_service_active = AsyncMock(return_value=False)
        source._restart_service_and_wait = AsyncMock(return_value=True)

        assert await source.on_allow_app_volume_changed(False) is True

        source._restart_service_and_wait.assert_not_awaited()


class TestTheSourceBoot:
    async def test_the_flag_is_written_before_the_sidecar_starts(self, source, flag):
        """Order is the assertion: qobuz-proxy reads the flag on its first
        volume command, and a flag written after the start is a race the
        sidecar can lose."""
        order = []
        source._write_volume_flag = Mock(side_effect=lambda a: order.append("flag"))
        source._start_service_and_wait = AsyncMock(
            side_effect=lambda: order.append("service") or True
        )
        source._monitor = None

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("backend.sources.qobuz.source.QobuzMonitor",
                       lambda **kw: MagicMock(start=AsyncMock()))
            assert await source._do_start() is True

        assert order == ["flag", "service"]

    async def test_a_sidecar_that_will_not_start_stops_the_boot(self, source):
        source._start_service_and_wait = AsyncMock(return_value=False)

        assert await source._do_start() is False

    async def test_the_monitor_is_pointed_at_our_own_alsa_device(self, source):
        """The match key. Handing it the wrong device makes the source either
        render another speaker's playback or stay idle forever."""
        made = {}
        source._start_service_and_wait = AsyncMock(return_value=True)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("backend.sources.qobuz.source.QobuzMonitor",
                       lambda **kw: made.update(kw) or MagicMock(start=AsyncMock()))
            await source._do_start()

        assert made["audio_device"] == source._audio_device
        assert made["status_url"] == source._status_url
        assert made["on_status"] == source._on_status

    async def test_a_crash_mid_start_tears_down_what_was_built(self, source):
        source._start_service_and_wait = AsyncMock(
            side_effect=RuntimeError("systemd busy")
        )
        source._cleanup = AsyncMock()

        assert await source._do_start() is False
        source._cleanup.assert_awaited_once()

    async def test_stopping_the_source_stops_the_poll(self, source):
        """The loop runs at ~1 Hz for as long as it lives; leaving it behind
        polls a sidecar the source no longer owns, forever."""
        stopped = AsyncMock()
        source._monitor = MagicMock(stop=stopped)

        await source._cleanup()

        stopped.assert_awaited_once()
        assert source._monitor is None

    async def test_stopping_clears_the_playback_state(self, source):
        source._monitor = None
        source._device_connected = True
        source._idle_ticks = 3
        source._trackless_ticks = 2

        await source._cleanup()

        assert source._device_connected is False
        assert (source._idle_ticks, source._trackless_ticks) == (0, 0)
