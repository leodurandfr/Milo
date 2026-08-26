"""`core/system/hostname_conflict.py` — the service that had no test file at all.

It is the worst-covered file of the backend (25.0 %), and what it decides is not
a badge: `hostname_conflict: true` mounts `HostnameConflictView`, a **full-screen
takeover** that hides the whole UI behind "Another Milō detected — turn off this
device". A false positive therefore bricks the appliance's screen until the next
five-minute re-check, and a false negative leaves two servers fighting over
`milo.local` with AirPlay and Spotify Connect showing duplicates.

The boundary mocked here is the **outside world**: the three programs it spawns
(`avahi-resolve`, `avahi-browse`, `ip`) and systemd. Nothing of the unit's own is
patched. Every canned stdout below is a **verbatim capture from this appliance**
against the live LAN on 2026-08-26 — the parsable `avahi-browse` frame in
particular, whose ten `;`-separated fields the parser indexes by position:

    avahi-browse -rt -p _workstation._tcp
    =;eth0;IPv4;milo\\032\\0912c\\058cf…\\093;Workstation;local;milo.local;192.168.1.55;9;
    =;eth0;IPv6;…;Workstation;local;milo.local;2a01:e0a:1048:b5b0:e079:41ff:e835:8628;9;

    ip -4 -o addr show
    2: eth0    inet 192.168.1.55/24 brd … scope global dynamic noprefixroute eth0\\  …

    avahi-resolve -4 -n milo.local   ->  "milo.local\\t192.168.1.55"
    avahi-resolve -a 192.168.1.55    ->  "192.168.1.55\\tmilo1.home"

That last capture is the one to read twice: on this LAN the reverse lookup answers
from **unicast** DNS (`enable-wide-area=yes`, the Livebox's `.home` domain) and can
never return a `.local` name — see `test_a_reverse_lookup_that_answers_from_unicast_dns…`.

A spawn of anything other than those three programs raises: this file drives the
service that browses the LAN's mDNS and restarts avahi-daemon, and the repo is
checked out on the appliance itself.
"""
import asyncio
import logging
import types

import pytest
from unittest.mock import AsyncMock, patch

from backend.core.models.ws_events import SystemHostnameConflictChanged
from backend.core.system.hostname_conflict import (
    EXPECTED_FQDN,
    RECLAIM_COOLDOWN_S,
    HostnameConflictService,
)

MODULE = "backend.core.system.hostname_conflict"

# --------------------------------------------------------------------------- #
# Verbatim captures (this appliance, live LAN, 2026-08-26)
# --------------------------------------------------------------------------- #

IP_ADDR_SHOW = (
    "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever\n"
    "2: eth0    inet 192.168.1.55/24 brd 192.168.1.255 scope global dynamic noprefixroute eth0\\"
    "       valid_lft 42917sec preferred_lft 42917sec\n"
    "3: wlan0    inet 192.168.1.39/24 brd 192.168.1.255 scope global dynamic noprefixroute wlan0\\"
    "       valid_lft 42905sec preferred_lft 42905sec\n"
    "4: tailscale0    inet 100.117.193.57/32 scope global tailscale0\\"
    "       valid_lft forever preferred_lft forever\n"
)
LOCAL_IPS = {"127.0.0.1", "192.168.1.55", "192.168.1.39", "100.117.193.57"}

_NAME = "milo\\032\\0912c\\058cf\\05867\\058b9\\05846\\0586f\\093"


def browse_record(fqdn, ip, proto="IPv4"):
    """One resolved `avahi-browse -rt -p` line, in the shape the daemon prints."""
    return f"=;eth0;{proto};{_NAME};Workstation;local;{fqdn};{ip};9;"


# The self record as this unit really publishes it: both address families.
BROWSE_SELF_ONLY = "\n".join([
    "+;eth0;IPv6;" + _NAME + ";Workstation;local",
    "+;eth0;IPv4;" + _NAME + ";Workstation;local",
    browse_record("milo.local", "2a01:e0a:1048:b5b0:e079:41ff:e835:8628", "IPv6"),
    browse_record("milo.local", "192.168.1.55"),
    browse_record("milo-client-2.local", "192.168.1.153"),
]) + "\n"


# --------------------------------------------------------------------------- #
# The spawn boundary
# --------------------------------------------------------------------------- #

