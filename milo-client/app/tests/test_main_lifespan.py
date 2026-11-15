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
import contextlib
import logging
import os
import socket
import sys
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import uvicorn

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
            main.mark_listening()
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
            main.mark_listening()
            await _until_spawned(spawned, 2)

    assert sorted(argv for argv, _ in spawned) == [START_CAMILLADSP, START_SNAPCLIENT]
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("milo-client-camilladsp.service" in m and "Job failed" in m for m in errors)
    assert any("milo-client-snapclient.service" in m and "Job failed" in m for m in errors)


@pytest.mark.asyncio
async def test_nothing_follows_ready_until_the_server_listens(notify_socket):
    """The lifespan's end is not READY=1: the socket is bound after it."""
    with _satellite(notify_socket) as spawned:
        async with main.lifespan(main.app):
            await _until_spawned(spawned, 1)
            ready_sent = _readable(notify_socket)

    assert spawned == []
    assert not ready_sent


def _readable(sock):
    try:
        sock.recv(64, socket.MSG_PEEK)
    except BlockingIOError:
        return False
    return True


@pytest.mark.asyncio
async def test_ready_is_sent_once_the_port_accepts_connections(notify_socket):
    """The real server on an ephemeral port: when READY=1 reaches systemd's
    socket, a connect to the app's port must already succeed. Sent earlier,
    snapclient started and the server admitted a satellite whose API refused
    it, and a new version's hold counted from before it could serve anything."""
    if sys.platform != "linux":
        pytest.skip("AF_UNIX notify socket")
    server = main._Server(uvicorn.Config(
        main.app, host="127.0.0.1", port=0, log_level="warning", ws="none"
    ))
    loop = asyncio.get_running_loop()
    answered_at_ready = []
    ready = asyncio.Event()

    def on_datagram():
        # Before `_satellite`'s spawn drains the socket: READY=1 is the first
        # datagram, and it is what this reads.
        loop.remove_reader(notify_socket.fileno())
        try:
            port = server.servers[0].sockets[0].getsockname()[1]
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
        except (AttributeError, IndexError, OSError):
            answered_at_ready.append(False)
        else:
            answered_at_ready.append(True)
        ready.set()

    loop.add_reader(notify_socket.fileno(), on_datagram)
    with _satellite(notify_socket) as spawned:
        serving = asyncio.create_task(server.serve())
        # A bound on a hang, not a budget: nothing is measured against it.
        await asyncio.wait_for(ready.wait(), timeout=30)
        await _until_spawned(spawned, 2)
        server.should_exit = True
        await serving

    assert answered_at_ready == [True]
    assert sorted(argv for argv, _ in spawned) == [START_CAMILLADSP, START_SNAPCLIENT]


@pytest.mark.asyncio
async def test_the_watchdog_is_fed_from_the_loop_once_ready(notify_socket, monkeypatch):
    """WatchdogSec= restarts an app whose event loop stopped turning: a pushed
    version that hangs after READY would otherwise neither commit its trial nor
    reach the start that rolls it back, and the room's API would stay dead.
    systemd arms the watchdog at READY=1, so the feed starts after it and must
    keep coming."""
    monkeypatch.setenv("WATCHDOG_USEC", "3000")
    monkeypatch.setenv("WATCHDOG_PID", str(os.getpid()))
    loop = asyncio.get_running_loop()
    received = []
    enough = asyncio.Event()

    def on_datagram():
        with contextlib.suppress(BlockingIOError):
            received.append(notify_socket.recv(64))
        if received.count(b"WATCHDOG=1") >= 3:
            enough.set()

    loop.add_reader(notify_socket.fileno(), on_datagram)
    try:
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_FakeProc(0, b""))), \
                patch.object(main, "register_with_main_milo", new=AsyncMock()), \
                patch.object(main, "follow_server_address", new=AsyncMock()), \
                patch.object(main.equalizer_service, "start_connection_loop", new=MagicMock()), \
                patch.object(main.equalizer_service, "stop_connection_loop", new=AsyncMock()):
            async with main.lifespan(main.app):
                main.mark_listening()
                # A bound on a hang, not a budget: nothing is measured against it.
                await asyncio.wait_for(enough.wait(), timeout=30)
    finally:
        loop.remove_reader(notify_socket.fileno())

    assert received[0] == b"READY=1"
    assert set(received[1:]) == {b"WATCHDOG=1"}


@pytest.mark.parametrize("usec, pid, expected", [
    ("30000000", "self", 10.0),       # a third of WatchdogSec=30
    (None, None, None),               # the unit sets no watchdog
    ("30000000", "1", None),          # a child that inherited the variables
    ("thirty", "self", None),
])
def test_the_watchdog_interval_follows_what_systemd_set(monkeypatch, usec, pid, expected):
    """Fed from a process systemd does not watch, a dead main process would
    look alive; fed too slowly, a healthy one would be killed."""
    for name, value in (("WATCHDOG_USEC", usec), ("WATCHDOG_PID", pid)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, str(os.getpid()) if value == "self" else value)
    assert main._watchdog_interval() == expected
