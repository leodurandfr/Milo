# backend/tests/test_journalctl.py
"""Tests for the shared journalctl follow (follow_unit)."""
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

    async def test_a_journalctl_killed_otherwise_is_reported(self, monkeypatch):
        """Only the restart's signal is silenced.

        SIGKILL is what the OOM killer sends, and the backend survives it: the
        source is deaf from then on. Treating every negative returncode as a
        restart made that death silent — exactly the hole the report closes.
        """
        async def fake_exec(*args, **kwargs):
            return _FakeFollowProc([b"a\n"], exit_code=-9)

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
        assert "exit=-9" in errors[0].getMessage()

    async def test_the_restart_signal_reports_nothing(self, monkeypatch):
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


class TestFollowUnitArguments:
    """argv is the whole surface: no shell, so every bound is a flag."""

    @staticmethod
    async def _argv(monkeypatch, **kwargs):
        seen = {}

        async def fake_exec(*args, **kw):
            seen["argv"] = args
            return _FakeFollowProc([b""])

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        async for _ in journalctl.follow_unit("milo-mac", consequence="nothing real", **kwargs):
            pass
        return seen["argv"]

    async def test_a_plain_follow_starts_from_now(self, monkeypatch):
        assert await self._argv(monkeypatch) == (
            "journalctl", "-u", "milo-mac", "-f", "-n", "0", "-o", "cat",
        )

    async def test_a_bounded_follow_replays_everything_since_its_bound(self, monkeypatch):
        """The Mac source's replay: every line the running roc-recv wrote, and
        none older — the bound travels as `--since`, in the same process as the
        follow, so no line can fall between a read and a follow."""
        argv = await self._argv(monkeypatch, tail="all", since="@1790254635.371598")
        assert argv[argv.index("-n") + 1] == "all"
        assert argv[argv.index("--since") + 1] == "@1790254635.371598"
        assert "-f" in argv


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
