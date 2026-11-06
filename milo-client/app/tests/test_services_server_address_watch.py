"""
Tests for the watcher that keeps snapclient pointed at the main Milo.

What breaks when these fail: a satellite stops following the server across an
address change. `milo-client-snapclient-launcher` freezes the resolved IP into
snapclient's argv at start, and snapclient retries a dead address in-process
rather than exiting — so `Restart=on-failure` never fires and nothing else in
the fleet notices. The speaker just disappears from the multiroom, with no error
anywhere. Measured on both satellites 2026-09-19, after the server rebooted
without its ethernet cable and came back on WiFi with a new IP.

The same watcher brings a satellite's stream back to its own cable once the cable
returns (the owner's decision of 2026-11-06: Ethernet first, even mid-song).
Measured on Bureau the same day: replugged, it stayed on its WiFi address for
3 min 38 s, until a reboot.

The other half of what these pin is the restraint: the watcher restarts a unit,
which silences a room, so every path that is *not* the defect must leave it
alone — a playing snapclient above all, which only that return to the cable may
cut, and only once the cable has held.

The mocked boundary is systemd (the unprivileged `systemctl show` and the two
sudo verbs), mDNS resolution, and the kernel's answers about eth0 (its carrier
under tmp_path, its address over ioctl, the source a new connection would take).
/proc is real, written under tmp_path, so the cmdline, socket-table and
ALSA-status parsing run against real bytes. The loop
tests run `follow_server_address` itself on a virtual clock its sleeps advance,
so what they count is passes of the real loop, never wall-clock time.
"""
import errno
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
    follow_server_address,
)
import services.server_address_watch as watch

ROOTFS = Path(__file__).resolve().parents[2] / "rootfs"
LAUNCHER = ROOTFS / "usr/local/bin/milo-client-snapclient-launcher"
ASOUND_CONF = ROOTFS / "etc/asound.conf"

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

# snapclient's ALSA output, as /proc/asound shows it: open and fed, or closed —
# which snapclient does 5 s after its last chunk, once the server's stream idles.
ALSA_RUNNING = "state: RUNNING\nowner_pid   : 1077\ntrigger_time: 5251.5\n"
ALSA_CLOSED = "closed\n"


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
              tcp=TCP_IDLE, alsa=ALSA_RUNNING, start_fails=False):
    """Patch the watcher's whole outside world; yields the list of spawned argv.

    `main_pid` is what systemd reports for the snapclient unit — 0 when it is not
    running. `resolves_to=None` makes the lookup fail, as avahi does under load.
    `tcp` is the socket table the kernel would show, `alsa` the status of
    snapclient's output (None: the file cannot be read).
    """
    spawned = []
    (tmp_path / "tcp").write_text(tcp)
    if alsa is not None:
        (tmp_path / "alsa").write_text(alsa)

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
         patch("services.server_address_watch.LOOPBACK_PLAYBACK_STATUS", str(tmp_path / "alsa")), \
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
async def test_a_playing_snapclient_is_never_cut_off(tmp_path):
    """The restraint: a room that is playing is never silenced for an address.

    A server can hold two addresses at once — the one snapclient dialled and the
    one mDNS now answers — so a moved address does not imply a broken client,
    and while audio flows the stale one costs nothing audible.
    """
    _write_cmdline(tmp_path, 1077, "/usr/bin/snapclient", "-h", "192.168.1.39", "-p", "1704")

    with _watching(tmp_path, 1077, resolves_to="192.168.1.55", tcp=TCP_STREAMING,
                   alsa=ALSA_RUNNING) as spawned:
        assert await _reconcile() is False

    assert _restarts(spawned) == []


@pytest.mark.asyncio
async def test_a_connected_client_playing_nothing_follows_the_server_back(tmp_path):
    """The stuck state: connected to the server's WiFi address after its cable came back.

    snapclient re-dials the literal it holds, and when the server only restarted
    snapserver it is back in ~1 s (measured twice on 2026-11-05, both
    satellites) — so it never stays disconnected long enough to be compared, and
    it sat on the WiFi address until someone restarted it by hand. With its ALSA
    output closed there is no audio to cut, so the address is compared there.
    """
    _write_cmdline(tmp_path, 1077, "/usr/bin/snapclient", "-h", "192.168.1.39", "-p", "1704")

    with _watching(tmp_path, 1077, resolves_to="192.168.1.55", tcp=TCP_STREAMING,
                   alsa=ALSA_CLOSED) as spawned:
        assert await _reconcile() is True

    assert _restarts(spawned) == RESTART


