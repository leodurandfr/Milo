"""
Tests for the watcher that keeps snapclient pointed at the main Milo.

What breaks when these fail: a satellite stops following the server across an
address change. `milo-client-snapclient-launcher` freezes the resolved IP into
snapclient's argv at start, and snapclient retries a dead address in-process
rather than exiting — so `Restart=on-failure` never fires and nothing else in
the fleet notices. The speaker just disappears from the multiroom, with no
error anywhere. Measured on both satellites 2026-09-19, after the server
rebooted without its ethernet cable and came back on WiFi with a new IP.

The mocked boundaries are the two CI cannot reach: systemd (the unprivileged
`systemctl show` and the two sudo verbs) and mDNS resolution. /proc is real,
written under tmp_path, so the cmdline parsing runs against real bytes.
"""
import socket
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from services.registration import MILO_PRINCIPAL_PORT
from services.server_address_watch import SNAPCLIENT_UNIT, _reconcile


class _FakeProc:
    """A finished subprocess, as create_subprocess_exec hands it back."""

    def __init__(self, stdout=b"", returncode=0):
        self.returncode = returncode
        self._output = (stdout, b"")

    async def communicate(self):
        return self._output


def _write_cmdline(tmp_path, pid, *args):
    """Write a /proc-shaped cmdline blob for `pid`: NUL-separated, NUL-terminated."""
    (tmp_path / str(pid)).write_bytes(b"\0".join(a.encode() for a in args) + b"\0")


@contextmanager
def _watching(tmp_path, main_pid, resolves_to=None, principal="milo.local"):
    """Patch the watcher's whole outside world; yields the list of spawned argv.

    `main_pid` is what systemd reports for the snapclient unit — 0 when it is
    not running. `resolves_to=None` makes the lookup fail, as avahi does under
    load.
    """
    spawned = []

    async def fake_exec(*argv, **kwargs):
        spawned.append(argv)
        if argv[0] == "systemctl":
            return _FakeProc(stdout=f"{main_pid}\n".encode())
        return _FakeProc()

    if resolves_to is None:
        resolver = patch("services.registration.socket.getaddrinfo",
                         side_effect=socket.gaierror("Name or service not known"))
    else:
        resolver = patch("services.registration.socket.getaddrinfo",
                         return_value=[(2, 1, 6, "", (resolves_to, MILO_PRINCIPAL_PORT))])

    with patch.dict("os.environ", {"MILO_PRINCIPAL_IP": principal}, clear=True), \
         patch("services.server_address_watch.PROC_CMDLINE", str(tmp_path) + "/{pid}"), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         resolver:
        yield spawned


def _restarts(spawned):
    """The privileged calls only — the unprivileged MainPID read is not one."""
    return [argv for argv in spawned if argv[0] == "sudo"]


RESTART = [
    ("sudo", "systemctl", "stop", SNAPCLIENT_UNIT),
    ("sudo", "systemctl", "start", SNAPCLIENT_UNIT),
]


@pytest.mark.asyncio
async def test_a_moved_server_restarts_snapclient(tmp_path):
    """The defect itself: snapclient holds the old IP, the server answers on a new one.

    Both verbs are asserted, in order and with their exact argv, because that
    argv *is* the contract — milo-client/rootfs/etc/sudoers.d/milo-client grants
    `stop` and `start` by name, and deliberately does not grant `restart`.
    """
    _write_cmdline(tmp_path, 1077, "/usr/bin/snapclient", "-h", "192.168.1.55", "-p", "1704")

    with _watching(tmp_path, 1077, resolves_to="192.168.1.39") as spawned:
        assert await _reconcile() is True

    assert _restarts(spawned) == RESTART


@pytest.mark.asyncio
async def test_an_unmoved_server_is_left_alone(tmp_path):
    """The steady state is the common one, and a restart there is an audio cut."""
    _write_cmdline(tmp_path, 1077, "/usr/bin/snapclient", "-h", "192.168.1.39", "-p", "1704")

    with _watching(tmp_path, 1077, resolves_to="192.168.1.39") as spawned:
        assert await _reconcile() is False

    assert _restarts(spawned) == []


@pytest.mark.asyncio
async def test_a_failed_lookup_is_not_a_move(tmp_path):
    """mDNS answers NXDOMAIN under load, and that says nothing about the server.

    Treating it as a move would cut the audio of a client that was connected —
    the transient-avahi failure mode the launcher's literal exists to survive.
    """
    _write_cmdline(tmp_path, 1077, "/usr/bin/snapclient", "-h", "192.168.1.39", "-p", "1704")

    with _watching(tmp_path, 1077, resolves_to=None) as spawned:
        assert await _reconcile() is False

    assert _restarts(spawned) == []


@pytest.mark.asyncio
async def test_a_unit_that_is_not_running_is_not_started(tmp_path):
    """MainPID=0 is the satellite with no audio card: the launcher exits 0 there.

    That unit is meant to stay down, so a watcher that starts it would fight
    systemd every interval.
    """
    with _watching(tmp_path, 0, resolves_to="192.168.1.39") as spawned:
        assert await _reconcile() is False

    assert _restarts(spawned) == []


@pytest.mark.asyncio
async def test_an_operator_pinned_ip_is_never_seen_as_a_move(tmp_path):
    """A literal MILO_PRINCIPAL_IP exists for a LAN where mDNS does not work.

    It resolves to itself, so the watcher stays inert on such a unit instead of
    stop/starting it on every pass. The lookup is armed with a *different*
    address on purpose: a resolver that lost its literal short-circuit would
    answer that one, and the assertion below would catch it.
    """
    _write_cmdline(tmp_path, 1077, "/usr/bin/snapclient", "-h", "192.168.1.10", "-p", "1704")

    with _watching(tmp_path, 1077, principal="192.168.1.10",
                   resolves_to="192.168.1.39") as spawned:
        assert await _reconcile() is False

    assert _restarts(spawned) == []


@pytest.mark.asyncio
async def test_the_launchers_hostname_fallback_is_reclaimed(tmp_path):
    """The launcher passes `milo.local` through when it cannot resolve at boot.

    snapclient then re-issues mDNS on every retry — the stall the literal exists
    to avoid — so the first lookup that succeeds must reclaim the fast path.
    """
    _write_cmdline(tmp_path, 1077, "/usr/bin/snapclient", "-h", "milo.local", "-p", "1704")

    with _watching(tmp_path, 1077, resolves_to="192.168.1.39") as spawned:
        assert await _reconcile() is True

    assert _restarts(spawned) == RESTART