class FakeProc:
    def __init__(self, rc=0, stdout="", stderr="", hang=False):
        self.returncode = rc
        self._out = stdout.encode()
        self._err = stderr.encode()
        self._hang = hang
        self.killed = False
        self.waited = False

    async def communicate(self, input=None):
        if self._hang:
            # Bounded rather than forever: a mutation that drops the timeout
            # must make the test RED, not make the suite hang (lesson B5(b)).
            await asyncio.sleep(30)
        return self._out, self._err

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True
        return self.returncode


class FakeSpawn:
    """Stands in for the three binaries the service shells out to.

    Anything else raises: this service browses the appliance's own LAN and
    restarts avahi-daemon, so an un-doubled spawn must fail, not be spied on.
    """

    ALLOWED = ("avahi-resolve", "avahi-browse", "ip")

    def __init__(self, router):
        self.calls: list[tuple[str, ...]] = []
        self.procs: list[FakeProc] = []
        self._router = router

    async def __call__(self, program, *args, **kwargs):
        assert program in self.ALLOWED, f"unexpected program spawned: {program}"
        argv = (program, *args)
        self.calls.append(argv)
        answer = self._router(argv)
        if isinstance(answer, BaseException):
            raise answer
        self.procs.append(answer)
        return answer

    def argv_starting(self, *prefix) -> list[tuple[str, ...]]:
        return [c for c in self.calls if c[:len(prefix)] == prefix]


def default_router(*, resolve_name=("milo.local\t192.168.1.55", 0),
                   browse=(BROWSE_SELF_ONLY, 0), ip_out=(IP_ADDR_SHOW, 0)):
    """Build a router from the three answers the service can ask for."""
    def route(argv):
        if argv[0] == "ip":
            return FakeProc(ip_out[1], ip_out[0])
        if argv[0] == "avahi-browse":
            return FakeProc(browse[1], browse[0])
        if argv[0] == "avahi-resolve" and "-n" in argv:
            return FakeProc(resolve_name[1], resolve_name[0])
        raise AssertionError(f"unrouted argv: {argv}")

    return route


# The LAN as it looks when this unit lost the probe race and Avahi renamed it.
BROWSE_RENAMED_SELF = "\n".join([
    browse_record("milo-2.local", "2a01:e0a:1048:b5b0:e079:41ff:e835:8628", "IPv6"),
    browse_record("milo-2.local", "192.168.1.55"),
    browse_record("milo-client-2.local", "192.168.1.153"),
]) + "\n"


@pytest.fixture
def systemd():
    manager = AsyncMock()
    manager.restart = AsyncMock(return_value=True)
    return manager


@pytest.fixture
def service(systemd):
    svc = HostnameConflictService(systemd_manager=systemd)
    svc.set_state_machine(AsyncMock())
    return svc


@pytest.fixture(autouse=True)
def never_the_real_avahi():
    """No test in this file may reach the LAN's mDNS or this host's `ip`.

    Modelled on `test_rotary.py::never_the_real_gpio`: the default is a spawn
    that RAISES, so a path this file forgot to double fails loudly instead of
    browsing the real network.
    """
    async def _refuse(program, *args, **kwargs):
        raise AssertionError(
            f"a real process was spawned: {program} {' '.join(map(str, args))}"
        )

    with patch(f"{MODULE}.asyncio.create_subprocess_exec", new=_refuse):
        yield


def with_spawn(router):
    fake = FakeSpawn(router)
    return fake, patch(f"{MODULE}.asyncio.create_subprocess_exec", new=fake)


def frozen_clock(now=1_000_000.0):
    """Replace the module's `time` name only — never `time.time` globally."""
    clock = types.SimpleNamespace(now=now)
    fake = types.SimpleNamespace(time=lambda: clock.now)
    return clock, patch(f"{MODULE}.time", fake)


# --------------------------------------------------------------------------- #
# The satellite short-circuit
# --------------------------------------------------------------------------- #

class TestHostnameShortCircuit:
    """A unit whose OS hostname is not `milo` never browses anything."""

    async def test_a_satellite_is_never_in_conflict_and_spawns_nothing(self, service):
        """`milo-client` legitimately owns its own name; the whole detection is
        skipped. Asserting the spawn count is the point: the browse costs a 5 s
        multicast wait, and it would run on every satellite boot for nothing."""
        fake, spawn = with_spawn(default_router())
        with patch(f"{MODULE}.socket.gethostname", return_value="milo-client"), spawn:
            assert await service.check() is False

        assert fake.calls == []
        assert service.get_state()["advertised_name"] == "milo-client.local"

    async def test_the_satellite_branch_still_stamps_the_check_time(self, service):
        """`last_checked` is what the settings panel shows; a branch that skips
        the work must not also skip saying when it ran."""
        fake, spawn = with_spawn(default_router())
        with patch(f"{MODULE}.socket.gethostname", return_value="milo-client"), spawn:
            await service.check()
        assert service.get_state()["last_checked"] is not None


