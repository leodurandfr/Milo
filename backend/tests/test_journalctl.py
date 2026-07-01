# backend/tests/test_journalctl.py
"""Tests for the shared journalctl helpers (follow_unit / read_unit)."""
import asyncio

import pytest

from backend.shared import journalctl


class _FakeFollowStdout:
    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""  # EOF


class _FakeFollowProc:
    def __init__(self, lines):
        self.stdout = _FakeFollowStdout(lines)
        self.returncode = None
        self.terminated = False

    def terminate(self):
        self.terminated = True

    async def wait(self):
        self.returncode = -15
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

        out = [line async for line in journalctl.follow_unit("milo-x")]

        assert out == ["hello", "world"]  # blank line skipped, EOF ends iteration
        assert proc.terminated is True    # finally-block teardown ran

    @pytest.mark.asyncio
    async def test_terminates_on_early_close(self, monkeypatch):
        proc = _FakeFollowProc([b"a\n", b"b\n", b"c\n", b""])

        async def fake_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        gen = journalctl.follow_unit("milo-x")
        assert await gen.__anext__() == "a"
        await gen.aclose()  # consumer stops early

        assert proc.terminated is True


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
