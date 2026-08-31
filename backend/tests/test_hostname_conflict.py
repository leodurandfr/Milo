"""`core/system/hostname_conflict.py` — the service behind the full-screen takeover.

What it decides is not a badge: `hostname_conflict: true` mounts
`HostnameConflictView`, which hides the whole UI behind "Another Milō detected —
turn off this device". A false positive therefore bricks the appliance's screen
until the next re-check, and a false negative leaves two servers fighting over
`milo.local` with AirPlay and Spotify Connect showing duplicates.

Two boundaries are doubled here, both of them the **outside world**:

  * **Avahi's system-bus interface** (`org.freedesktop.Avahi.Server`), which
    answers `GetHostNameFqdn` — "did I get renamed?" — and `GetState`. This is
    the verdict's only source. Until 2026-08-31 the same fact was *inferred*
    from `avahi-resolve` + `ip -4`, and the two were sampled ~9 s apart: on a
    plain reboot the unit compared a pre-DHCP address list against an answer
    naming its own fresh lease, failed to recognise itself, and put the takeover
    on the screen. `TestTheBootRace` replays that exact boot.
  * **The three programs it spawns** (`avahi-browse`, `avahi-resolve`, `ip`).
    The browse now serves one question only — is another, already renamed
    `milo-N.local` server on the LAN — `avahi-resolve` only decides the reclaim,
    and `ip` only picks the address printed in the takeover.

Every canned stdout below is a verbatim capture from this appliance against the
live LAN (browse + `ip` on 2026-08-26, D-Bus answers on 2026-08-31):

    avahi-browse -rt -p _workstation._tcp
    =;eth0;IPv4;milo\\032\\0912c\\058cf…\\093;Workstation;local;milo.local;192.168.1.55;9;
    =;eth0;IPv6;…;Workstation;local;milo.local;2a01:e0a:1048:b5b0:e079:41ff:e835:8628;9;

    ip -4 -o addr show
    2: eth0    inet 192.168.1.55/24 brd … scope global dynamic noprefixroute eth0\\  …

    busctl call org.freedesktop.Avahi / org.freedesktop.Avahi.Server GetHostNameFqdn
    s "milo.local"

A spawn of anything other than those three programs raises, and so does a real
`MessageBus`: this file drives the service that browses the LAN's mDNS and
restarts avahi-daemon, and the repo is checked out on the appliance itself.
"""
import asyncio
import contextlib
import logging
import types

import pytest
from unittest.mock import AsyncMock, patch

from backend.core.models.ws_events import SystemHostnameConflictChanged
from backend.core.system.hostname_conflict import (
    AVAHI_SERVER_COLLISION,
    AVAHI_SERVER_REGISTERING,
    AVAHI_SERVER_RUNNING,
    EXPECTED_FQDN,
    RECLAIM_COOLDOWN_S,
    HostnameConflictService,
)

MODULE = "backend.core.system.hostname_conflict"

# --------------------------------------------------------------------------- #
# Verbatim captures (this appliance, live LAN)
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

# The same host 9 s into the boot of 2026-08-31: tailscale0 is up, neither DHCP
# lease has landed yet. This is the snapshot the old code judged itself on.
IP_ADDR_SHOW_BOOT = (
    "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever\n"
    "4: tailscale0    inet 100.117.193.57/32 scope global tailscale0\\"
    "       valid_lft forever preferred_lft forever\n"
)

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