# --------------------------------------------------------------------------- #
# Owning milo.local
# --------------------------------------------------------------------------- #

class TestWeOwnMiloLocal:

    async def test_a_healthy_unit_reports_no_conflict(self, service):
        """The nominal path, and the one this appliance is measured on: the
        forward resolve answers one of our own addresses."""
        fake, spawn = with_spawn(default_router())
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            assert await service.check() is False

        state = service.get_state()
        assert state["hostname_conflict"] is False
        assert state["advertised_name"] == EXPECTED_FQDN
        assert state["local_ip"] == "192.168.1.55"
        assert state["other_milos"] == []

    async def test_owning_the_name_never_reverse_resolves_anything(self, service):
        """The reverse lookups exist only for the renamed case, and each costs up
        to 3 s under the service lock. Four addresses on this host = 12 s of
        multicast wait that the healthy path must not pay."""
        fake, spawn = with_spawn(default_router())
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            await service.check()
        assert fake.argv_starting("avahi-resolve", "-a") == []

    async def test_a_parasite_renamed_server_is_a_conflict_even_when_we_own_the_name(self, service):
        """The whole reason the browse exists. We hold `milo.local`, but a second
        Milō renamed itself to `milo-2.local`: it will stay orphaned for ever
        unless the owner is told, so this is reported as a conflict."""
        browse = BROWSE_SELF_ONLY + browse_record("milo-2.local", "192.168.1.77") + "\n"
        fake, spawn = with_spawn(default_router(browse=(browse, 0)))
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            assert await service.check() is True

        state = service.get_state()
        assert state["other_milos"] == ["milo-2.local"]
        # Still ours: the banner must not tell the owner to shut *this* one down
        # for a name it legitimately holds.
        assert state["advertised_name"] == EXPECTED_FQDN

    async def test_a_satellite_is_not_a_parasite_server(self, service):
        """`milo-client-2.local` is a legitimately renamed *satellite* and is in
        the browse output of every real multiroom install. Matching it would put
        a permanent takeover on every unit that has two speakers."""
        fake, spawn = with_spawn(default_router())
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            assert await service.check() is False
        assert service.get_state()["other_milos"] == []


# --------------------------------------------------------------------------- #
# The parasite scan
# --------------------------------------------------------------------------- #

class TestRenamedMiloScan:

    async def test_our_own_record_is_excluded_by_address_not_by_name(self, service):
        """When *we* are the renamed one, the browse carries our own
        `milo-2.local` and it must not be reported as somebody else's server —
        the appliance would be telling its owner to unplug itself.

        The address is the only discriminator available: the frame's name field
        is the same shape for every Milō on the LAN.
        """
        browse = browse_record("milo-2.local", "192.168.1.55") + "\n"
        fake, spawn = with_spawn(default_router(browse=(browse, 0)))
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            await service.check()
        assert service.get_state()["other_milos"] == []

    async def test_a_renamed_peer_is_listed_once_despite_two_address_families(self, service):
        """avahi-browse resolves every host twice, once per family. The list is
        what the warning names, so a peer must appear once, not twice."""
        browse = "\n".join([
            browse_record("milo-2.local", "2a01:e0a:1048:b5b0::77", "IPv6"),
            browse_record("milo-2.local", "192.168.1.77"),
        ]) + "\n"
        fake, spawn = with_spawn(default_router(browse=(browse, 0)))
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            await service.check()
        assert service.get_state()["other_milos"] == ["milo-2.local"]

    async def test_unresolved_and_short_frames_are_skipped(self, service):
        """`+` lines are the announce half of the same browse and carry no
        address; a truncated `=` line has no fqdn field to read."""
        browse = "\n".join([
            "+;eth0;IPv4;" + _NAME + ";Workstation;local",
            "=;eth0;IPv4;name;Workstation;local",          # 6 fields, truncated
            "",
            browse_record("milo-3.local", "192.168.1.88"),
        ]) + "\n"
        fake, spawn = with_spawn(default_router(browse=(browse, 0)))
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            await service.check()
        assert service.get_state()["other_milos"] == ["milo-3.local"]

    async def test_a_browse_that_times_out_kills_the_process_and_reports_nothing(self, service):
        """Five seconds is the MX wait; if the daemon never answers, the check
        must give up rather than hold the service lock for ever — and it must
        reap the child, or every five-minute cycle leaks one avahi-browse."""
        hung = FakeProc(hang=True)

        def route(argv):
            if argv[0] == "avahi-browse":
                return hung
            return default_router()(argv)

        fake, spawn = with_spawn(route)
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), \
                patch(f"{MODULE}.BROWSE_TIMEOUT_S", 0.05), spawn:
            assert await service.check() is False
        assert hung.killed and hung.waited
        assert service.get_state()["other_milos"] == []

    async def test_a_browse_that_exits_non_zero_yields_no_peers(self, service):
        """14th blind spot: the frames are parsed next to an exit code, and a
        daemon that refused prints nothing useful on stdout either way."""
        fake, spawn = with_spawn(default_router(
            browse=(browse_record("milo-2.local", "192.168.1.77"), 1)
        ))
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            assert await service.check() is False
        assert service.get_state()["other_milos"] == []

    async def test_a_host_without_avahi_browse_is_not_an_error(self, service):
        """Fail open: a dev host with no avahi installed must still boot."""
        def route(argv):
            if argv[0] == "avahi-browse":
                return FileNotFoundError("avahi-browse")
            return default_router()(argv)

        fake, spawn = with_spawn(route)
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            assert await service.check() is False


