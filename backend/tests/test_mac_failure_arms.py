# backend/tests/test_mac_failure_arms.py
"""What the Mac (ROC) source does when avahi or the journal misbehaves.

`test_mac_source.py` drives the happy resolutions and the log classification.
What was uncovered at 39ff9daf is everything underneath: `_run_avahi`'s four
failure arms (12 lines, none executed), `_avahi_reverse`'s bad-address guard,
the journal monitor's loop-body guard, and the start path's two refusals.

Why the avahi arms matter here specifically: this source's only job in the UI
is to name the Mac that is streaming. `AudioSourceStatus` renders that name and
nothing else — Family A has no metadata — so every one of these arms decides
between "Mac mini de Léo" and a bare IP on the card. And the resolution runs on
every connect, so a spawn that hangs delays the card by its full timeout.

Every test here makes the spawn **fail or answer from a double**: `avahi-resolve`
and `avahi-browse` are on the appliance probe's deny-list, and the real ones
would query this LAN.
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, Mock, patch

from backend.sources.mac.mdns import _normalize_address
from backend.sources.mac.source import MacSource


@pytest.fixture
def source():
    src = MacSource({"rtp_port": 10001, "rs8m_port": 10002,
                     "rtcp_port": 10003, "audio_output": "hw:1,0"})
    src._service_manager = Mock()
    src._service_manager.start = AsyncMock(return_value=True)
    src._service_manager.stop = AsyncMock(return_value=True)
    src._service_manager.is_active = AsyncMock(return_value=True)
    src.emit_connection_state = Mock()
    return src


def spawn_that(*, raises=None, stdout=b"", returncode=0, hangs=False):
    """A stand-in for `asyncio.create_subprocess_exec`.

    `hangs` models an avahi that never answers: `communicate()` waits on an
    event nothing sets, which is what the production `wait_for` is there to
    bound.
    """
    proc = Mock()
    proc.returncode = returncode
    proc.kill = Mock()
    proc.wait = AsyncMock()
    if hangs:
        async def never():
            await asyncio.Event().wait()
        proc.communicate = never
    else:
        proc.communicate = AsyncMock(return_value=(stdout, b""))

    async def _exec(*_args, **_kwargs):
        if raises is not None:
            raise raises
        return proc

    _exec.proc = proc
    return _exec


class TestRunningAnAvahiQuery:
    """`_run_avahi` — 12 lines, none of which had ever executed."""

    async def test_a_successful_query_answers_its_stripped_output(self, source):
        """Non-triviality: every refusal below is the arm and not a broken
        double."""
        with patch("asyncio.create_subprocess_exec",
                   new=spawn_that(stdout=b"192.168.1.21\tmac-mini.local\n")):
            out = await source._run_avahi(["avahi-resolve", "-a", "192.168.1.21"])

        assert out == "192.168.1.21\tmac-mini.local"

    async def test_avahi_not_being_installed_is_logged_and_survivable(
        self, source, caplog
    ):
        """A dev host has no avahi-utils. Fail-open is the appliance rule: the
        client still registers, under its bare IP."""
        with patch("asyncio.create_subprocess_exec",
                   new=spawn_that(raises=FileNotFoundError("avahi-resolve"))):
            with caplog.at_level("ERROR", logger="source.mac"):
                assert await source._run_avahi(["avahi-resolve", "-a", "1.2.3.4"]) is None

        assert "not installed" in caplog.text

    async def test_a_spawn_that_fails_transiently_falls_back(self, source, caplog):
        """EMFILE/ENOMEM under load. The connect must still register the
        client rather than raise into the journal monitor."""
        with patch("asyncio.create_subprocess_exec",
                   new=spawn_that(raises=OSError(12, "Cannot allocate memory"))):
            with caplog.at_level("WARNING", logger="source.mac"):
                assert await source._run_avahi(["avahi-resolve", "-a", "1.2.3.4"]) is None

        assert "spawn failed" in caplog.text

    async def test_an_avahi_that_never_answers_is_killed_and_reaped(self, source):
        """The bound, and the reap. A ROC connect runs this on the audio path's
        own event loop; unbounded it would hold the card blank. Reaping the
        killed child is what closes its transport — without it asyncio warns
        about a subprocess still running, once per connect."""
        spawn = spawn_that(hangs=True)

        # The production bound is 5 s and nothing asserts its value, so it is
        # collapsed here rather than waited out: a mutation campaign pays that
        # 5 s on every cycle, and the B1(d) rule is to reduce a wall-clock bound
        # in the fixture as soon as nothing depends on it. What is under test is
        # the handler, not the duration.
        async def expire(coro, timeout):
            coro.close()
            raise asyncio.TimeoutError

        with patch("asyncio.create_subprocess_exec", new=spawn), \
             patch("backend.sources.mac.source.asyncio.wait_for", expire):
            result = await source._run_avahi(["avahi-resolve", "-a", "1.2.3.4"])

        assert result is None
        spawn.proc.kill.assert_called_once()
        spawn.proc.wait.assert_awaited_once()

    async def test_a_non_zero_exit_answers_nothing_even_with_output(self, source):
        """avahi-resolve prints its usage to stdout and exits non-zero. Reading
        the text without the status is the 14th blind spot; here the status is
        the authority."""
        with patch("asyncio.create_subprocess_exec",
                   new=spawn_that(stdout=b"Failed to resolve\n", returncode=1)):
            assert await source._run_avahi(["avahi-resolve", "-a", "1.2.3.4"]) is None


class TestTheReverseLookupGuard:
    async def test_an_address_that_is_not_an_ip_never_reaches_avahi(
        self, source, caplog
    ):
        """The IP comes out of a journal line matched by a regex. A malformed
        one must be refused here rather than handed to a subprocess as an
        argument."""
        spawn = spawn_that(stdout=b"")

        with patch("asyncio.create_subprocess_exec", new=spawn):
            with caplog.at_level("DEBUG", logger="source.mac"):
                assert await source._avahi_reverse("not-an-ip") is None

        assert "Bad IP for mDNS reverse" in caplog.text

    async def test_an_ipv6_query_asks_avahi_for_ipv6(self, source):
        """`avahi-resolve` needs `-6` told to it; without the flag it answers
        nothing for a v6 sender and the card falls back to the raw address."""
        captured = {}

        async def _exec(*args, **kwargs):
            captured["argv"] = list(args)
            proc = Mock(returncode=0)
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        with patch("asyncio.create_subprocess_exec", new=_exec):
            await source._avahi_reverse("fe80::1%eth0")

        assert captured["argv"][1] == "-6"

    async def test_a_link_local_v6_address_carries_its_scope(self, source):
        """A link-local address without a scope is unroutable, and avahi
        refuses it. The source's configured interface is the scope."""
        captured = {}

        async def _exec(*args, **kwargs):
            captured["argv"] = list(args)
            proc = Mock(returncode=0)
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        source.network_interface = "eth0"

        with patch("asyncio.create_subprocess_exec", new=_exec):
            await source._avahi_reverse("fe80::1")

        assert captured["argv"][-1] == "fe80::1%eth0"

    async def test_an_answer_with_no_name_is_not_a_name(self, source):
        with patch("asyncio.create_subprocess_exec", new=spawn_that(stdout=b"1.2.3.4\n")):
            assert await source._avahi_reverse("1.2.3.4") is None


