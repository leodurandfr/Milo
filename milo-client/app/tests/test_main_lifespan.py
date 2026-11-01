"""The satellite app's startup, as systemd and sudo see it.

What breaks when these fail: a room goes silent for good after the app dies.
`milo-client-camilladsp` and `milo-client-snapclient` are `BindsTo=` the app,
so a crash stops both, `Restart=always` brings only the app back, and nothing
else starts them again. With no snapclient the server sees the satellite
offline, never admits it, and no update push reaches it either: the speaker
stays mute until someone reboots it.

The mocked boundary is systemd: the notify socket is a real datagram socket
under tmp_path, and every `sudo systemctl` is a child process that never runs.
The unit names are written out rather than imported: the contract is the
satellite's sudoers policy, which grants exactly these two argv.
"""
import asyncio
import logging
import socket
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import main

START_CAMILLADSP = ("sudo", "systemctl", "start", "milo-client-camilladsp.service")
START_SNAPCLIENT = ("sudo", "systemctl", "start", "milo-client-snapclient.service")


class _FakeProc:
    """A finished subprocess, as create_subprocess_exec hands it back."""

    def __init__(self, returncode, stderr):
        self.returncode = returncode
        self._output = (b"", stderr)

    async def communicate(self):
        return self._output


@pytest.fixture
def notify_socket(tmp_path, monkeypatch):
    """systemd's end of `NOTIFY_SOCKET`, readable without blocking."""
    path = tmp_path / "notify"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(str(path))
    sock.setblocking(False)
    monkeypatch.setenv("NOTIFY_SOCKET", str(path))
    yield sock
    sock.close()


@contextmanager
def _satellite(notify_socket, returncode=0, stderr=b""):
    """Run the lifespan with systemd mocked; yields one (argv, notified) per spawn.

    `notified` is every datagram systemd had received when that child was
    spawned, so the order between READY=1 and a start is read from outside.
    """
    received = []
    spawned = []

    async def spawn(*argv, **_kwargs):
        while True:
            try:
                received.append(notify_socket.recv(64))
            except BlockingIOError:
                break
        spawned.append((argv, tuple(received)))
        return _FakeProc(returncode, stderr)

    with patch("asyncio.create_subprocess_exec", new=spawn), \
            patch.object(main, "register_with_main_milo", new=AsyncMock()), \
            patch.object(main, "follow_server_address", new=AsyncMock()), \
            patch.object(main.equalizer_service, "start_connection_loop", new=MagicMock()), \
            patch.object(main.equalizer_service, "stop_connection_loop", new=AsyncMock()):
        yield spawned


async def _until_spawned(spawned, count):
    """Let the lifespan's background tasks run until `count` children were spawned."""
    for _ in range(100):
        if len(spawned) >= count:
            return
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_lifespan_starts_both_bound_units_after_ready(notify_socket):
    """A restarted app finds its two units stopped by BindsTo= and must start
    them; before READY=1 a start would only queue behind the app's own start
    job, since both units are ordered After= it."""
    with _satellite(notify_socket) as spawned:
        async with main.lifespan(main.app):
            await _until_spawned(spawned, 2)

    assert sorted(argv for argv, _ in spawned) == [START_CAMILLADSP, START_SNAPCLIENT]
    assert all(notified == (b"READY=1",) for _, notified in spawned)


@pytest.mark.asyncio
async def test_a_failed_start_is_logged_at_error(notify_socket, caplog):
    """The satellite has no log surface in the UI: a start that fails silently
    is a mute room with nothing anywhere to say why. One unit failing must not
    keep the other down."""
    caplog.set_level(logging.ERROR)
    with _satellite(notify_socket, returncode=1, stderr=b"Job failed") as spawned:
        async with main.lifespan(main.app):
            await _until_spawned(spawned, 2)

    assert sorted(argv for argv, _ in spawned) == [START_CAMILLADSP, START_SNAPCLIENT]
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("milo-client-camilladsp.service" in m and "Job failed" in m for m in errors)
    assert any("milo-client-snapclient.service" in m and "Job failed" in m for m in errors)
