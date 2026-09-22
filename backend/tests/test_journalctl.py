# backend/tests/test_journalctl.py
"""Tests for the shared journalctl helpers (follow_unit / read_unit)."""
import asyncio
import contextlib
import logging

import pytest

from backend.shared import journalctl


@contextlib.contextmanager
def caplog_at(name):
    """Collect records from one named logger.

    `caplog` alone is not enough for a logger whose own level has been raised
    elsewhere in the process, and `backend/main.py` raises several at import.
    """
    records = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Collect()
    logger = logging.getLogger(name)
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


class _FakeFollowStdout:
    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""  # EOF


class _FakeFollowProc:
    """journalctl. `exit_code` is what `wait()` reaps, and the default is the
    negative one a terminated process carries — the case the follow's own
    teardown produces, and the one systemd's cgroup kill produces too."""

    def __init__(self, lines, exit_code: int = -15):
        self.stdout = _FakeFollowStdout(lines)
        self.returncode = None
        self.terminated = False
        self._exit_code = exit_code

    def terminate(self):
        self.terminated = True

    async def wait(self):
        self.returncode = self._exit_code
        return self.returncode


class _FakeCommProc:
    def __init__(self, stdout: bytes, returncode: int = 0):
        self._stdout = stdout
        self.returncode = returncode
        self.killed = False

    async def communicate(self):
        return self._stdout, b""

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


class TestFollowUnit:
    @pytest.mark.asyncio
    async def test_yields_decoded_stripped_nonempty_lines(self, monkeypatch):
        proc = _FakeFollowProc([b"  hello \n", b"world\n", b"   \n", b""])

        async def fake_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        out = [line async for line in journalctl.follow_unit(
            "milo-x", consequence="nothing real"
        )]

        assert out == ["hello", "world"]  # blank line skipped, EOF ends iteration
        # Reaped rather than terminated: journalctl exited on its own, so the
        # EOF branch waits for it and the finally block has nothing left to kill.
        assert proc.returncode is not None
        assert proc.terminated is False

    @pytest.mark.asyncio
    async def test_terminates_on_early_close(self, monkeypatch):
        proc = _FakeFollowProc([b"a\n", b"b\n", b"c\n", b""])

        async def fake_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        gen = journalctl.follow_unit("milo-x", consequence="nothing real")
        assert await gen.__anext__() == "a"
        await gen.aclose()  # consumer stops early

        assert proc.terminated is True


class TestAFollowThatDies:
    """A journalctl that exits takes a source's only connection feed with it.

    Consumers: `sources/mac/source.py::_monitor_events` (ROC connect/disconnect)
    and `sources/spotify/source.py::_monitor_logs` (go-librespot errors). Before
    this, the EOF break was silent — the source went deaf for the rest of the
    session with nothing in the journal to say so.
    """

    async def test_an_exit_is_reported_once_with_the_unit_and_the_cost(
        self, monkeypatch
    ):
        async def fake_exec(*args, **kwargs):
            return _FakeFollowProc([b"a\n"], exit_code=1)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        with caplog_at("test.journal") as records:
            async for _ in journalctl.follow_unit(
                "milo-mac",
                consequence="Mac connection detection is down",
                logger=logging.getLogger("test.journal"),
            ):
                pass

        errors = [r for r in records if r.levelno >= logging.ERROR]
        assert len(errors) == 1
        message = errors[0].getMessage()
        assert "milo-mac" in message
        assert "Mac connection detection is down" in message

    async def test_a_journalctl_killed_by_a_signal_reports_nothing(self, monkeypatch):
        """`systemctl restart milo-backend` is not a dead feed.

        milo-backend.service runs KillMode=control-group, so the restart
        SIGTERMs the journalctl children in the same cgroup and they reach the
        EOF branch before uvicorn's shutdown cancels the monitor that owns the
        generator (measured on the unit: returncode -15). Without this, every
        restart wrote a false "connection detection is down" to errors.log and
        raised the UI banner — the exact noise this report exists to replace.
        """
        async def fake_exec(*args, **kwargs):
            return _FakeFollowProc([b"a\n"], exit_code=-15)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        with caplog_at("test.journal") as records:
            async for _ in journalctl.follow_unit(
                "milo-mac",
                consequence="Mac connection detection is down",
                logger=logging.getLogger("test.journal"),
            ):
                pass

        assert [r for r in records if r.levelno >= logging.ERROR] == []

    async def test_a_consumer_that_stops_early_reports_nothing(self, monkeypatch):
        """The one that earns this file's keep.

        A generator closed by its consumer is an ordinary source stop, which
        happens on every source switch. Reporting there would put an error
        banner on the screen every time the owner changes source — and the
        report would be false: nothing died.
        """
        async def fake_exec(*args, **kwargs):
            return _FakeFollowProc([b"a\n", b"b\n", b""])

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        with caplog_at("test.journal") as records:
            gen = journalctl.follow_unit(
                "milo-mac",
                consequence="Mac connection detection is down",
                logger=logging.getLogger("test.journal"),
            )
            assert await gen.__anext__() == "a"
            await gen.aclose()

        assert [r for r in records if r.levelno >= logging.ERROR] == []