@pytest.mark.asyncio
async def test_an_unreadable_output_counts_as_playing(tmp_path):
    """Closed has to be read, never assumed: a guess there would cut a playing room."""
    _write_cmdline(tmp_path, 1077, "/usr/bin/snapclient", "-h", "192.168.1.39", "-p", "1704")

    with _watching(tmp_path, 1077, resolves_to="192.168.1.55", tcp=TCP_STREAMING,
                   alsa=None) as spawned:
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


def test_the_watcher_reads_the_output_snapclient_actually_plays_into():
    """The ALSA path is a convention spread over the launcher and asound.conf.

    snapclient plays into the PCM the launcher names, which asound.conf maps to a
    Loopback substream. Moving either would leave the watcher reading a substream
    that is always closed — and every connected room would be restarted at each
    check, mid-song.
    """
    launcher = LAUNCHER.read_text()
    match = re.search(r"--soundcard\s+(\S+)", launcher)
    assert match, "the launcher names no --soundcard — update this test"
    pcm = match.group(1)

    block = re.search(r"^pcm\." + re.escape(pcm) + r"\s*\{(.*?)^\}", ASOUND_CONF.read_text(),
                      re.S | re.M)
    assert block, f"asound.conf defines no pcm.{pcm}"
    hw = dict(re.findall(r"^\s*(card|device|subdevice)\s+(\S+)", block.group(1), re.M))
    assert set(hw) == {"card", "device", "subdevice"}, hw

    expected = f"/proc/asound/{hw['card']}/pcm{hw['device']}p/sub{hw['subdevice']}/status"
    assert watch.LOOPBACK_PLAYBACK_STATUS == expected


# --- The loop, on a virtual clock ------------------------------------------------

def _hex(address):
    """An IPv4 address as /proc/net/tcp prints it: the 32-bit word, little-endian."""
    return socket.inet_aton(address)[::-1].hex().upper()


def _tcp_stream(local, remote):
    """A socket table holding one established connection from `local` to a snapserver."""
    return TCP_HEADER + (f"   4: {_hex(local)}:D3FC {_hex(remote)}:06A8 01 00000000:00000000 "
                         "00:00000000 00000000  1000        0 12107\n")


# The satellite's two addresses: Bureau's eth0 and the WiFi standby it streamed
# from for 3 min 38 s after its cable came back (measured 2026-11-06).
SAT_WIRED, SAT_WIFI = "192.168.1.60", "192.168.1.69"


class _FakeUdpSocket:
    """The datagram socket the kernel is asked through: the source it would pick
    for a new connection (`connect` + `getsockname`, nothing sent), and the fd an
    ioctl goes through."""

    def __init__(self, world):
        self.world = world
        self.source = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def fileno(self):
        return -1

    def connect(self, address):
        self.source = self.world.preferred(self.world.now)
        if self.source is None:
            raise OSError(errno.ENETUNREACH, "Network is unreachable")

    def getsockname(self):
        return (self.source, 40000)


class _End(BaseException):
    """Ends a loop run; a BaseException so the loop's own `except Exception` cannot eat it."""