# --------------------------------------------------------------------------- #
# We do not own milo.local
# --------------------------------------------------------------------------- #

class TestSomebodyElseOwnsMiloLocal:

    async def test_a_name_held_by_a_remote_address_is_a_conflict(self, service):
        """The case the takeover exists for: `milo.local` answers, and the
        address is not one of ours. Nothing of ours is advertised under the
        expected name, so `advertised_name` is left empty rather than guessed."""
        fake, spawn = with_spawn(default_router(
            resolve_name=("milo.local\t192.168.1.99", 0),
            browse=(browse_record("milo.local", "192.168.1.99") + "\n", 0),
        ))
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            assert await service.check() is True

        state = service.get_state()
        assert state["advertised_name"] is None
        assert state["local_ip"] is not None

    async def test_the_reported_address_is_the_lowest_non_loopback_one(self, service):
        """`local_ip` is printed in the takeover ("This device: {name} ({ip})"),
        and `_get_local_ips` returns a *set*: without the sort, which of eth0,
        wlan0 and tailscale0 the owner is shown changes with hash randomisation
        between reboots, on the one screen that must identify the box."""
        fake, spawn = with_spawn(default_router(
            resolve_name=("milo.local\t192.168.1.99", 0),
            browse=(browse_record("milo.local", "192.168.1.99") + "\n", 0),
        ))
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            await service.check()
        # 100.117.193.57 < 192.168.1.39 < 192.168.1.55 as strings, and 127.0.0.1
        # is excluded by name.
        assert service.get_state()["local_ip"] == "100.117.193.57"

    def test_the_lowest_address_is_picked_whatever_order_they_arrive_in(self):
        """Deterministic half of the test above. A set iterates in hash order,
        which is fixed inside one process — so a single-process assertion on a
        set can only catch a dropped `sorted()` by luck (one chance in n). Fed
        an iterable that is *already* in the opposite order, the sort is the only
        thing that can produce the answer."""
        descending = ["192.168.1.55", "192.168.1.39", "127.0.0.1", "100.117.193.57"]
        assert HostnameConflictService._first_non_loopback(descending) == "100.117.193.57"

    def test_a_host_with_only_loopback_reports_no_address(self):
        """A unit whose interfaces are all down: the takeover then shows the
        name alone rather than a fabricated address."""
        assert HostnameConflictService._first_non_loopback({"127.0.0.1"}) is None

    async def test_the_name_is_never_read_by_reverse_resolution(self, service):
        """`avahi-resolve -a` answers from unicast DNS whenever
        `enable-wide-area=yes` — which Milō ships — so on this LAN it returns the
        router's `.home` name for every address, including one whose mDNS name is
        provably `milo-client-2.local`. It can therefore never say "we are
        advertised as milo.local", and using it would turn every silent-Avahi
        moment into a full-screen takeover. Measured 2026-08-26; the browse is
        the only mDNS-only source."""
        fake, spawn = with_spawn(default_router(resolve_name=("", 1)))
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            await service.check()
        assert fake.argv_starting("avahi-resolve", "-a") == []


# --------------------------------------------------------------------------- #
# Failing open
# --------------------------------------------------------------------------- #