class TestReadUnit:
    @pytest.mark.asyncio
    async def test_filters_drop_substrings_and_blanks(self, monkeypatch):
        proc = _FakeCommProc(b"line1\n[trc] noise\nline2\n\n")

        async def fake_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        out = await journalctl.read_unit("milo-x", drop_substrings=("[trc]",))

        assert out == ["line1", "line2"]

    @pytest.mark.asyncio
    async def test_nonzero_exit_returns_empty(self, monkeypatch):
        proc = _FakeCommProc(b"whatever\n", returncode=1)

        async def fake_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        assert await journalctl.read_unit("milo-x") == []

    @pytest.mark.asyncio
    async def test_keep_last_trims_after_filtering(self, monkeypatch):
        proc = _FakeCommProc(b"a\n[trc] x\nb\nc\nd\n")

        async def fake_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        # [trc] dropped -> [a, b, c, d]; keep_last=2 -> last 2 survivors
        out = await journalctl.read_unit(
            "milo-x", drop_substrings=("[trc]",), keep_last=2
        )
        assert out == ["c", "d"]


class _HangingCommProc:
    """A journalctl that never answers.

    `kill` is a plain method rather than an async one: `Process.kill` is
    synchronous, and an async double would let the production `proc.kill()`
    build a coroutine nobody awaits — the test would pass while the real child
    kept reading the journal.
    """

    def __init__(self):
        self.returncode = None
        self.killed = False
        self.waited = False

    async def communicate(self):
        await asyncio.sleep(3600)

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True
        self.returncode = -9
        return self.returncode


class TestReadUnitArguments:
    """argv is the whole surface here: no shell, so every filter is a flag.

    `-n` and `--since` are what keep a read of a unit that has been up for weeks
    from returning the entire journal into memory. Both were at zero.
    """

    @staticmethod
    def _capture(monkeypatch, stdout=b"line\n"):
        seen = {}

        async def fake_exec(*args, **kwargs):
            seen["argv"] = args
            return _FakeCommProc(stdout)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        return seen

    async def test_the_base_read_is_unprivileged_and_unpaged(self, monkeypatch):
        """`--no-pager` matters: without a tty journalctl still buffers through
        its pager and `communicate()` would wait on a process expecting a reader.
        And no sudo — the `milo` user reads its own units, and the sudoers policy
        grants nothing for journalctl.
        """
        seen = self._capture(monkeypatch)

        await journalctl.read_unit("milo-backend")

        assert seen["argv"] == (
            "journalctl", "-u", "milo-backend", "--no-pager", "-o", "cat",
        )

    async def test_a_tail_becomes_the_n_flag(self, monkeypatch):
        """Without it the read is the unit's whole journal — up to the 2 MB
        rotation, into a list, on a Pi."""
        seen = self._capture(monkeypatch)

        await journalctl.read_unit("milo-backend", tail=50)

        assert seen["argv"][-2:] == ("-n", "50")

    async def test_a_since_becomes_the_since_flag(self, monkeypatch):
        """The window a diagnostic read asks for. Dropped, the caller silently
        gets the whole journal and its own `keep_last` trims from the wrong end.
        """
        seen = self._capture(monkeypatch)

        await journalctl.read_unit("milo-backend", since="-2 hours")

        assert seen["argv"][-2:] == ("--since", "-2 hours")

    async def test_tail_and_since_are_both_passed(self, monkeypatch):
        seen = self._capture(monkeypatch)

        await journalctl.read_unit("milo-backend", tail=10, since="today")

        assert seen["argv"][-4:] == ("-n", "10", "--since", "today")

    async def test_a_zero_tail_is_still_sent(self, monkeypatch):
        """The guard is `is not None`, not truthiness. `-n 0` is a legitimate
        request (headers only) and a falsy check would turn it into "no limit" —
        the exact opposite.
        """
        seen = self._capture(monkeypatch)

        await journalctl.read_unit("milo-backend", tail=0)

        assert seen["argv"][-2:] == ("-n", "0")


class TestReadUnitTimeout:
    """A journalctl that will not come back."""

    async def test_a_wedged_read_is_killed_reaped_and_answers_empty(
        self, monkeypatch, caplog
    ):
        """Callers render this list; `[]` is "nothing to show", which is the only
        safe answer. Without the kill the child survives the request, and without
        the `wait()` it survives as a zombie — one per timed-out read, in a
        process that runs for weeks.
        """
        proc = _HangingCommProc()

        async def fake_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        with caplog.at_level(logging.ERROR):
            result = await journalctl.read_unit(
                "milo-backend", timeout=0.05, logger=logging.getLogger("test.journal")
            )

        assert result == []
        assert proc.killed is True
        assert proc.waited is True
        assert "Timeout reading journalctl for milo-backend" in caplog.text

    async def test_a_caller_without_a_logger_still_gets_the_empty_list(
        self, monkeypatch
    ):
        """`logger` is optional and several callers pass none; an unguarded
        `logger.error` would raise inside the timeout arm and turn a slow read
        into an exception the caller never expected.
        """
        proc = _HangingCommProc()

        async def fake_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        assert await journalctl.read_unit("milo-backend", timeout=0.05) == []
        assert proc.killed is True


class TestFollowUnitAnnouncement:
    async def test_the_follow_says_which_unit_it_attached_to(self, monkeypatch):
        """The only trace a follow leaves. Two sources follow their own units,
        and without this line a follow attached to the wrong one is invisible.
        """
        async def fake_exec(*args, **kwargs):
            return _FakeFollowProc([b"a\n"])

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        with caplog_at("test.journal") as records:
            async for _ in journalctl.follow_unit(
                "milo-mac",
                consequence="nothing real",
                logger=logging.getLogger("test.journal"),
            ):
                pass

        assert any("journalctl follow started for milo-mac" in r.getMessage() for r in records)