class _World:
    """A satellite's outside world as functions of time, on a clock the loop's sleeps advance.

    `connected(t)` and `playing(t)` say what snapclient is doing, `resolves(t)`
    what mDNS answers for the server. snapclient runs with `-h dialled`, from
    the satellite address `local(t)`. The satellite's eth0 is `carrier(t)` (the
    sysfs text, None: no such file), `wired(t)` its IPv4 address (None: none),
    and `preferred(t)` the source the kernel picks for a new connection to the
    server (None: no route). The run ends at `until`, or at the first restart
    unless `past_restarts`; restart times are recorded.
    """

    def __init__(self, tmp_path, dialled, resolves, connected, playing=lambda t: True,
                 until=600, past_restarts=False, local=lambda t: SAT_WIRED,
                 carrier=lambda t: None, wired=lambda t: None, preferred=lambda t: None):
        self.tmp_path = tmp_path
        self.dialled = dialled
        self.resolves, self.connected, self.playing = resolves, connected, playing
        self.local, self.carrier, self.wired, self.preferred = local, carrier, wired, preferred
        self.until, self.past_restarts = until, past_restarts
        self.now = 0.0
        self.lookups = []      # virtual times of each mDNS resolution
        self.pid_reads = []    # virtual times of each `systemctl show`
        self.restarts = []     # virtual times of each `systemctl start`
        _write_cmdline(tmp_path, 1077, "/usr/bin/snapclient", "-h", dialled, "-p", "1704")
        self._write()

    def _write(self):
        tcp = _tcp_stream(self.local(self.now), self.dialled) if self.connected(self.now) else TCP_IDLE
        (self.tmp_path / "tcp").write_text(tcp)
        (self.tmp_path / "alsa").write_text(ALSA_RUNNING if self.playing(self.now) else ALSA_CLOSED)
        carrier = self.tmp_path / "carrier"
        if self.carrier(self.now) is None:
            carrier.unlink(missing_ok=True)
        else:
            carrier.write_text(self.carrier(self.now) + "\n")

    async def _sleep(self, seconds):
        self.now += seconds
        if self.now >= self.until or (self.restarts and not self.past_restarts):
            raise _End
        self._write()

    async def _exec(self, *argv, **kwargs):
        if argv[0] == "systemctl":
            self.pid_reads.append(self.now)
            return _FakeProc(stdout=b"1077\n")
        if argv[2] == "start":
            self.restarts.append(self.now)
        return _FakeProc()

    @property
    def restarted_at(self):
        return self.restarts[0] if self.restarts else None

    def _getaddrinfo(self, *args, **kwargs):
        self.lookups.append(self.now)
        return [(2, 1, 6, "", (self.resolves(self.now), MILO_PRINCIPAL_PORT))]

    def _ioctl(self, fd, request, ifreq):
        """SIOCGIFADDR: the interface's IPv4 address, at bytes 20-24 of the ifreq."""
        address = self.wired(self.now)
        if address is None:
            raise OSError(errno.EADDRNOTAVAIL, "Cannot assign requested address")
        return ifreq[:20] + socket.inet_aton(address) + ifreq[24:]

    async def run(self):
        with patch.dict("os.environ", {"MILO_PRINCIPAL_IP": "milo.local"}, clear=True), \
             patch("services.server_address_watch.PROC_CMDLINE", str(self.tmp_path) + "/{pid}"), \
             patch("services.server_address_watch.PROC_NET_TCP", (str(self.tmp_path / "tcp"),)), \
             patch("services.server_address_watch.LOOPBACK_PLAYBACK_STATUS",
                   str(self.tmp_path / "alsa")), \
             patch("asyncio.create_subprocess_exec", side_effect=self._exec), \
             patch("services.server_address_watch.WIRED_CARRIER", str(self.tmp_path / "carrier")), \
             patch("services.registration.socket.getaddrinfo", side_effect=self._getaddrinfo), \
             patch("socket.socket", side_effect=lambda *a, **k: _FakeUdpSocket(self)), \
             patch("fcntl.ioctl", side_effect=self._ioctl), \
             patch("asyncio.sleep", side_effect=self._sleep):
            try:
                await follow_server_address()
            except _End:
                pass
        return self


WIRED, WIFI = "192.168.1.55", "192.168.1.39"
# What mDNS lags behind a loss: avahi published the WiFi address 2.4 s after the
# cable went (measured 2026-11-05); 3 s leaves the satellite's cache a pass more.
AVAHI_LAG = 3


@pytest.mark.asyncio
@pytest.mark.parametrize("loss", [31.5, 45.5, 58.5])
async def test_a_loss_while_playing_is_followed_within_seconds(tmp_path, loss):
    """The server loses its cable while the room plays, and moves to WiFi.

    The loss happens while snapclient is connected, so a check paced on the
    connected state alone sees it a whole connected interval late — measured
    6 s on Bureau and 25 s on Canapé on 2026-11-05, against a reconnection that
    takes under a second. Each loss phase is run: the gap must not depend on it.
    """
    world = await _World(
        tmp_path, dialled=WIRED,
        resolves=lambda t: WIFI if t >= loss + AVAHI_LAG else WIRED,
        connected=lambda t: t < loss,
        playing=lambda t: t < loss + 5,
        until=300,
    ).run()

    assert world.restarted_at is not None, "the move was never followed"
    assert world.restarted_at - loss <= AVAHI_LAG + 3