class TestFailOpen:

    async def test_a_detection_that_raises_reports_no_conflict(self, service, caplog):
        """Fail open is the rule for the whole file: a detection that blew up
        must not put a full-screen takeover on the appliance. It must still say
        so at error level — this is the only trace the operator gets."""
        fake, spawn = with_spawn(default_router())
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn, \
                patch.object(service, "_detect_conflict",
                             side_effect=RuntimeError("avahi socket gone")), \
                caplog.at_level(logging.ERROR):
            assert await service.check() is False

        assert "avahi socket gone" in caplog.text

    async def test_a_host_with_no_ip_command_still_answers(self, service):
        """`ip` is missing on no real unit, but the whole service is written to
        keep a dev host bootable."""
        def route(argv):
            if argv[0] == "ip":
                return FileNotFoundError("ip")
            return default_router()(argv)

        fake, spawn = with_spawn(route)
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            # milo.local resolves to 192.168.1.55, which is not in the {127.0.0.1}
            # that survives — so this reads as somebody else holding the name.
            assert await service.check() is True

    async def test_a_host_with_no_avahi_resolve_reports_no_conflict(self, service):
        """Same fail-open as the browse, on the binary that decides ownership:
        with no avahi at all the unit must boot into a working UI, not into the
        takeover."""
        def route(argv):
            if argv[0] == "avahi-resolve":
                return FileNotFoundError("avahi-resolve")
            return default_router()(argv)

        fake, spawn = with_spawn(route)
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            assert await service.check() is False

    async def test_a_resolve_that_times_out_reaps_its_child(self, service):
        """Same leak as the browse, five minutes at a time: `avahi-resolve` is
        spawned on every cycle and a killed child that is never waited on stays
        a zombie for the life of the backend."""
        hung = FakeProc(hang=True)

        def route(argv):
            if argv[0] == "avahi-resolve":
                return hung
            return default_router()(argv)

        fake, spawn = with_spawn(route)
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), \
                patch(f"{MODULE}.RESOLVE_TIMEOUT_S", 0.05), spawn:
            await service.check()
        assert hung.killed and hung.waited

    async def test_a_resolve_answer_without_an_address_is_no_answer(self, service):
        """`avahi-resolve` prints "<query>\\t<answer>"; a run that exits 0 with a
        single token has resolved nothing, and reading token 0 would hand the
        query string back as if it were an address."""
        fake, spawn = with_spawn(default_router(
            resolve_name=("milo.local", 0), browse=("", 0)))
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            assert await service.check() is False
        assert service.get_state()["advertised_name"] is None


# --------------------------------------------------------------------------- #
# Reading this host's addresses
# --------------------------------------------------------------------------- #

class TestLocalAddresses:

    async def test_every_ipv4_address_of_this_host_counts_as_ours(self, service):
        """Ownership of `milo.local` is decided by set membership, so an address
        this parser drops is an interface the unit does not recognise as itself
        — and it then declares a conflict against its own wlan0."""
        fake, spawn = with_spawn(default_router(
            resolve_name=("milo.local\t192.168.1.39", 0),   # wlan0, not eth0
        ))
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            assert await service.check() is False
        assert service.get_state()["local_ip"] == "192.168.1.39"

    async def test_the_tailscale_address_is_read_too(self, service):
        """A /32 with no `brd` field parses through the same `inet` index."""
        fake, spawn = with_spawn(default_router(
            resolve_name=("milo.local\t100.117.193.57", 0),
        ))
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            assert await service.check() is False

    async def test_a_line_without_an_inet_token_is_skipped_not_fatal(self, service):
        """`ip -4 -o addr show` prints one line per address; a `tentative` or
        otherwise unexpected line must not abort the whole enumeration."""
        out = "2: eth0    inet6 fe80::1/64 scope link \\       valid_lft forever\n" + IP_ADDR_SHOW
        fake, spawn = with_spawn(default_router(
            ip_out=(out, 0),
            resolve_name=("milo.local\t192.168.1.55", 0),
        ))
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            assert await service.check() is False

    async def test_an_ip_command_that_hangs_is_killed_and_reaped(self, service):
        hung = FakeProc(hang=True)

        def route(argv):
            if argv[0] == "ip":
                return hung
            return default_router()(argv)

        fake, spawn = with_spawn(route)
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), \
                patch(f"{MODULE}.IP_LOCAL_TIMEOUT_S", 0.05), spawn:
            await service.check()
        assert hung.killed and hung.waited


