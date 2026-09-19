"""
Tests for the watcher that keeps snapclient pointed at the main Milo.

What breaks when these fail: a satellite stops following the server across an
address change. `milo-client-snapclient-launcher` freezes the resolved IP into
snapclient's argv at start, and snapclient retries a dead address in-process
rather than exiting — so `Restart=on-failure` never fires and nothing else in
the fleet notices. The speaker just disappears from the multiroom, with no error
anywhere. Measured on both satellites 2026-09-19, after the server rebooted
without its ethernet cable and came back on WiFi with a new IP.

The other half of what these pin is the restraint: the watcher restarts a unit,
which silences a room, so every path that is *not* the defect must leave it
alone — above all a snapclient that is connected and streaming.

The mocked boundary is systemd (the unprivileged `systemctl show` and the two
sudo verbs) and mDNS resolution. /proc is real, written under tmp_path, so the
cmdline and socket-table parsing run against real bytes.
"""
import re
import socket
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from services.registration import MILO_PRINCIPAL_PORT
from services.server_address_watch import (
    SNAPCLIENT_SERVICE,
    _reconcile,
    _server_arg,
)
import services.server_address_watch as watch

LAUNCHER = (Path(__file__).resolve().parents[2]
            / "rootfs/usr/local/bin/milo-client-snapclient-launcher")

RESTART = [
    ("sudo", "systemctl", "stop", SNAPCLIENT_SERVICE),
    ("sudo", "systemctl", "start", SNAPCLIENT_SERVICE),
]

TCP_HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
# An established socket to a snapserver, copied from canape's own table:
# addresses are little-endian hex, remote port 1704 == 0x06A8, state 01.
TCP_STREAMING = TCP_HEADER + "   4: 9901A8C0:D3FC 2701A8C0:06A8 01 00000000:00000000 00:00000000 00000000  1000        0 12107\n"
# The same host with only its SSH session open: nothing on 1704.
TCP_IDLE = TCP_HEADER + "   3: 9901A8C0:0016 0501A8C0:E4A2 01 00000000:00000000 00:00000000 00000000  1000        0 11842\n"


class _FakeProc:
    """A finished subprocess, as create_subprocess_exec hands it back."""

    def __init__(self, stdout=b"", returncode=0):
        self.returncode = returncode
        self._output = (stdout, b"")

    async def communicate(self):
        return self._output


@pytest.fixture(autouse=True)
def _forget_owed_start():
    """The owed-start flag is module state; a leak would make tests order-dependent."""
    watch._start_owed = False
    yield
    watch._start_owed = False


def _write_cmdline(tmp_path, pid, *args):
    """Write a /proc-shaped cmdline blob for `pid`: NUL-separated, NUL-terminated."""
    (tmp_path / str(pid)).write_bytes(b"\0".join(a.encode() for a in args) + b"\0")


@contextmanager
def _watching(tmp_path, main_pid, resolves_to=None, principal="milo.local",
              tcp=TCP_IDLE, start_fails=False):
    """Patch the watcher's whole outside world; yields the list of spawned argv.

    `main_pid` is what systemd reports for the snapclient unit — 0 when it is not
    running. `resolves_to=None` makes the lookup fail, as avahi does under load.
    `tcp` is the socket table the kernel would show.
    """
    spawned = []
    (tmp_path / "tcp").write_text(tcp)

    async def fake_exec(*argv, **kwargs):
        spawned.append(argv)
        if argv[0] == "systemctl":
            return _FakeProc(stdout=f"{main_pid}\n".encode())
        if start_fails and argv[2] == "start":
            return _FakeProc(stdout=b"", returncode=1)
        return _FakeProc()

    if resolves_to is None:
        resolver = patch("services.registration.socket.getaddrinfo",
                         side_effect=socket.gaierror("Name or service not known"))
    else:
        resolver = patch("services.registration.socket.getaddrinfo",
                         return_value=[(2, 1, 6, "", (resolves_to, MILO_PRINCIPAL_PORT))])

    with patch.dict("os.environ", {"MILO_PRINCIPAL_IP": principal}, clear=True), \
         patch("services.server_address_watch.PROC_CMDLINE", str(tmp_path) + "/{pid}"), \
         patch("services.server_address_watch.PROC_NET_TCP", (str(tmp_path / "tcp"),)), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         resolver:
        yield spawned


def _restarts(spawned):
    """The privileged calls only — the unprivileged MainPID read is not one."""
    return [argv for argv in spawned if argv[0] == "sudo"]