# The LAN as it looks when this unit lost the probe race and Avahi renamed it.
BROWSE_RENAMED_SELF = "\n".join([
    browse_record("milo-2.local", "2a01:e0a:1048:b5b0:e079:41ff:e835:8628", "IPv6"),
    browse_record("milo-2.local", "192.168.1.55"),
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


# --------------------------------------------------------------------------- #
# The Avahi D-Bus boundary
# --------------------------------------------------------------------------- #

class FakeServerIface:
    """`org.freedesktop.Avahi.Server` as dbus-next exposes it — the two reads
    the verdict is made of, plus the signal subscription."""

    def __init__(self, fqdn, state, error):
        self.fqdn = fqdn
        self.state = state
        self.error = error
        self.reads = 0
        self.handlers: list = []
        self.detached = 0

    async def call_get_host_name_fqdn(self):
        self.reads += 1
        if self.error:
            raise self.error
        return self.fqdn

    async def call_get_state(self):
        if self.error:
            raise self.error
        return self.state

    def on_state_changed(self, handler):
        self.handlers.append(handler)

    def off_state_changed(self, handler):
        self.detached += 1
        self.handlers.remove(handler)

    def emit(self, state, error=""):
        for handler in list(self.handlers):
            handler(state, error)


class FakeBus:
    def __init__(self, iface):
        self.iface = iface
        self.connected = True

    async def connect(self):
        return self

    async def introspect(self, name, path):
        return types.SimpleNamespace(name=name, path=path)

    def get_proxy_object(self, name, path, introspection):
        return types.SimpleNamespace(get_interface=lambda _iface: self.iface)

    def disconnect(self):
        self.connected = False


class FakeAvahi:
    """The `MessageBus` constructor, doubled. Counts connections: a proxy that
    is never rebuilt after a failure is a service that stays blind until the
    next reboot."""

    def __init__(self, iface, connect_error=None):
        self.iface = iface
        self.connect_error = connect_error
        self.connects = 0
        self.buses: list[FakeBus] = []

    def __call__(self, *, bus_type=None):
        self.connects += 1
        if self.connect_error:
            raise self.connect_error
        bus = FakeBus(self.iface)
        self.buses.append(bus)
        return bus


def avahi(fqdn=EXPECTED_FQDN, state=AVAHI_SERVER_RUNNING, error=None, connect_error=None):
    return FakeAvahi(FakeServerIface(fqdn, state, error), connect_error)


@contextlib.contextmanager
def on_unit(router=None, dbus=None, hostname="milo"):
    """The unit as `check()` sees it: OS hostname, the three binaries, Avahi."""
    spawn = FakeSpawn(router if router is not None else default_router())
    bus = dbus if dbus is not None else avahi()
    with patch(f"{MODULE}.socket.gethostname", return_value=hostname), \
            patch(f"{MODULE}.asyncio.create_subprocess_exec", new=spawn), \
            patch(f"{MODULE}.MessageBus", new=bus):
        yield spawn, bus


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
    """No test in this file may reach the LAN's mDNS, this host's `ip`, or the
    system bus.

    Modelled on `test_rotary.py::never_the_real_gpio`: the default is a spawn
    and a bus connection that RAISE, so a path this file forgot to double fails
    loudly instead of talking to the appliance's own Avahi.
    """
    async def _refuse_spawn(program, *args, **kwargs):
        raise AssertionError(
            f"a real process was spawned: {program} {' '.join(map(str, args))}"
        )

    def _refuse_bus(*args, **kwargs):
        raise AssertionError("a real system bus connection was attempted")

    with patch(f"{MODULE}.asyncio.create_subprocess_exec", new=_refuse_spawn), \
            patch(f"{MODULE}.MessageBus", new=_refuse_bus):
        yield


def frozen_clock(now=1_000_000.0):
    """Replace the module's `time` name only — never `time.time` globally."""
    clock = types.SimpleNamespace(now=now)
    fake = types.SimpleNamespace(time=lambda: clock.now)
    return clock, patch(f"{MODULE}.time", fake)


async def settle(predicate, timeout=2.0):
    """Yield until a background task the service spawned has landed.

    A synchronisation helper, not a latency budget: the timeout only keeps a
    regression from hanging the suite.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("the spawned background check never ran")
        await asyncio.sleep(0.005)


# --------------------------------------------------------------------------- #
# The satellite short-circuit
# --------------------------------------------------------------------------- #

class TestHostnameShortCircuit:
    """A unit whose OS hostname is not `milo` never asks anything."""

    async def test_a_satellite_is_never_in_conflict_and_touches_nothing(self, service):
        """`milo-client` legitimately owns its own name; the whole detection is
        skipped. Asserting the spawn and connect counts is the point: the browse
        costs a 5 s multicast wait, and it would run on every satellite boot for
        nothing."""
        with on_unit(hostname="milo-client") as (spawn, bus):
            assert await service.check() is False

        assert spawn.calls == []
        assert bus.connects == 0
        assert service.get_state()["advertised_name"] == "milo-client.local"

    async def test_the_satellite_branch_still_stamps_the_check_time(self, service):
        """`last_checked` is what the settings panel shows; a branch that skips
        the work must not also skip saying when it ran."""
        with on_unit(hostname="milo-client"):
            await service.check()
        assert service.get_state()["last_checked"] is not None


# --------------------------------------------------------------------------- #
# Owning milo.local
# --------------------------------------------------------------------------- #

class TestWeOwnMiloLocal:

    async def test_a_healthy_unit_reports_no_conflict(self, service):
        """The nominal path, and the one this appliance is measured on: Avahi
        answers `milo.local` and says it has finished probing."""
        with on_unit():
            assert await service.check() is False

        state = service.get_state()
        assert state["hostname_conflict"] is False
        assert state["advertised_name"] == EXPECTED_FQDN
        assert state["other_milos"] == []

    async def test_owning_the_name_costs_no_resolve(self, service):
        """`avahi-resolve` is a 3 s multicast wait and now answers one question
        only — does anybody still hold the name we lost. A unit that never lost
        it must not pay for it on every five-minute cycle."""
        with on_unit() as (spawn, _bus):
            await service.check()
        assert spawn.argv_starting("avahi-resolve") == []

    async def test_a_parasite_renamed_server_is_a_conflict_even_when_we_own_the_name(self, service):
        """The whole reason the browse survives. We hold `milo.local`, but a
        second Milō renamed itself to `milo-2.local`: it will stay orphaned for
        ever unless the owner is told, so this is reported as a conflict."""
        browse = BROWSE_SELF_ONLY + browse_record("milo-2.local", "192.168.1.77") + "\n"
        with on_unit(default_router(browse=(browse, 0))):
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
        with on_unit():
            assert await service.check() is False
        assert service.get_state()["other_milos"] == []


# --------------------------------------------------------------------------- #
# The boot race this service was rewritten for
# --------------------------------------------------------------------------- #

class TestTheBootRace:
    """Reboot of 2026-08-31, reproduced twice in a row on this unit.

    `NetworkManager-wait-online` is masked on purpose, so milo-backend starts
    ~9 s before either DHCP lease lands. The old detection sampled `ip -4`
    first and `avahi-resolve milo.local` last, and the answer named an address
    acquired in between — so the unit did not recognise itself and mounted the
    takeover, showing "This device: ? (100.117.193.57)".
    """

    async def test_a_lease_that_lands_mid_check_is_not_another_milo(self, service):
        """The verdict now comes from Avahi's own name, which no interface
        timing can move."""
        with on_unit(default_router(ip_out=(IP_ADDR_SHOW_BOOT, 0))):
            assert await service.check() is False
        assert service.get_state()["advertised_name"] == EXPECTED_FQDN

    async def test_a_daemon_still_probing_decides_nothing(self, service):
        """`REGISTERING` is the boot window itself: the daemon has not settled
        on a name, so there is nothing to conclude — and a takeover shown here
        is a takeover shown wrongly.

        The LAN below is what makes that load-bearing: mid-probe the browse
        cache can already carry a `milo-N.local` frame (the peer that just
        renamed itself, or our own record from before the rename) while Avahi
        still answers `milo.local` for us. Judged, that reads as a parasite
        server and mounts the takeover; it must be judged after the daemon
        settles, on the signal it fires when it does.
        """
        browse = BROWSE_SELF_ONLY + browse_record("milo-2.local", "192.168.1.77") + "\n"
        with on_unit(default_router(browse=(browse, 0)),
                     dbus=avahi(state=AVAHI_SERVER_REGISTERING)):
            assert await service.check() is False
        assert service.get_state()["other_milos"] == []

    async def test_a_daemon_still_probing_does_not_pay_for_a_browse(self, service):
        """Same window, the cost side: a 5 s multicast wait inside the startup
        gather, for an answer that cannot be used."""
        with on_unit(dbus=avahi(state=AVAHI_SERVER_REGISTERING)) as (spawn, _bus):
            await service.check()
        assert spawn.argv_starting("avahi-browse") == []


# --------------------------------------------------------------------------- #
# Avahi renamed us
# --------------------------------------------------------------------------- #

class TestAvahiRenamedUs:

    async def test_a_renamed_unit_is_in_conflict_and_says_under_which_name(self, service):
        """The case the takeover exists for. `milo-2.local` is what it shows the
        owner, and what `_should_attempt_reclaim` compares against."""
        with on_unit(default_router(browse=(BROWSE_RENAMED_SELF, 0)),
                     dbus=avahi(fqdn="milo-2.local")):
            assert await service.check() is True
            await service.cleanup()

        assert service.get_state()["advertised_name"] == "milo-2.local"

    async def test_a_collision_state_is_a_conflict_whatever_the_name_says(self, service):
        """`COLLISION` is the daemon reporting the collision itself, records
        withdrawn, before it has picked an alternative name. Reading the name
        alone would call that healthy."""
        with on_unit(dbus=avahi(state=AVAHI_SERVER_COLLISION)):
            assert await service.check() is True

    async def test_a_collision_is_never_reclaimed(self, service, systemd):
        """Re-probing while the peer that took the name is still there just
        loses the race again and drops every advertisement the unit has."""
        with on_unit(dbus=avahi(fqdn="milo-2.local", state=AVAHI_SERVER_COLLISION)):
            assert await service.check() is True
            await asyncio.sleep(0)
        systemd.restart.assert_not_awaited()

    async def test_a_renamed_unit_does_not_list_itself_among_the_peers(self, service):
        """Its own `milo-2.local` record is in the browse, in both address
        families. Reporting it would be the appliance telling its owner to
        unplug the box they are looking at."""
        with on_unit(default_router(browse=(BROWSE_RENAMED_SELF, 0), resolve_name=("", 1)),
                     dbus=avahi(fqdn="milo-2.local")):
            await service.check()
            await service.cleanup()

        assert service.get_state()["other_milos"] == []


# --------------------------------------------------------------------------- #
# The parasite scan
# --------------------------------------------------------------------------- #

class TestRenamedMiloScan:

    async def test_a_renamed_peer_is_listed_once_despite_two_address_families(self, service):
        """avahi-browse resolves every host twice, once per family. The list is
        what the warning names, so a peer must appear once, not twice."""
        browse = "\n".join([
            browse_record("milo-2.local", "2a01:e0a:1048:b5b0::77", "IPv6"),
            browse_record("milo-2.local", "192.168.1.77"),
        ]) + "\n"
        with on_unit(default_router(browse=(browse, 0))):
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
        with on_unit(default_router(browse=(browse, 0))):
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

        with on_unit(route), patch(f"{MODULE}.BROWSE_TIMEOUT_S", 0.05):
            assert await service.check() is False
        assert hung.killed and hung.waited
        assert service.get_state()["other_milos"] == []

    async def test_a_browse_that_exits_non_zero_yields_no_peers(self, service):
        """The frames are parsed next to an exit code, and a daemon that refused
        prints nothing useful on stdout either way."""
        with on_unit(default_router(
                browse=(browse_record("milo-2.local", "192.168.1.77"), 1))):
            assert await service.check() is False
        assert service.get_state()["other_milos"] == []

    async def test_a_host_without_avahi_browse_is_not_an_error(self, service):
        """Fail open: a dev host with no avahi-utils installed must still boot."""
        def route(argv):
            if argv[0] == "avahi-browse":
                return FileNotFoundError("avahi-browse")
            return default_router()(argv)

        with on_unit(route):
            assert await service.check() is False


# --------------------------------------------------------------------------- #
# Failing open
# --------------------------------------------------------------------------- #

class TestFailOpen:

    async def test_a_bus_that_cannot_be_reached_reports_no_conflict(self, service, caplog):
        """No system bus at all (dev host, container): the unit must boot into a
        working UI, not into the takeover — and must say so once per cycle,
        which is the only trace the operator gets."""
        with on_unit(dbus=avahi(connect_error=OSError("no system bus"))), \
                caplog.at_level(logging.WARNING):
            assert await service.check() is False

        assert "no system bus" in caplog.text
        assert service.get_state()["advertised_name"] is None

    async def test_a_daemon_that_stops_answering_reports_no_conflict(self, service):
        """avahi-daemon down or mid-restart: the call raises where the old code
        simply got no mDNS answer. Same fail-open verdict."""
        with on_unit(dbus=avahi(error=RuntimeError("org.freedesktop.DBus.Error.NoReply"))):
            assert await service.check() is False

    async def test_the_proxy_is_rebuilt_after_a_failed_call(self, service):
        """`_attempt_avahi_reclaim` restarts avahi-daemon, and the backend
        starts in the same second as it at boot. A proxy that is never rebuilt
        leaves detection blind until the next reboot — the failure mode this
        service exists to avoid, one layer down."""
        broken = avahi(error=RuntimeError("NoReply"))
        with on_unit(dbus=broken):
            assert await service.check() is False
            broken.iface.error = None
            assert await service.check() is False
            await service.cleanup()

        assert broken.connects == 2
        assert service.get_state()["advertised_name"] == EXPECTED_FQDN

    async def test_a_detection_that_raises_reports_no_conflict(self, service, caplog):
        """Fail open is the rule for the whole file: a detection that blew up
        must not put a full-screen takeover on the appliance."""
        with on_unit(), \
                patch.object(service, "_detect_conflict",
                             side_effect=RuntimeError("avahi socket gone")), \
                caplog.at_level(logging.ERROR):
            assert await service.check() is False

        assert "avahi socket gone" in caplog.text

    async def test_a_host_with_no_ip_command_still_answers(self, service):
        """`ip` is missing on no real unit, but the whole service is written to
        keep a dev host bootable — and the address is decoration now, so its
        absence cannot change a verdict."""
        def route(argv):
            if argv[0] == "ip":
                return FileNotFoundError("ip")
            return default_router()(argv)

        with on_unit(route):
            assert await service.check() is False
        assert service.get_state()["local_ip"] is None

    async def test_an_ip_command_that_hangs_is_killed_and_reaped(self, service):
        """A killed child that is never waited on stays a zombie for the life of
        the backend, one per five-minute cycle."""
        hung = FakeProc(hang=True)

        def route(argv):
            if argv[0] == "ip":
                return hung
            return default_router()(argv)

        with on_unit(route), patch(f"{MODULE}.IP_LOCAL_TIMEOUT_S", 0.05):
            await service.check()
        assert hung.killed and hung.waited


# --------------------------------------------------------------------------- #
# The address printed in the takeover
# --------------------------------------------------------------------------- #

class TestDisplayAddress:
    """`local_ip` is printed in the takeover ("This device: {name} ({ip})") and
    is the only thing identifying which box is speaking."""

    async def test_the_lan_address_is_shown_not_the_tailscale_one(self, service):
        """The takeover of 2026-08-31 read "? (100.117.193.57)". A CGNAT address
        names nothing on the owner's network — and `ipaddress.is_private` calls
        100.64/10 private, so only an explicit RFC-1918 test rejects it."""
        with on_unit():
            await service.check()
        assert service.get_state()["local_ip"] == "192.168.1.39"

    def test_the_same_address_is_picked_whatever_order_they_arrive_in(self):
        """`_get_local_ips` returns a *set*, which iterates in hash order: fed an
        iterable already in the opposite order, the sort is the only thing that
        can produce the answer."""
        descending = ["192.168.1.55", "192.168.1.39", "127.0.0.1", "100.117.193.57"]
        assert HostnameConflictService._display_address(descending) == "192.168.1.39"

    def test_a_host_with_only_a_tailscale_address_still_shows_something(self):
        """Mid-boot, or a unit reached over Tailscale alone: better the CGNAT
        address than nothing."""
        assert HostnameConflictService._display_address(
            {"127.0.0.1", "100.117.193.57"}) == "100.117.193.57"

    def test_a_host_with_only_loopback_reports_no_address(self):
        """A unit whose interfaces are all down: the takeover then shows the
        name alone rather than a fabricated address."""
        assert HostnameConflictService._display_address({"127.0.0.1"}) is None

    def test_an_unparseable_line_does_not_take_the_address_down(self):
        """`ip -4 -o addr show` is parsed by token index; a line that yields
        something that is not an address must be dropped, not returned."""
        assert HostnameConflictService._display_address(
            {"not-an-address", "192.168.1.55"}) == "192.168.1.55"


# --------------------------------------------------------------------------- #
# Avahi's own signal
# --------------------------------------------------------------------------- #

class TestStateChangedSignal:
    """`StateChanged` is what makes a real rename visible in seconds instead of
    up to five minutes: the daemon fires it the moment it settles on a new name."""

    async def test_a_settled_daemon_triggers_a_fresh_check(self, service):
        dbus = avahi()
        with on_unit(dbus=dbus):
            assert await service.check() is False
            # The LAN changed under us: another Milō took the name.
            dbus.iface.fqdn = "milo-2.local"
            dbus.iface.emit(AVAHI_SERVER_RUNNING)
            await settle(lambda: service.get_state()["hostname_conflict"] is True)
            await service.cleanup()

    async def test_a_daemon_that_is_only_probing_triggers_nothing(self, service):
        """`REGISTERING` is fired on the way to every rename *and* on the way to
        every healthy boot; acting on it would re-check into the one window
        where nothing is decidable."""
        dbus = avahi()
        with on_unit(dbus=dbus):
            await service.check()
            reads = dbus.iface.reads
            dbus.iface.emit(AVAHI_SERVER_REGISTERING)
            for _ in range(5):
                await asyncio.sleep(0)
            await service.cleanup()

        assert dbus.iface.reads == reads

    async def test_cleanup_detaches_the_handler(self, service):
        """`main.py` calls this on teardown. A handler left on a live bus keeps
        a dead service reachable from the daemon's next state change."""
        dbus = avahi()
        with on_unit(dbus=dbus):
            await service.check()
            assert len(dbus.iface.handlers) == 1
            await service.cleanup()

        assert dbus.iface.handlers == []
        assert dbus.iface.detached == 1


# --------------------------------------------------------------------------- #
# Broadcasting the change
# --------------------------------------------------------------------------- #

class TestBroadcast:

    async def test_the_first_check_of_a_healthy_boot_broadcasts_nothing(self, service):
        """`_conflict` starts False, so a healthy boot is not a change. The
        frontend gets the same state from `GET /api/system/status` at boot; a
        broadcast per five-minute cycle would be pure noise on the socket."""
        with on_unit():
            await service.check()
        service._state_machine.broadcast.assert_not_awaited()

    async def test_the_transition_into_conflict_is_broadcast_once(self, service):
        """The takeover mounts on this event and on nothing else."""
        with on_unit(default_router(browse=(BROWSE_RENAMED_SELF, 0), resolve_name=("", 1)),
                     dbus=avahi(fqdn="milo-2.local")):
            await service.check()
            await service.check()
            await service.cleanup()

        broadcasts = service._state_machine.broadcast.await_args_list
        assert len(broadcasts) == 1
        event = broadcasts[0].args[0]
        assert isinstance(event, SystemHostnameConflictChanged)
        assert event.hostname_conflict is True
        assert event.advertised_name == "milo-2.local"
        assert event.expected_name == EXPECTED_FQDN

    async def test_the_transition_out_of_conflict_is_broadcast_too(self, service):
        """The takeover is full-screen with no way past it: if the recovery is
        not broadcast, turning the other Milō off leaves this one blocked until
        someone reloads the page."""
        dbus = avahi(fqdn="milo-2.local")
        lan = {"browse": BROWSE_RENAMED_SELF}

        def route(argv):
            if argv[0] == "avahi-browse":
                return FakeProc(0, lan["browse"])
            return default_router()(argv)

        with on_unit(route, dbus=dbus):
            assert await service.check() is True
            # The other Milō is turned off: we get the name back and it leaves
            # the browse in the same move.
            dbus.iface.fqdn = EXPECTED_FQDN
            lan["browse"] = BROWSE_SELF_ONLY
            assert await service.check() is False
            await service.cleanup()

        events = [c.args[0] for c in service._state_machine.broadcast.await_args_list]
        assert [e.hostname_conflict for e in events] == [True, False]

    async def test_a_service_with_no_state_machine_still_checks(self, service, systemd):
        """`set_state_machine` is a STEP 2 injection; a check that ran before it
        (or in a unit test) must not raise on the broadcast."""
        bare = HostnameConflictService(systemd_manager=systemd)
        with on_unit(default_router(browse=(BROWSE_RENAMED_SELF, 0), resolve_name=("", 1)),
                     dbus=avahi(fqdn="milo-2.local")):
            assert await bare.check() is True
            await bare.cleanup()


# --------------------------------------------------------------------------- #
# The avahi reclaim
# --------------------------------------------------------------------------- #

class TestAvahiReclaim:
    """Avahi never takes its principal name back on its own once it has been
    renamed, even after the peer that took it disappears. Restarting the daemon
    forces a re-probe — so this is the only thing that gets a stuck unit back to
    `milo.local` without a reboot."""

    @staticmethod
    def _orphaned():
        """Nobody at all holds `milo.local` — the forward resolve fails."""
        return default_router(resolve_name=("", 1), browse=(BROWSE_RENAMED_SELF, 0))

    @staticmethod
    def _renamed():
        return avahi(fqdn="milo-2.local")

    async def test_a_renamed_orphan_restarts_avahi_to_reprobe(self, service, systemd):
        with on_unit(self._orphaned(), dbus=self._renamed()):
            assert await service.check() is True
            await settle(lambda: systemd.restart.await_count == 1)
            await service.cleanup()

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
        with on_unit(self._orphaned(), dbus=self._renamed()):
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
        with on_unit(self._orphaned(), dbus=self._renamed()), clock_patch:
            await service.check()
            clock.now += RECLAIM_COOLDOWN_S - 1
            await service.check()
            await settle(lambda: systemd.restart.await_count >= 1)
            await service.cleanup()

        assert systemd.restart.await_count == 1

    async def test_the_cooldown_expires_and_the_reclaim_is_tried_again(self, service, systemd):
        """It is a cooldown, not a one-shot: a reclaim that did not take must be
        retried, or a unit that lost a probe race stays on `milo-2` for ever."""
        clock, clock_patch = frozen_clock()
        with on_unit(self._orphaned(), dbus=self._renamed()), clock_patch:
            await service.check()
            clock.now += RECLAIM_COOLDOWN_S + 1
            await service.check()
            await settle(lambda: systemd.restart.await_count == 2)
            await service.cleanup()

        assert systemd.restart.await_count == 2

    async def test_a_peer_really_holding_the_name_is_not_reclaimed(self, service, systemd):
        """The reclaim is for the *orphan* case only. Re-probing while another
        device genuinely owns `milo.local` just loses the race again and drops
        this unit's advertisements for nothing."""
        with on_unit(default_router(resolve_name=("milo.local\t192.168.1.99", 0),
                                    browse=(BROWSE_RENAMED_SELF, 0)),
                     dbus=self._renamed()):
            assert await service.check() is True
            await asyncio.sleep(0)
            await service.cleanup()

        systemd.restart.assert_not_awaited()

    async def test_a_unit_that_is_not_in_conflict_is_never_reclaimed(self, service, systemd):
        with on_unit():
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
        with on_unit(TestAvahiReclaim._orphaned(), dbus=TestAvahiReclaim._renamed()):
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
        with on_unit(default_router(browse=(browse, 0))):
            await service.check()

        handed_out = service.get_state()["other_milos"]
        handed_out.append("milo-9.local")
        assert service.get_state()["other_milos"] == ["milo-2.local"]