@pytest.mark.asyncio
async def test_a_playing_room_costs_no_subprocess_and_no_lookup(tmp_path):
    """A satellite plays for hours: only the socket table may be read for it.

    The socket is read every second, so anything more on that path — a
    `systemctl show`, an mDNS query — would run every second, all day.
    """
    world = await _World(
        tmp_path, dialled=WIRED, resolves=lambda t: WIFI,
        connected=lambda t: True, playing=lambda t: True, until=300,
    ).run()

    assert world.restarted_at is None
    assert world.pid_reads == []
    assert world.lookups == []


@pytest.mark.asyncio
async def test_the_fast_check_after_a_loss_is_bounded(tmp_path):
    """Multiroom switched off stops snapserver for hours: the fast check must end.

    The client never reconnects then, and its address never moves. Past the
    window after the loss, the lookup falls back to one per check interval —
    not one every two seconds for as long as multiroom stays off.
    """
    loss = 10.5
    world = await _World(
        tmp_path, dialled=WIRED, resolves=lambda t: WIRED,
        connected=lambda t: t < loss, playing=lambda t: t < loss + 5, until=900,
    ).run()

    assert world.restarted_at is None
    early = [t for t in world.lookups if t < loss + 30]
    assert len(early) >= 10, "no fast check after the loss"
    late = [t for t in world.lookups if t >= 300]
    assert late, "the check stopped altogether"
    assert len(late) <= (900 - 300) // watch.CHECK_INTERVAL + 1


@pytest.mark.asyncio
async def test_a_pause_brings_a_stuck_client_back_to_the_wired_address(tmp_path):
    """Connected to the WiFi address, playing, then paused: it follows back then, never before."""
    pause = 100.5
    world = await _World(
        tmp_path, dialled=WIFI, resolves=lambda t: WIRED,
        connected=lambda t: True, playing=lambda t: t < pause, until=400,
    ).run()

    assert world.restarted_at is not None, "a client playing nothing never followed back"
    assert world.restarted_at > pause
    assert world.restarted_at - pause <= watch.CHECK_INTERVAL + 1


@pytest.mark.asyncio
async def test_a_restart_is_not_repeated_before_its_cooldown(tmp_path):
    """The restart's own disconnection is not a loss to chase.

    Stopping snapclient drops its connection, which reads exactly like the cable
    going; a restarted client still dialling, or relaunched on a stale mDNS
    answer, would otherwise be stopped again every two seconds, each time a
    fresh gap in the room.
    """
    restarts = []
    world = _World(
        tmp_path, dialled=WIFI, resolves=lambda t: WIRED,
        connected=lambda t: not any(r <= t < r + 3 for r in restarts),
        playing=lambda t: False, until=300, past_restarts=True,
    )
    restarts = world.restarts
    await world.run()

    assert len(world.restarts) >= 2, "the stale client was not retried at all"
    gaps = [b - a for a, b in zip(world.restarts, world.restarts[1:])]
    assert min(gaps) >= watch.COOLDOWN_INTERVAL


def test_the_stream_addresses_are_read_from_the_kernels_own_bytes():
    """The return to the cable compares the address the stream leaves from.

    Read off canape's own table: a parse that swapped the columns or the byte
    order would compare an address no interface holds, and restart every room
    that is playing.
    """
    assert watch._snapserver_stream(TCP_STREAMING) == ("192.168.1.153", "192.168.1.39")
    assert watch._snapserver_stream(TCP_IDLE) is None


def test_an_ipv4_stream_listed_in_the_ipv6_table_reads_as_ipv4():
    """snapclient resolves for itself on the launcher's hostname fallback, and an
    IPv4 connection then shows in /proc/net/tcp6 as ::ffff:a.b.c.d."""
    tcp6 = TCP_HEADER + ("   0: 0000000000000000FFFF00009901A8C0:D3FC "
                         "0000000000000000FFFF00002701A8C0:06A8 01 00000000:00000000 "
                         "00:00000000 00000000  1000        0 12107\n")

    assert watch._snapserver_stream(tcp6) == ("192.168.1.153", "192.168.1.39")


# What the cable's return looks like: carrier, a lease, and the kernel's choice
# moving to eth0 for every new connection — while the open stream stays on WiFi.
REPLUG = 100.5


def _cable_back(t):
    return t >= REPLUG