@pytest.mark.asyncio
async def test_a_moved_server_restarts_a_disconnected_snapclient(tmp_path):
    """The defect itself: snapclient holds the old IP and can no longer dial.

    Both verbs are asserted, in order and with their exact argv, because that
    argv *is* the contract — milo-client/rootfs/etc/sudoers.d/milo-client grants
    `stop` and `start` by name, and deliberately does not grant `restart`.
    """
    _write_cmdline(tmp_path, 1077, "/usr/bin/snapclient", "-h", "192.168.1.55", "-p", "1704")

    with _watching(tmp_path, 1077, resolves_to="192.168.1.39") as spawned:
        assert await _reconcile() is True

    assert _restarts(spawned) == RESTART


@pytest.mark.asyncio
async def test_a_streaming_snapclient_is_never_cut_off(tmp_path):
    """The restraint, and the reason the check is gated on the socket at all.

    A server can hold two addresses at once — the one snapclient dialled and the
    one mDNS now answers — so a moved address does not imply a broken client.
    Restarting on the address alone silences a room that was playing, to no end:
    the stale address costs nothing until the connection drops, and this check
    fires then.
    """
    _write_cmdline(tmp_path, 1077, "/usr/bin/snapclient", "-h", "192.168.1.55", "-p", "1704")

    with _watching(tmp_path, 1077, resolves_to="192.168.1.39", tcp=TCP_STREAMING) as spawned:
        assert await _reconcile() is False

    assert _restarts(spawned) == []


@pytest.mark.asyncio
async def test_an_unmoved_server_is_left_alone(tmp_path):
    """A disconnected client whose address is still right is a server that is down.

    Restarting it would add an audio gap to an outage it cannot fix.
    """
    _write_cmdline(tmp_path, 1077, "/usr/bin/snapclient", "-h", "192.168.1.39", "-p", "1704")

    with _watching(tmp_path, 1077, resolves_to="192.168.1.39") as spawned:
        assert await _reconcile() is False

    assert _restarts(spawned) == []


@pytest.mark.asyncio
async def test_a_failed_lookup_is_not_a_move(tmp_path):
    """mDNS answers NXDOMAIN under load, and that says nothing about the server."""
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
    to avoid — so a disconnected client in that state must be handed the literal.
    """
    _write_cmdline(tmp_path, 1077, "/usr/bin/snapclient", "-h", "milo.local", "-p", "1704")

    with _watching(tmp_path, 1077, resolves_to="192.168.1.39") as spawned:
        assert await _reconcile() is True

    assert _restarts(spawned) == RESTART


@pytest.mark.asyncio
async def test_a_start_that_failed_is_owed_and_retried(tmp_path):
    """A stop that succeeds and a start that does not leaves the room silent.

    Nothing else would retry it: the unit is dead, a dead unit has no address to
    compare, and the watcher's own "never start a stopped unit" rule would then
    refuse to touch the very unit it stopped. The debt has to be carried.
    """
    _write_cmdline(tmp_path, 1077, "/usr/bin/snapclient", "-h", "192.168.1.55", "-p", "1704")

    with _watching(tmp_path, 1077, resolves_to="192.168.1.39", start_fails=True):
        with pytest.raises(RuntimeError):
            await _reconcile()
    assert watch._start_owed is True

    # Next pass: the unit is dead, so nothing can be compared — it must start anyway.
    with _watching(tmp_path, 0, resolves_to="192.168.1.39") as spawned:
        assert await _reconcile() is True

    assert _restarts(spawned) == RESTART
    assert watch._start_owed is False


def test_the_watcher_reads_the_flag_the_launcher_actually_passes():
    """The parse is a convention shared with a shell script no test would run.

    Renaming the flag to `--host` in the launcher would make the watcher read
    nothing, report every snapclient as "not running", and turn the whole
    feature into a silent no-op with both suites green.
    """
    source = LAUNCHER.read_text()
    assert "exec /usr/bin/snapclient" in source, "launcher no longer execs snapclient"

    match = re.search(r'(-{1,2}[\w-]+)\s+"\$server"', source)
    assert match, "no flag carries $server into snapclient — update the parse"

    flag = match.group(1)
    argv = b"\0".join([b"/usr/bin/snapclient", flag.encode(), b"192.0.2.1"]) + b"\0"
    assert _server_arg(argv) == "192.0.2.1"