class TestTheForwardLookup:
    async def test_a_name_and_its_address_are_both_returned(self, source):
        """The address is what bridges a Mac that streams ROC from one
        interface and advertises Bonjour on another."""
        with patch("asyncio.create_subprocess_exec",
                   new=spawn_that(stdout=b"mac-mini.local\t192.168.1.21\n")):
            assert await source._avahi_forward("mac-mini.local") == (
                "mac-mini.local", "192.168.1.21"
            )

    async def test_a_name_with_no_address_still_answers_the_name(self, source):
        with patch("asyncio.create_subprocess_exec",
                   new=spawn_that(stdout=b"mac-mini.local\n")):
            assert await source._avahi_forward("mac-mini.local") == (
                "mac-mini.local", None
            )

    async def test_a_query_that_answered_nothing_is_two_nones(self, source):
        with patch("asyncio.create_subprocess_exec",
                   new=spawn_that(stdout=b"", returncode=1)):
            assert await source._avahi_forward("mac-mini.local") == (None, None)


class TestNormalisingAnAddress:
    """`mdns._normalize_address` — the ValueError arm had never run."""

    def test_a_bracketed_scoped_v6_address_is_canonicalised(self):
        assert _normalize_address("[fe80::1%eth0]") == "fe80::1"

    def test_a_v4_address_is_returned_as_it_stands(self):
        assert _normalize_address("192.168.1.21") == "192.168.1.21"

    def test_a_scope_suffix_is_dropped_from_a_v6_address(self):
        """`avahi-browse` prints the scope; the ROC sender's address does not
        carry one, so the two only compare equal once it is off."""
        assert _normalize_address("fe80::1%eth0") == "fe80::1"

    def test_a_hostname_is_not_an_address(self):
        """`avahi-browse` puts a name in the address column for a record it
        could not resolve; matching on it would attribute someone else's
        Bonjour name to this Mac."""
        assert _normalize_address("mac-mini.local") is None

    def test_an_empty_address_is_not_an_address(self):
        assert _normalize_address("") is None
        assert _normalize_address(None) is None