@pytest.mark.asyncio
async def test_a_playing_stream_on_the_wifi_address_moves_once_ethernet_is_back(tmp_path):
    """The defect: replugged, the satellite streamed from its WiFi address until a reboot.

    New connections already prefer eth0; the open one never moves, because
    snapclient has no reason to reconnect. The owner chose a few seconds of
    silence in that room over a whole listening session on WiFi, so it moves
    while playing — after the hold, never before.
    """
    world = await _World(
        tmp_path, dialled=WIRED, resolves=lambda t: WIRED,
        connected=lambda t: True, playing=lambda t: True, local=lambda t: SAT_WIFI,
        carrier=lambda t: "1" if _cable_back(t) else "0",
        wired=lambda t: SAT_WIRED if _cable_back(t) else None,
        preferred=lambda t: SAT_WIRED if _cable_back(t) else SAT_WIFI,
    ).run()

    assert world.restarted_at is not None, "the stream never came back to the cable"
    assert REPLUG + watch.LINK_HOLD - 1 <= world.restarted_at <= REPLUG + watch.LINK_HOLD + 1


@pytest.mark.asyncio
async def test_a_flapping_cable_restarts_nothing(tmp_path):
    """A cable that comes and goes faster than the hold never earns a restart.

    Each restart is a gap in the room; one per flap would be worse than the WiFi.
    Only the carrier flaps here: NetworkManager defers a carrier loss by a few
    seconds, keeping eth0's lease, and the kernel keeps choosing its routes
    (marked linkdown) meanwhile — so the address and the route alone never see
    a flap that short.
    """
    world = await _World(
        tmp_path, dialled=WIRED, resolves=lambda t: WIRED,
        connected=lambda t: True, local=lambda t: SAT_WIFI,
        carrier=lambda t: "1" if int(t) % 8 < 4 else "0",
        wired=lambda t: SAT_WIRED, preferred=lambda t: SAT_WIRED,
    ).run()

    assert world.restarted_at is None


@pytest.mark.asyncio
async def test_a_preferred_wifi_route_is_never_chased(tmp_path):
    """eth0 up with a lease, but the kernel still routes to the server over WiFi.

    NetworkManager raises eth0's metric (20100) when its connectivity check reads
    the link as degraded: a restart would land on WiFi again, every cooldown,
    forever. The rule moves a stream only to where a new connection would go.
    """
    world = await _World(
        tmp_path, dialled=WIRED, resolves=lambda t: WIRED,
        connected=lambda t: True, local=lambda t: SAT_WIFI,
        carrier=lambda t: "1", wired=lambda t: SAT_WIRED, preferred=lambda t: SAT_WIFI,
    ).run()

    assert world.restarted_at is None


@pytest.mark.asyncio
async def test_a_wifi_only_satellite_is_left_alone(tmp_path):
    """No cable, or no eth0 at all: the WiFi stream is the only one there is."""
    world = await _World(
        tmp_path, dialled=WIRED, resolves=lambda t: WIRED,
        connected=lambda t: True, local=lambda t: SAT_WIFI,
        carrier=lambda t: None, wired=lambda t: None, preferred=lambda t: SAT_WIFI,
    ).run()

    assert world.restarted_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("paused_at", [0, 15])
async def test_two_reasons_never_restart_inside_one_cooldown(tmp_path, paused_at):
    """The server-address rule and the cable rule share one cooldown.

    Here both stay true for good — a client on the server's WiFi address,
    streaming from its own WiFi address with the cable back — and each restart
    lands it there again. Whichever rule restarts first (the cable while the
    room plays, the address once it pauses), the other must wait the cooldown
    too, or the room takes two gaps where one was due.
    """
    restarts = []
    world = _World(
        tmp_path, dialled=WIFI, resolves=lambda t: WIRED,
        connected=lambda t: not any(r <= t < r + 3 for r in restarts),
        playing=lambda t: t < paused_at, local=lambda t: SAT_WIFI,
        carrier=lambda t: "1", wired=lambda t: SAT_WIRED, preferred=lambda t: SAT_WIRED,
        until=400, past_restarts=True,
    )
    restarts = world.restarts
    await world.run()

    assert len(world.restarts) >= 2, "neither rule restarted the stranded client"
    gaps = [b - a for a, b in zip(world.restarts, world.restarts[1:])]
    assert min(gaps) >= watch.COOLDOWN_INTERVAL