# --------------------------------------------------------------------------- #
# Broadcasting the change
# --------------------------------------------------------------------------- #

class TestBroadcast:

    async def test_the_first_check_of_a_healthy_boot_broadcasts_nothing(self, service):
        """`_conflict` starts False, so a healthy boot is not a change. The
        frontend gets the same state from `GET /api/system/status` at boot; a
        broadcast per five-minute cycle would be pure noise on the socket."""
        fake, spawn = with_spawn(default_router())
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            await service.check()
        service._state_machine.broadcast.assert_not_awaited()

    async def test_the_transition_into_conflict_is_broadcast_once(self, service):
        """The takeover mounts on this event and on nothing else."""
        conflicted = default_router(
            resolve_name=("milo.local\t192.168.1.99", 0),
            browse=(browse_record("milo.local", "192.168.1.99") + "\n", 0))
        fake, spawn = with_spawn(conflicted)
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            await service.check()
            await service.check()

        broadcasts = service._state_machine.broadcast.await_args_list
        assert len(broadcasts) == 1
        event = broadcasts[0].args[0]
        assert isinstance(event, SystemHostnameConflictChanged)
        assert event.hostname_conflict is True
        assert event.expected_name == EXPECTED_FQDN

    async def test_the_transition_out_of_conflict_is_broadcast_too(self, service):
        """The takeover is full-screen with no way past it: if the recovery is
        not broadcast, turning the other Milō off leaves this one blocked until
        someone reloads the page."""
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"):
            fake, spawn = with_spawn(default_router(
                resolve_name=("milo.local\t192.168.1.99", 0),
                browse=(browse_record("milo.local", "192.168.1.99") + "\n", 0)))
            with spawn:
                assert await service.check() is True
            fake, spawn = with_spawn(default_router())
            with spawn:
                assert await service.check() is False

        events = [c.args[0] for c in service._state_machine.broadcast.await_args_list]
        assert [e.hostname_conflict for e in events] == [True, False]

    async def test_a_service_with_no_state_machine_still_checks(self, service, systemd):
        """`set_state_machine` is a STEP 2 injection; a check that ran before it
        (or in a unit test) must not raise on the broadcast."""
        bare = HostnameConflictService(systemd_manager=systemd)
        fake, spawn = with_spawn(default_router(
            resolve_name=("milo.local\t192.168.1.99", 0),
            browse=(browse_record("milo.local", "192.168.1.99") + "\n", 0)))
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            assert await bare.check() is True


# --------------------------------------------------------------------------- #
# The avahi reclaim
# --------------------------------------------------------------------------- #

class TestAvahiReclaim:
    """Avahi never takes its principal name back on its own once it has been
    renamed, even after the peer that took it disappears. Restarting the daemon
    forces a re-probe — so this is the only thing that gets a stuck unit back to
    `milo.local` without a reboot."""

    @staticmethod
    def _renamed_and_orphaned():
        """We answer to `milo-2.local`, and nobody at all holds `milo.local`."""
        return default_router(resolve_name=("", 1), browse=(BROWSE_RENAMED_SELF, 0))

    async def test_a_renamed_orphan_restarts_avahi_to_reprobe(self, service, systemd):
        fake, spawn = with_spawn(self._renamed_and_orphaned())
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            assert await service.check() is True
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        systemd.restart.assert_awaited_once_with("avahi-daemon")

    async def test_the_restart_runs_outside_the_service_lock(self, service, systemd):
        """The docstring's own claim, and it is load-bearing: `restart()` waits
        on systemd for up to ten seconds, and `check()` is called concurrently
        by the boot init and the manual re-check button. Holding the lock across
        it would serialise the button behind a daemon restart."""
        released = asyncio.Event()

        async def slow_restart(unit):
            await released.wait()
            return True

        systemd.restart = AsyncMock(side_effect=slow_restart)
        fake, spawn = with_spawn(self._renamed_and_orphaned())
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            assert await service.check() is True
            # The reclaim is still parked on `released`; a second check must not
            # block behind it.
            second = asyncio.create_task(service.check())
            assert await asyncio.wait_for(second, timeout=2) is True
            released.set()
            await service.cleanup()

    async def test_a_second_check_inside_the_cooldown_does_not_restart_again(self, service, systemd):
        """Without the cooldown the five-minute loop restarts avahi-daemon every
        cycle for as long as the rename sticks — and each restart drops every
        mDNS advertisement the unit has, AirPlay included."""
        clock, clock_patch = frozen_clock()
        fake, spawn = with_spawn(self._renamed_and_orphaned())
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), clock_patch, spawn:
            await service.check()
            clock.now += RECLAIM_COOLDOWN_S - 1
            await service.check()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            await service.cleanup()

        assert systemd.restart.await_count == 1

    async def test_the_cooldown_expires_and_the_reclaim_is_tried_again(self, service, systemd):
        """It is a cooldown, not a one-shot: a reclaim that did not take must be
        retried, or a unit that lost a probe race stays on `milo-2` for ever."""
        clock, clock_patch = frozen_clock()
        fake, spawn = with_spawn(self._renamed_and_orphaned())
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), clock_patch, spawn:
            await service.check()
            clock.now += RECLAIM_COOLDOWN_S + 1
            await service.check()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            await service.cleanup()

        assert systemd.restart.await_count == 2

    async def test_a_peer_really_holding_the_name_is_not_reclaimed(self, service, systemd):
        """The reclaim is for the *orphan* case only. Re-probing while another
        device genuinely owns `milo.local` just loses the race again and drops
        this unit's advertisements for nothing."""
        fake, spawn = with_spawn(default_router(
            resolve_name=("milo.local\t192.168.1.99", 0),
            browse=("\n".join([
                browse_record("milo-2.local", "192.168.1.55"),
                browse_record("milo.local", "192.168.1.99"),
            ]) + "\n", 0),
        ))
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            assert await service.check() is True
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        systemd.restart.assert_not_awaited()

    async def test_a_unit_that_is_not_in_conflict_is_never_reclaimed(self, service, systemd):
        fake, spawn = with_spawn(default_router())
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            await service.check()
            await asyncio.sleep(0)
        systemd.restart.assert_not_awaited()