class TestTheJournalMonitor:
    async def test_every_line_reaches_the_classifier(self, source):
        seen = []

        async def follow(unit, logger=None):
            for line in ["a", "b"]:
                yield line

        source._process_log_line = AsyncMock(side_effect=lambda ln: seen.append(ln))

        with patch("backend.sources.mac.source.follow_unit", follow):
            await source._monitor_events()

        assert seen == ["a", "b"]

    async def test_a_line_that_throws_costs_only_that_line(self, source, caplog):
        """Background-loop doctrine. Without the body guard one unparsable line
        ends the monitor, and no Mac connect or disconnect is seen again until
        the source is restarted — with nothing on screen to say so."""
        seen = []

        async def follow(unit, logger=None):
            for line in ["bad", "good"]:
                yield line

        async def handle(line):
            seen.append(line)
            if line == "bad":
                raise ValueError("unparsable")

        source._process_log_line = handle

        with patch("backend.sources.mac.source.follow_unit", follow):
            with caplog.at_level("ERROR", logger="source.mac"):
                await source._monitor_events()

        assert seen == ["bad", "good"]

    async def test_the_journal_going_away_is_logged_not_raised(self, source, caplog):
        """The monitor is a bare task; an exception escaping it dies unobserved
        except for asyncio's own warning."""
        async def follow(unit, logger=None):
            raise RuntimeError("journalctl gone")
            yield  # pragma: no cover - generator marker

        with patch("backend.sources.mac.source.follow_unit", follow):
            with caplog.at_level("ERROR", logger="source.mac"):
                await source._monitor_events()

        assert "journalctl gone" in caplog.text

    async def test_a_cancelled_monitor_ends_quietly(self, source, caplog):
        """`_do_stop` cancels it on every source switch; an error log here
        would raise the UI banner on an ordinary switch away from Mac."""
        async def follow(unit, logger=None):
            raise asyncio.CancelledError
            yield  # pragma: no cover - generator marker

        with patch("backend.sources.mac.source.follow_unit", follow):
            with caplog.at_level("ERROR", logger="source.mac"):
                await source._monitor_events()

        assert caplog.records == []


class TestTheSourceBoot:
    async def test_a_service_that_will_not_start_stops_the_boot(self, source):
        source._start_service_and_wait = AsyncMock(return_value=False)

        assert await source._do_start() is False

    async def test_a_service_that_died_during_its_settle_stops_the_boot(
        self, source, caplog
    ):
        """The second check is not redundant: `_start_service_and_wait` returns
        on systemd's acknowledgement, and roc-recv can exit during the settle
        (a port already bound). Reporting started over that gives the Mac a
        receiver that is not listening."""
        source._start_service_and_wait = AsyncMock(return_value=True)
        source._is_service_active = AsyncMock(return_value=False)
        # Everything past the guard is doubled, not left to the real thing.
        # Measured: without this, removing the guard falls through to
        # `_monitor_events`, which is spawned as a TASK — so it outlives any
        # `patch()` window the test could wrap `_do_start` in, reaches the real
        # `follow_unit`, and follows the LIVE journal forever. The mutation then
        # HANGS the campaign instead of reddening it, and spawns a real
        # journalctl against the running unit (rule 5). Doubling the coroutine
        # itself is what closes both.
        source._check_initial_state = AsyncMock()
        source._monitor_events = AsyncMock()

        try:
            with caplog.at_level("ERROR", logger="source.mac"):
                assert await source._do_start() is False
        finally:
            if source._monitor_task:
                source._monitor_task.cancel()

        assert "not active after start" in caplog.text

    async def test_a_crash_during_boot_is_reported_not_raised(self, source, caplog):
        source._start_service_and_wait = AsyncMock(return_value=True)
        source._is_service_active = AsyncMock(return_value=True)
        source._check_initial_state = AsyncMock(side_effect=RuntimeError("journal gone"))

        with caplog.at_level("ERROR", logger="source.mac"):
            assert await source._do_start() is False

        assert "Start failed" in caplog.text

    async def test_stopping_cancels_the_monitor_and_drops_it(self, source):
        """Left behind, it keeps following the journal of a source nobody
        selected — and publishes connects into a state machine that has moved
        on."""
        async def forever():
            await asyncio.Event().wait()

        source._monitor_task = asyncio.create_task(forever())
        source._stop_service = AsyncMock(return_value=True)
        task = source._monitor_task

        assert await source._do_stop() is True

        assert task.cancelled()
        assert source._monitor_task is None

    async def test_stopping_a_source_that_never_started_still_stops_the_unit(
        self, source
    ):
        source._monitor_task = None
        source._stop_service = AsyncMock(return_value=True)

        assert await source._do_stop() is True
        source._stop_service.assert_awaited_once()