# --------------------------------------------------------------------------- #
# The periodic loop and teardown
# --------------------------------------------------------------------------- #

class TestPeriodicLoop:

    async def test_a_check_that_raises_does_not_kill_the_loop(self, service, caplog):
        """It is a bare `create_task` with nobody watching it: a loop that dies
        leaves conflict detection stopped until the next backend restart, with
        no trace beyond one line."""
        calls = []

        async def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("avahi gone")
            return False

        with patch.object(service, "check", side_effect=flaky), \
                patch(f"{MODULE}.PERIODIC_INTERVAL_S", 0.01), \
                caplog.at_level(logging.ERROR):
            service.start_periodic()
            for _ in range(200):
                if len(calls) >= 2:
                    break
                await asyncio.sleep(0.005)
            await service.cleanup()

        assert len(calls) >= 2
        assert "avahi gone" in caplog.text

    async def test_start_periodic_twice_leaves_one_loop(self, service):
        """`start_periodic()` is called once from `initialize_services`, but the
        guard is what keeps a second call from leaking a task nothing cancels."""
        with patch.object(service, "check", new=AsyncMock(return_value=False)), \
                patch(f"{MODULE}.PERIODIC_INTERVAL_S", 60):
            service.start_periodic()
            first = service._periodic_task
            service.start_periodic()
            assert service._periodic_task is first
            await service.cleanup()

    async def test_cleanup_stops_the_loop_and_drains_the_reclaim(self, service, systemd):
        """`main.py` calls this on teardown; a reclaim still in flight would
        restart avahi-daemon after the backend has already gone."""
        parked = asyncio.Event()

        async def never_returns(unit):
            await parked.wait()

        systemd.restart = AsyncMock(side_effect=never_returns)
        fake, spawn = with_spawn(TestAvahiReclaim._renamed_and_orphaned())
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            await service.check()
            with patch.object(service, "check", new=AsyncMock(return_value=False)):
                service.start_periodic()
                await asyncio.sleep(0)
                await asyncio.wait_for(service.cleanup(), timeout=2)

        assert service._periodic_task is None
        assert service._bg._tasks == set()

    async def test_cleanup_is_safe_on_a_service_that_never_started(self, service):
        await service.cleanup()
        await service.cleanup()


# --------------------------------------------------------------------------- #
# The payload
# --------------------------------------------------------------------------- #

class TestState:

    async def test_get_state_carries_every_key_the_status_route_returns(self, service):
        """`GET /api/system/status` merges this dict straight into its payload,
        and `systemStore` reads three of the keys by name."""
        assert set(service.get_state()) == {
            "hostname_conflict", "last_checked", "advertised_name",
            "local_ip", "expected_name", "other_milos",
        }

    async def test_the_peer_list_handed_out_is_a_copy(self, service):
        """The route serialises it while the five-minute loop can be rewriting
        `_other_milos`; handing out the live list makes that a mutation during
        iteration."""
        browse = BROWSE_SELF_ONLY + browse_record("milo-2.local", "192.168.1.77") + "\n"
        fake, spawn = with_spawn(default_router(browse=(browse, 0)))
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            await service.check()

        handed_out = service.get_state()["other_milos"]
        handed_out.append("milo-9.local")
        assert service.get_state()["other_milos"] == ["milo-2.local"]


# --------------------------------------------------------------------------- #
# Reading our own advertised name (the branch the reverse lookup could not reach)
# --------------------------------------------------------------------------- #

class TestOwnAdvertisement:
    """Before 2026-08-26 this was `avahi-resolve -a <our ip>`, and on this LAN
    that answers from unicast DNS — the router's `.home` name for every address,
    never a `.local` one. The "we are advertised correctly" verdict was
    therefore unreachable, and any moment where `milo.local` failed to resolve
    became a full-screen takeover plus an avahi-daemon restart."""

    async def test_a_silent_forward_resolve_is_not_a_conflict_when_we_own_the_name(
            self, service, systemd):
        """The case the old path got wrong. `avahi-resolve -n milo.local` fails
        — the daemon is mid-probe, or was just restarted — while the browse
        still shows this host publishing `milo.local`. Nothing is in conflict."""
        fake, spawn = with_spawn(default_router(resolve_name=("", 1)))
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            assert await service.check() is False
            await asyncio.sleep(0)

        state = service.get_state()
        assert state["advertised_name"] == EXPECTED_FQDN
        assert state["local_ip"] == "192.168.1.55"
        systemd.restart.assert_not_awaited()

    async def test_a_renamed_unit_reads_its_real_avahi_name(self, service):
        """`milo-2.local` is what the takeover shows the owner, and what
        `_should_attempt_reclaim` compares against `milo.local`."""
        fake, spawn = with_spawn(TestAvahiReclaim._renamed_and_orphaned())
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            assert await service.check() is True
            await service.cleanup()

        state = service.get_state()
        assert state["advertised_name"] == "milo-2.local"
        assert state["local_ip"] == "192.168.1.55"

    async def test_a_renamed_unit_does_not_list_itself_among_the_peers(self, service):
        """The browse resolves every host twice, once per family, and
        `_get_local_ips` reads `ip -4`. An IPv6 frame can never be recognised as
        ours, so without the family filter this unit finds its own AAAA record
        and tells its owner to turn this device off."""
        fake, spawn = with_spawn(TestAvahiReclaim._renamed_and_orphaned())
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            await service.check()
            await service.cleanup()

        assert service.get_state()["other_milos"] == []

    async def test_a_unit_avahi_publishes_nothing_for_is_not_in_conflict(
            self, service, systemd):
        """avahi-daemon down, or bound to an interface that is: no record, no
        name, no conflict — and no reclaim, because restarting a daemon that is
        not publishing does not make a second Milō appear."""
        fake, spawn = with_spawn(default_router(resolve_name=("", 1), browse=("", 0)))
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            assert await service.check() is False
            await asyncio.sleep(0)

        assert service.get_state()["advertised_name"] is None
        systemd.restart.assert_not_awaited()

    async def test_a_dual_interface_host_reports_one_stable_name(self, service):
        """Same reason as `_first_non_loopback`: a host advertising on eth0 and
        wlan0 appears twice in the browse, and the name shown to the owner must
        not change with the order the daemon happened to answer in."""
        browse = "\n".join([
            browse_record("milo-3.local", "192.168.1.39"),
            browse_record("milo-2.local", "192.168.1.55"),
        ]) + "\n"
        fake, spawn = with_spawn(default_router(resolve_name=("", 1), browse=(browse, 0)))
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            await service.check()
            await service.cleanup()

        assert service.get_state()["advertised_name"] == "milo-2.local"

    async def test_a_peer_holding_the_name_leaves_us_without_one(self, service):
        """We publish nothing (the peer won the probe) and somebody else holds
        `milo.local`: that is the conflict the takeover exists for, and the name
        field stays empty rather than borrowing the peer's."""
        browse = browse_record("milo.local", "192.168.1.99") + "\n"
        fake, spawn = with_spawn(default_router(
            resolve_name=("milo.local\t192.168.1.99", 0), browse=(browse, 0)))
        with patch(f"{MODULE}.socket.gethostname", return_value="milo"), spawn:
            assert await service.check() is True

        assert service.get_state()["advertised_name"] is None
