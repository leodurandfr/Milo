"""`core/network/` — the service that had no test at all.

It drives NetworkManager for scan / connect / saved profiles / the setup
hotspot, and its main path is the blank-SD blind spot: first boot, AP mode,
the wizard. It is therefore exercised about once per SD image, by hand. Least
tested and least manually checked at the same time.

The boundary mocked here is the **outside world**: `nmcli` (spawned via
`create_subprocess_exec`) and NetworkManager's D-Bus. Nothing in the service's
own internals is patched, so a private helper can be renamed without breaking
a test, and the argv nmcli actually receives is part of what is asserted —
that argv is the contract with NetworkManager exactly as sudo's is with the
sudoers policy.

The two D-Bus subscription tiers were left bare here until 2026-08-26 on the
grounds that "a mock of dbus-next would assert the mock". They are covered now,
and the header of their own block says why the earlier reading was wrong: what
is asserted is not dbus-next but the four wiring decisions layered on top of it
(which properties are worth a re-read, that IP4Config is subscribed to as its
own object, that a listener is detached before its replacement, that an
unchanged status is not re-broadcast) — each of which fails silently on a live
unit and each of which has an observable at this boundary. The fake NM object
tree stands for the outside world exactly as `test_bt_remote.py`'s bus does.

Every canned `nmcli` payload below is a **verbatim capture from this appliance**
on 2026-08-26 (`IP4.ADDRESS[1]:…/24`, `wifi-p2p:disconnected:`, the four active
connections on four devices). A protocol's shape is captured, never invented.
"""
import asyncio
import contextlib
import inspect
import logging
import types

from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import NAMESPACE_URL, uuid5

import pytest

from backend.api.middleware import RecoveryAccessPointGuard
from backend.config.constants import RECOVERY_AP_ADDRESS, RECOVERY_AP_PREFIX
from backend.core.models.ws_events import NetworkStatusChanged
from backend.core.network.models import EthernetStatus, NetworkStatus, WifiConnectionStatus
from backend.core.network.service import (
    ADOPTION_PROFILE,
    ADOPTION_READ_TRIES,
    HOTSPOT_NAME,
    NetworkService,
    ServerWifi,
    WifiShareRefusal,
    _parse_nmcli_line,
    _redact_nmcli_args,
)


# --------------------------------------------------------------------------- #
# The nmcli boundary
# --------------------------------------------------------------------------- #

class FakeProc:
    def __init__(self, rc, stdout, stderr, answer=None):
        self.returncode = rc
        self._out = stdout.encode()
        self._err = stderr.encode()
        # An answer still in flight: the caller waits on it as on a real nmcli.
        self._answer = answer
        self.killed = False

    async def communicate(self, input=None):
        if self._answer is not None:
            self.returncode, out, err = await self._answer
            return out.encode(), err.encode()
        return self._out, self._err

    def kill(self):
        self.killed = True


class FakeNmcli:
    """Stands in for the nmcli binary; records argv, answers from a router."""

    def __init__(self, router=None):
        self.calls: list[tuple[str, ...]] = []
        self._router = router or (lambda args: (0, "", ""))
        # `nmcli radio wifi` answers a word, and every caller compares against
        # "enabled". An empty default made every test that never mentions the
        # radio read as "the radio is off", which is a state with its own
        # recovery path — so the default answers what a normal unit answers,
        # and a test that means "off" says so in its own router.
        self._default_radio = "enabled"

    async def __call__(self, program, *args, **kwargs):
        assert program == "nmcli", f"unexpected program spawned: {program}"
        self.calls.append(args)
        # A real spawn always suspends the caller; without this, overlapping
        # evaluations run one after the other and a race cannot be reproduced.
        await asyncio.sleep(0)
        answer = self._router(args)
        if inspect.isawaitable(answer):
            return FakeProc(None, "", "", answer=answer)
        rc, out, err = answer
        if args == ("radio", "wifi") and not out:
            out = self._default_radio
        return FakeProc(rc, out, err)

    def argv_containing(self, word: str) -> list[tuple[str, ...]]:
        return [c for c in self.calls if word in c]


@pytest.fixture
def service():
    state_machine = AsyncMock()
    settings_service = AsyncMock()
    return NetworkService(state_machine, settings_service)


def with_nmcli(router=None):
    fake = FakeNmcli(router)
    return fake, patch(
        "backend.core.network.service.asyncio.create_subprocess_exec", new=fake
    )


@contextlib.contextmanager
def no_delay():
    """Collapse the recovery AP's waits for a test.

    Both are production design — NM's own autoconnect and DHCP must be allowed
    to finish before the radio is taken, and `nmcli radio wifi on` returns
    before NM has re-enumerated wlan0 — so they are shortened here rather than
    removed there.
    """
    with patch("backend.core.network.service.RECOVERY_AP_DELAY_S", 0), \
         patch("backend.core.network.service.RADIO_SETTLE_INTERVAL_S", 0), \
         patch("backend.core.network.service.AUTOCONNECT_INTERVAL_S", 0):
        yield


class FakeNetworkManager:
    """NetworkManager's state as nmcli reads and changes it, for the paths where
    what counts is what NM is left holding rather than which argv was sent.

    Only what the recovery path touches is modelled: eth0's lease, the radio,
    the wireless profiles (name → `802-11-wireless.mode`, each with a UUID drawn
    from its name, as a save reads it back) and which of them is up or still
    activating. `unreachable` names the station profiles whose
    network is out of range, with how many more activations of each fail before
    it comes back. `hold(*prefix)` keeps every
    command starting with `prefix` in flight until `release()`, and NM applies
    it then **whether or not its nmcli is still waited on** — a caller that is
    cancelled or times out does not undo an activation NM already accepted.
    `fail(*command)` is the opposite: one later `command` that NM never applies.
    """

    def __init__(self, *, ethernet=False, radio=True, profiles=None, active=(), activating=(), unreachable=None):
        self.ethernet = ethernet
        self.radio = radio
        self.profiles: dict[str, str] = dict(profiles or {})
        self.active = set(active)
        self.activating = set(activating)
        self.unreachable: dict[str, int] = dict(unreachable or {})
        self.in_flight: list[asyncio.Task] = []
        self._held: tuple[str, ...] = ()
        self._released = asyncio.Event()
        self._failing: tuple | None = None

    @staticmethod
    def uuid_of(name: str) -> str:
        return str(uuid5(NAMESPACE_URL, name))

    def hold(self, *prefix: str) -> None:
        self._held = prefix

    def release(self) -> None:
        self._released.set()

    def fail(self, *command: str, answer=None, after: int = 0) -> None:
        """The next `command` once `after` more of them went through is not
        applied: its nmcli times out (`answer` None — NM stalled: five nmcli
        timeouts in this appliance's errors.log, 2026-09/10) or NM answers
        `answer`."""
        self._failing = (command, after, answer)

    @staticmethod
    async def _stall():
        raise asyncio.TimeoutError

    def route(self, args):
        if self._failing is not None and args == self._failing[0]:
            command, after, answer = self._failing
            if after:
                self._failing = (command, after - 1, answer)
            else:
                self._failing = None
                return self._stall() if answer is None else answer
        if self._held and args[:len(self._held)] == self._held:
            task = asyncio.ensure_future(self._apply_once_released(args))
            self.in_flight.append(task)
            return asyncio.shield(task)
        return self._apply(args)

    async def _apply_once_released(self, args):
        await self._released.wait()
        return self._apply(args)

    def _apply(self, args):
        if args == ("radio", "wifi"):
            return (0, "enabled" if self.radio else "disabled", "")
        if args[:2] == ("radio", "wifi"):
            self.radio = args[2] == "on"
            if not self.radio:
                self.active.clear()
                self.activating.clear()
            return (0, "", "")
        if "TYPE,STATE,CONNECTION" in args:
            eth = "ethernet:connected:Wired connection 1" if self.ethernet else "ethernet:unavailable:"
            wifi = [f"wifi:connected:{name}" for name in sorted(self.active)]
            wifi += [f"wifi:connecting (configuring):{name}" for name in sorted(self.activating)]
            if not wifi:
                wifi = ["wifi:disconnected:" if self.radio else "wifi:unavailable:"]
            return (0, "\n".join([eth, *wifi]), "")
        if "DEVICE,STATE" in args:
            eth = "eth0:connected" if self.ethernet else "eth0:unavailable"
            wlan = "wlan0:disconnected" if self.radio else "wlan0:unavailable"
            return (0, f"{eth}\n{wlan}", "")
        if args[-3:] == ("device", "show", "eth0"):
            if self.ethernet:
                return (0, _device_show("Wired connection 1", "192.168.1.55"), "")
            return (0, _device_show("--", None), "")
        if args[-3:] == ("device", "show", "wlan0"):
            # What the status broadcast reads: the profile wlan0 carries, if any.
            if not self.active:
                return (0, _device_show("--", None), "")
            name = sorted(self.active)[0]
            return (0, _device_show(name, "10.42.0.1" if name == HOTSPOT_NAME else "192.168.1.11"), "")
        if "NAME,TYPE" in args:
            return (0, "\n".join(f"{name}:802-11-wireless" for name in sorted(self.profiles)), "")
        if "NAME,UUID,TYPE" in args:
            return (0, "\n".join(
                f"{name}:{self.uuid_of(name)}:802-11-wireless" for name in sorted(self.profiles)
            ), "")
        if "802-11-wireless.mode" in args:
            name = args[-1]
            if name not in self.profiles:
                return (10, "", f"Error: {name} - no such connection profile.")
            return (0, f"802-11-wireless.mode:{self.profiles[name]}", "")
        if args[:2] == ("connection", "add"):
            mode = args[args.index("wifi.mode") + 1] if "wifi.mode" in args else "infrastructure"
            name = args[args.index("con-name") + 1]
            self.profiles[name] = mode
            return (0, f"Connection '{name}' ({self.uuid_of(name)}) successfully added.", "")
        if args[:2] in (("connection", "delete"), ("connection", "up")):
            name = args[2]
            if name not in self.profiles:
                return (10, "", f"Error: unknown connection '{name}'.")
            if args[1] == "delete":
                del self.profiles[name]
                self.active.discard(name)
                self.activating.discard(name)
            elif self.unreachable.get(name):
                self.unreachable[name] -= 1
                return (4, "", "Error: Connection activation failed: The Wi-Fi network could not be found")
            else:
                self.active.add(name)
            return (0, "", "")
        raise AssertionError(f"not modelled: nmcli {' '.join(args)}")


class FakeStations:
    """NetworkManager's WiFi station profiles as nmcli lists, reads, adds,
    activates, modifies and deletes them — what a connect, a save or a forget
    leaves NM holding is what these tests assert.

    Profiles are addressed by UUID as NM does, and two may share a name. A
    profile is `{name, ssid, psk, on_disk, autoconnect}`; `in_range` maps each
    network in reach to its key (None for an open one), and `connection up`
    joins iff the profile's key is that one. Bringing a profile up takes wlan0
    from whatever held it — the AP included — before it joins or fails, as one
    radio does. `persists` False models a modify NM keeps in /run. `fail(*command,
    answer=…)` answers one later command (by prefix) with `answer` instead.
    """

    WIRED = ("Wired connection 1", "a1f0c0de-0000-4000-8000-000000000001")

    def __init__(self, profiles=(), *, active=None, in_range=None, hotspot=False, ethernet=False, persists=True):
        self.profiles: dict[str, dict] = {}
        for name, uuid, ssid, psk in profiles:
            self.profiles[uuid] = {"name": name, "ssid": ssid, "psk": psk, "on_disk": True, "autoconnect": True}
        self.active = active
        self.in_range = dict(in_range or {})
        self.hotspot = hotspot
        self.hotspot_profile = hotspot
        self.ethernet = ethernet
        self.persists = persists
        self._added = 0
        self._failing: list[tuple[tuple, tuple]] = []

    def fail(self, *command, answer):
        self._failing.append((command, answer))

    def by_name(self, name):
        return [p for p in self.profiles.values() if p["name"] == name]

    def _take_radio(self):
        self.active = None
        self.hotspot = False

    def route(self, args):
        for i, (command, answer) in enumerate(self._failing):
            if args[:len(command)] == command:
                del self._failing[i]
                return answer
        if args == ("radio", "wifi"):
            return (0, "enabled", "")
        if args[:2] == ("radio", "wifi"):
            return (0, "", "")
        if args == ("-t", "-f", "NAME,UUID,TYPE", "connection", "show"):
            rows = [f"{self.WIRED[0]}:{self.WIRED[1]}:802-3-ethernet"]
            rows += [f"{p['name']}:{uuid}:802-11-wireless" for uuid, p in self.profiles.items()]
            if self.hotspot_profile:
                rows.append(f"{HOTSPOT_NAME}:5e1f0000-0000-4000-8000-0000000000a9:802-11-wireless")
            return (0, "\n".join(rows), "")
        if args == ("-t", "-f", "NAME,TYPE", "connection", "show"):
            return (0, "\n".join(f"{p['name']}:802-11-wireless" for p in self.profiles.values()), "")
        if args[:5] == ("-t", "-f", "802-11-wireless.ssid", "connection", "show"):
            profile = self.profiles.get(args[-1])
            if profile is None:
                return (10, "", "Error: no such connection profile.")
            return (0, f"802-11-wireless.ssid:{profile['ssid']}", "")
        if args == ("-t", "-f", "NAME,UUID,DEVICE", "connection", "show", "--active"):
            rows = [f"{self.WIRED[0]}:{self.WIRED[1]}:eth0"] if self.ethernet else []
            if self.active is not None:
                rows.append(f"{self.profiles[self.active]['name']}:{self.active}:wlan0")
            elif self.hotspot:
                rows.append(f"{HOTSPOT_NAME}:5e1f0000-0000-4000-8000-0000000000a9:wlan0")
            return (0, "\n".join(rows), "")
        if args == ("-t", "-f", "UUID,FILENAME", "connection", "show"):
            return (0, "\n".join(
                f"{uuid}:/{'etc' if p['on_disk'] else 'run'}/NetworkManager/system-connections/{p['name']}.nmconnection"
                for uuid, p in self.profiles.items()
            ), "")
        if "TYPE,STATE,CONNECTION" in args:
            rows = ["ethernet:connected:Wired connection 1" if self.ethernet else "ethernet:unavailable:"]
            if self.active is not None:
                rows.append(f"wifi:connected:{self.profiles[self.active]['name']}")
            elif self.hotspot:
                rows.append(f"wifi:connected:{HOTSPOT_NAME}")
            else:
                rows.append("wifi:disconnected:")
            return (0, "\n".join(rows), "")
        if args[-3:] == ("device", "show", "eth0"):
            if self.ethernet:
                return (0, _device_show("Wired connection 1", "192.168.1.55"), "")
            return (0, _device_show("--", None), "")
        if args[-3:] == ("device", "show", "wlan0"):
            if self.active is None:
                return (0, "GENERAL.STATE:30 (disconnected)\n" + _device_show("--", None), "")
            return (0, "GENERAL.STATE:100 (connected)\n"
                    + _device_show(self.profiles[self.active]["name"], "192.168.1.66"), "")
        if args[:2] == ("connection", "add"):
            if "wifi.mode" in args:
                self.hotspot_profile = True
                return (0, f"Connection '{HOTSPOT_NAME}' (5e1f0000-0000-4000-8000-0000000000a9) successfully added.", "")
            self._added += 1
            uuid = f"0d0e0f00-0000-4000-8000-{self._added:012d}"

            def value(key, default=None):
                return args[args.index(key) + 1] if key in args else default
            self.profiles[uuid] = {
                "name": value("con-name"), "ssid": value("ssid"), "psk": value("wifi-sec.psk"),
                "on_disk": value("save", "yes") == "yes",
                "autoconnect": value("connection.autoconnect", "yes") == "yes",
            }
            return (0, f"Connection '{value('con-name')}' ({uuid}) successfully added.", "")
        if args[:3] == ("connection", "up", "uuid"):
            profile = self.profiles.get(args[3])
            if profile is None:
                return (10, "", f"Error: unknown connection '{args[3]}'.")
            self._take_radio()
            if profile["ssid"] not in self.in_range:
                return (4, "", "Error: Connection activation failed: The Wi-Fi network could not be found")
            if self.in_range[profile["ssid"]] != profile["psk"]:
                return (4, "", "Error: Connection activation failed: Secrets were required, but not provided")
            self.active = args[3]
            return (0, "", "")
        if args == ("connection", "up", HOTSPOT_NAME):
            self._take_radio()
            self.hotspot = True
            return (0, "", "")
        if args[:3] == ("connection", "modify", "uuid"):
            profile = self.profiles[args[3]]
            profile["autoconnect"] = args[args.index("connection.autoconnect") + 1] == "yes"
            profile["on_disk"] = profile["on_disk"] or self.persists
            return (0, "", "")
        if args[:3] == ("connection", "delete", "uuid"):
            if self.profiles.pop(args[3], None) is None:
                return (10, "", f"Error: unknown connection '{args[3]}'.")
            if self.active == args[3]:
                self.active = None
            return (0, "", "")
        if args == ("connection", "delete", HOTSPOT_NAME):
            if not self.hotspot_profile:
                return (10, "", f"Error: unknown connection '{HOTSPOT_NAME}'.")
            self.hotspot_profile = self.hotspot = False
            return (0, "", "")
        raise AssertionError(f"not modelled: nmcli {' '.join(args)}")


async def settle_until(predicate, timeout=1.0):
    """Run the loop until `predicate()` holds, or fail after `timeout`.

    Bounded on purpose: an implementation that never arms the timer must make
    the suite RED, not make it hang.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("the recovery access point never settled into the expected state")


# --------------------------------------------------------------------------- #
# nmcli terse-mode parsing (pure)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("line,expected", [
    ("MySSID:72:WPA2:*", ["MySSID", "72", "WPA2", "*"]),
    # an SSID may legitimately contain the field separator
    (r"Foo\:Bar:60:WPA2:", ["Foo:Bar", "60", "WPA2", ""]),
    (r"back\\slash:60:WPA2:", ["back\\slash", "60", "WPA2", ""]),
    # open network: the security field is empty, not absent
    ("Guest:41::", ["Guest", "41", "", ""]),
    ("", [""]),
])
def test_nmcli_terse_line_splits_on_unescaped_colons_only(line, expected):
    """An SSID containing ':' must not shift every field after it.

    Every caller indexes the result positionally, so a mis-split does not fail
    loudly — it reports one network's signal as another's security type.
    """
    assert _parse_nmcli_line(line) == expected


# --------------------------------------------------------------------------- #
# scan_networks
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_scan_keeps_the_strongest_duplicate_and_sorts_by_signal(service):
    """One SSID on three APs is one entry in the picker, at its best signal."""
    scan = "\n".join([
        "Home:41:WPA2:",
        "Cafe:88:WPA2:",
        "Home:77:WPA2:*",
        "Home:12:WPA2:",
    ])
    fake, patcher = with_nmcli(lambda args: (0, scan, ""))
    with patcher:
        networks = await service.scan_networks()

    assert [(n.ssid, n.signal) for n in networks] == [("Cafe", 88), ("Home", 77)]
    assert next(n for n in networks if n.ssid == "Home").in_use is True


@pytest.mark.asyncio
async def test_scan_keeps_the_associated_ap_over_a_stronger_one(service):
    """A dual-band router is one SSID on two radios, and the unit is on one.

    Keeping the stronger radio showed the connected network at 4 arcs in the
    WiFi panel while its connection card, which reads the associated AP, showed
    2 (measured on a Freebox: 5 GHz associated at 64, 2.4 GHz at 85).
    """
    scan = "\n".join([
        "Home:85:WPA2:",
        "Home:64:WPA2:*",
        "Home:90:WPA2:",
    ])
    fake, patcher = with_nmcli(lambda args: (0, scan, ""))
    with patcher:
        networks = await service.scan_networks()

    assert [(n.ssid, n.signal, n.in_use) for n in networks] == [("Home", 64, True)]


@pytest.mark.asyncio
async def test_scan_drops_hidden_and_malformed_entries(service):
    """A blank SSID is a hidden network; a short line is nmcli noise.

    Both would otherwise reach the wizard as an unselectable empty row.
    """
    scan = "\n".join([
        ":80:WPA2:",          # hidden
        "Truncated:80",       # fewer fields than requested
        "Good:55:WPA2:",
    ])
    fake, patcher = with_nmcli(lambda args: (0, scan, ""))
    with patcher:
        networks = await service.scan_networks()

    assert [n.ssid for n in networks] == ["Good"]


@pytest.mark.asyncio
async def test_scan_survives_a_non_numeric_signal(service):
    """nmcli can emit '--'; a ValueError here would empty the whole picker."""
    fake, patcher = with_nmcli(lambda args: (0, "Weird:--:WPA2:", ""))
    with patcher:
        networks = await service.scan_networks()

    assert [(n.ssid, n.signal) for n in networks] == [("Weird", 0)]


@pytest.mark.asyncio
async def test_a_scan_without_rescan_never_asks_nm_to_rescan(service):
    """An active scan takes the radio off its channel for 70 to 127 ms, 23 times
    in ~3.6 s (measured on wlan0): on a WiFi-only server that is the link
    carrying multiroom audio. The discovery poll must be able to read
    NetworkManager's cache instead, while the WiFi picker keeps its rescan.
    """
    fake, patcher = with_nmcli(lambda args: (0, "Home:70:WPA2:", ""))
    with patcher:
        await service.scan_networks(rescan=False)
        cached = list(fake.calls)
        await service.scan_networks()

    assert len(cached) == 1
    assert cached[0][cached[0].index("--rescan") + 1] == "no"
    fresh = fake.calls[1]
    assert fresh[fresh.index("--rescan") + 1] == "yes"


@pytest.mark.asyncio
async def test_scan_failure_is_raised_not_swallowed(service):
    """The wizard must show an error, not an empty list that looks like "no networks"."""
    fake, patcher = with_nmcli(lambda args: (1, "", "wifi disabled"))
    with patcher:
        with pytest.raises(RuntimeError, match="wifi disabled"):
            await service.scan_networks()


# --------------------------------------------------------------------------- #
# Status: "connected" means reachable
# --------------------------------------------------------------------------- #

def _device_show(connection, ip):
    lines = [f"GENERAL.CONNECTION:{connection}"]
    if ip:
        lines.append(f"IP4.ADDRESS[1]:{ip}/24")
    return "\n".join(lines)


def _status_router(*, wifi_radio="enabled", eth=("--", None), wlan=("--", None), profiles=""):
    def router(args):
        if args[:2] == ("radio", "wifi"):
            return (0, wifi_radio, "")
        if "show" in args and "eth0" in args:
            return (0, _device_show(*eth), "")
        if "show" in args and "wlan0" in args:
            return (0, _device_show(*wlan), "")
        if args[:1] == ("-t",) and "connection" in args and "show" in args:
            return (0, profiles, "")
        return (0, "", "")
    return router


@pytest.mark.asyncio
async def test_an_interface_without_an_ip_is_reported_disconnected(service):
    """A profile mid-DHCP is not yet reachable — the badge must not say it is.

    milo.local is advertised on an interface only once it has an address, so a
    "connected" badge without one sends the user to a name that does not
    resolve. Both interfaces answer the same rule.
    """
    fake, patcher = with_nmcli(_status_router(
        eth=("Wired connection 1", None),
        wlan=("milo-Home", None),
    ))
    with patcher:
        status = await service.get_network_status()

    assert status.ethernet.connected is False
    assert status.wifi.connected is False


@pytest.mark.parametrize("profile", [HOTSPOT_NAME, ADOPTION_PROFILE])
@pytest.mark.asyncio
async def test_the_setup_hotspot_is_not_reported_as_a_wifi_connection(service, profile):
    """The AP Milō raises for its own wizard is not a network it joined, and
    neither is a speaker's setup hotspot the adoption joined for a minute.

    Reporting the first as connected would let the wizard believe setup
    succeeded while the unit is still serving its own captive portal; the
    second, an open "Milō", is what "WiFi joined" would then push to every
    satellite as its standby link.
    """
    fake, patcher = with_nmcli(_status_router(wlan=(profile, "10.42.0.1")))
    with patcher:
        status = await service.get_network_status()

    assert status.wifi.connected is False


@pytest.mark.asyncio
async def test_a_network_being_joined_is_reported_as_connecting(service):
    """Between association and the DHCP lease the UI says "Connecting…" and
    names the network, rather than "Not connected" on a join about to land."""
    def router(args):
        if "show" in args and "wlan0" in args:
            return (0, "GENERAL.STATE:70 (connecting (getting IP configuration))\n"
                       "GENERAL.CONNECTION:milo-Maison", "")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher:
        status = await service.get_network_status()

    assert status.wifi.connected is False
    assert status.wifi.connecting is True
    assert status.wifi.ssid == "Maison"


@pytest.mark.parametrize("show", [
    "GENERAL.STATE:30 (disconnected)\nGENERAL.CONNECTION:--",
    f"GENERAL.STATE:100 (connected)\nGENERAL.CONNECTION:{HOTSPOT_NAME}\nIP4.ADDRESS[1]:10.42.0.1/24",
    f"GENERAL.STATE:70 (connecting (getting IP configuration))\nGENERAL.CONNECTION:{HOTSPOT_NAME}",
])
@pytest.mark.asyncio
async def test_an_idle_radio_or_the_units_own_access_point_is_not_connecting(service, show):
    """A "Connecting…" badge that never resolves is worse than none: neither an
    idle wlan0 nor the recovery AP coming up is a network being joined."""
    def router(args):
        if "show" in args and "wlan0" in args:
            return (0, show, "")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher:
        status = await service.get_network_status()

    assert status.wifi.connecting is False


@pytest.mark.asyncio
async def test_connecting_ends_with_the_dhcp_window_even_without_a_lease(service):
    """Under `ipv4.may-fail` an activated wlan0 can stay on IPv6 alone for
    good. "Connecting…" lasts NM's DHCP window and then goes out by itself —
    a broadcast at its end, with no NM event needed to trigger it."""
    def router(args):
        if "show" in args and "wlan0" in args:
            return (0, "GENERAL.STATE:100 (connected)\nGENERAL.CONNECTION:milo-Maison", "")
        if "status" in args and "TYPE,STATE,CONNECTION" in args:
            return (0, "wifi:connected:milo-Maison", "")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher, patch("backend.core.network.service.DHCP_LEASE_WAIT_S", 0.05):
        status = await service.get_network_status()
        assert status.wifi.connecting is True
        await settle_until(lambda: service.state_machine.broadcast.await_count > 0)

    sent = service.state_machine.broadcast.await_args.args[0]
    assert sent.wifi.connecting is False


@pytest.mark.asyncio
async def test_a_disabled_radio_short_circuits_the_wifi_probe(service):
    """With the radio off there is nothing to ask wlan0, and asking can stall."""
    fake, patcher = with_nmcli(_status_router(wifi_radio="disabled"))
    with patcher:
        status = await service.get_network_status()

    assert status.wifi_enabled is False
    assert status.wifi.connected is False
    assert not fake.argv_containing("wlan0")


@pytest.mark.asyncio
async def test_the_saved_ssid_is_read_back_from_the_milo_prefixed_profile(service):
    """`milo-<ssid>` is the naming contract save_network writes; reads must match it."""
    fake, patcher = with_nmcli(_status_router(
        wlan=("--", None),
        profiles="\n".join([
            "Wired connection 1:802-3-ethernet",
            "milo-Home:802-11-wireless",
        ]),
    ))
    with patcher:
        status = await service.get_network_status()

    assert status.wifi.saved_ssid == "Home"


@pytest.mark.asyncio
async def test_known_ssids_name_every_station_profile_and_not_the_hotspot(service):
    """The diagnostic report redacts exactly these, so one missing is one leaked.

    The SSID is read from the profile, not its name: an Imager `preconfigured`
    profile names its network nowhere else, and two profiles may share a name,
    which is why each is asked for by UUID.
    The setup hotspot is every Milō's, not the household's, and stays readable.
    """
    profiles = [
        ("milo-Home", "u-1", "Home"),
        ("preconfigured", "u-3", "Imager: Net"),
        ("preconfigured", "u-4", "Second Net"),
        (HOTSPOT_NAME, "u-5", HOTSPOT_NAME),
    ]
    by_uuid = {uuid: ssid for _, uuid, ssid in profiles}

    def router(args):
        if args == ("-t", "-f", "NAME,UUID,TYPE", "connection", "show"):
            return 0, "\n".join([
                "Wired connection 1:u-0:802-3-ethernet",
                *(f"{name}:{uuid}:802-11-wireless" for name, uuid, _ in profiles),
                "lo:u-9:loopback",
            ]), ""
        if args[:6] == ("-t", "-f", "802-11-wireless.ssid", "connection", "show", "uuid"):
            value = by_uuid[args[6]].replace(":", "\\:")
            return 0, f"802-11-wireless.ssid:{value}", ""
        return 0, "", ""

    fake, patcher = with_nmcli(router)
    with patcher:
        known = await service.known_ssids()

    assert known == {"Home", "Imager: Net", "Second Net"}
    assert not any("u-5" in call for call in fake.argv_containing("802-11-wireless.ssid"))


@pytest.mark.asyncio
async def test_known_ssids_fall_back_to_the_profile_name_when_its_ssid_is_unreadable(service):
    """A profile nmcli will not describe, or not in time, still names its network
    in its name — and one stalled read does not cost the others."""
    def router(args):
        if args == ("-t", "-f", "NAME,UUID,TYPE", "connection", "show"):
            return 0, "Livebox:u-1:802-11-wireless\nCafe:u-2:802-11-wireless\nOffice:u-3:802-11-wireless", ""
        if args[-1] == "u-1":
            return 10, "", "Error: no such connection profile."
        if args[-1] == "u-2":
            return asyncio.get_running_loop().create_future()
        return 0, "802-11-wireless.ssid:Office Net", ""

    fake, patcher = with_nmcli(router)
    with patcher:
        known = await service.known_ssids()

    assert known == {"Livebox", "Cafe", "Office Net"}


# --------------------------------------------------------------------------- #
# The AP transition — what a blank card depends on
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_a_unit_with_no_link_at_all_raises_its_own_access_point(service):
    """The rule: the appliance must always be reachable, so with no link it
    makes one.

    This replaces a first-boot special case gated on `setup_completed`, which
    failed exactly where it mattered. On the hotspot path the wizard *saves* a
    WiFi profile instead of connecting to it — it cannot connect, the AP is
    carrying the user's browser — so nothing validates the password. A wrong
    one completed the wizard, set the flag, rebooted, and left a unit NM could
    not associate: no LAN, no AP (setup was "complete"), and on a headless unit
    no screen. Unreachable, permanently.
    """
    fake, patcher = with_nmcli(lambda args: (0, "", ""))
    with patcher, no_delay():
        await service.evaluate_reachability()
        await settle_until(lambda: service.hotspot_active)

    assert fake.argv_containing("add")


@pytest.mark.asyncio
async def test_a_live_network_raises_nothing(service):
    """A unit that is reachable already needs no second channel."""
    def router(args):
        if "status" in args:
            return (0, "ethernet:connected:Wired connection 1", "")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher, no_delay():
        await service.evaluate_reachability()
        await asyncio.sleep(0.05)

    assert service.hotspot_active is False
    assert not fake.argv_containing("add")


@pytest.mark.asyncio
async def test_the_units_own_access_point_does_not_count_as_a_live_network(service):
    """Otherwise the AP would prove the unit reachable and tear itself down.
    A restart that finds it up adopts it in `initialize()` instead — see
    test_an_access_point_left_up_by_a_previous_process_is_adopted_…"""
    def router(args):
        if "status" in args:
            return (0, f"wifi:connected:{HOTSPOT_NAME}", "")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher, no_delay():
        await service.evaluate_reachability()
        await settle_until(lambda: service.hotspot_active)


@pytest.mark.asyncio
async def test_a_link_that_comes_back_takes_the_access_point_down(service):
    """The other half of the rule, and the one with no previous owner: the AP
    used to be torn down only by `connect()` and by the next boot, so an
    ethernet cable plugged back in left an open AP broadcasting."""
    state = {"connected": False}

    def router(args):
        if "status" in args and state["connected"]:
            return (0, "ethernet:connected:Wired connection 1", "")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher, no_delay():
        await service.evaluate_reachability()
        await settle_until(lambda: service.hotspot_active)

        state["connected"] = True
        await service.evaluate_reachability()

    assert service.hotspot_active is False
    assert fake.argv_containing("delete")


@pytest.mark.asyncio
async def test_a_link_arriving_during_the_settle_window_cancels_the_access_point(service):
    """The window exists because a cold boot has no connection until
    autoconnect and DHCP finish. Arming without disarming would raise an AP
    seconds after the unit became reachable, on every single boot.
    """
    state = {"connected": False}

    def router(args):
        if "status" in args and state["connected"]:
            return (0, "ethernet:connected:Wired connection 1", "")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    # The real window, not the collapsed one: the point is that nothing happens
    # inside it.
    with patcher:
        await service.evaluate_reachability()
        state["connected"] = True
        await service.evaluate_reachability()
        await asyncio.sleep(0.05)

    assert service.hotspot_active is False
    assert not fake.argv_containing("add")


@pytest.mark.asyncio
async def test_a_status_that_already_shows_a_link_costs_no_extra_nmcli(service):
    """The rule runs on every NetworkManager property change, on a path that was
    deliberately trimmed to four nmcli forks per event (see the module
    docstring — a subscription was *removed* over that cost). A fifth fork on
    every event for the common case, a healthy unit, is what the pre-filter
    avoids; it is trusted in one direction only, so it can cost a redundant
    check but never a wrong answer.
    """
    from backend.core.network.models import EthernetStatus, NetworkStatus, WifiConnectionStatus

    status = NetworkStatus(
        wifi_enabled=True,
        ethernet=EthernetStatus(connected=True, ip_address="192.168.1.10"),
        wifi=WifiConnectionStatus(connected=False),
        hotspot_active=False,
        link_lost=False,
    )

    fake, patcher = with_nmcli()
    with patcher, no_delay():
        await service.evaluate_reachability(status)
        await asyncio.sleep(0.05)

    assert fake.calls == [], "the pre-filter did not short-circuit the authoritative read"
    assert service.hotspot_active is False


@pytest.mark.asyncio
async def test_a_status_showing_no_link_is_confirmed_before_acting(service):
    """The other direction: the cheap status may not decide *against* the unit.

    `get_network_status` fails open — an unreadable interface reads as
    disconnected — so acting on its "no link" would raise an access point over
    one nmcli hiccup. It only ever triggers the real read.
    """
    from backend.core.network.models import EthernetStatus, NetworkStatus, WifiConnectionStatus

    status = NetworkStatus(
        wifi_enabled=True,
        ethernet=EthernetStatus(connected=False),
        wifi=WifiConnectionStatus(connected=False),
        hotspot_active=False,
        link_lost=False,
    )

    def router(args):
        if "status" in args:
            return (0, "ethernet:connected:Wired connection 1", "")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher, no_delay():
        await service.evaluate_reachability(status)
        await asyncio.sleep(0.05)

    assert fake.argv_containing("status"), "the authoritative read was skipped"
    assert service.hotspot_active is False


@pytest.mark.asyncio
async def test_a_radio_switched_off_is_turned_back_on_before_the_access_point(service):
    """Measured on the appliance 2026-09-04: with the cable out and the WiFi
    toggle off, the rule fired on time (46 s) and NM refused the activation with
    *No suitable device found for this connection (device eth0 not available…)*.

    It names eth0, which reads like a profile bug; the cause is that a radio
    switched off leaves wlan0 out of NM's candidate list entirely. So the radio
    comes back on first — a preference the appliance cannot afford while it is
    unreachable, and the exact state an owner on ethernet who dislikes WiFi is
    one cable-failure away from.
    """
    radio = {"on": False}

    def router(args):
        if args[:2] == ("radio", "wifi") and len(args) == 2:
            return (0, "enabled" if radio["on"] else "disabled", "")
        if args[:3] == ("radio", "wifi", "on"):
            radio["on"] = True
            return (0, "", "")
        if "status" in args and "DEVICE,STATE" in args:
            return (0, "eth0:unavailable\nwlan0:disconnected", "")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher, no_delay():
        await service.evaluate_reachability()
        await settle_until(lambda: service.hotspot_active)

    assert radio["on"], "the access point was attempted on a radio that was still off"
    turn_on = fake.calls.index(("radio", "wifi", "on"))
    add = next(i for i, c in enumerate(fake.calls) if "add" in c)
    assert turn_on < add, "the profile was created before the radio came back"


@pytest.mark.asyncio
async def test_the_radio_alone_coming_back_is_enough(service):
    """If turning the radio on restores the link — a saved network associates —
    there is nothing to recover from and no access point is raised. Otherwise
    the unit would take its own radio away from the connection it just made."""
    state = {"connected": False}

    def router(args):
        if args[:2] == ("radio", "wifi") and len(args) == 2:
            return (0, "disabled", "")
        if args[:3] == ("radio", "wifi", "on"):
            state["connected"] = True
            return (0, "", "")
        if "status" in args and "DEVICE,STATE" in args:
            return (0, "wlan0:connected", "")
        if "status" in args:
            return (0, "wifi:connected:Maison" if state["connected"] else "", "")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher, no_delay():
        await service.evaluate_reachability()
        await asyncio.sleep(0.15)

    assert service.hotspot_active is False
    assert not fake.argv_containing("add")


@pytest.mark.parametrize("profile", ["milo-Maison", "preconfigured"])
@pytest.mark.asyncio
async def test_a_saved_network_gets_its_autoconnect_before_the_access_point(service, profile):
    """Measured 2026-10-03: NM began autoconnecting the saved network 2.5 s
    after the recovery turned the radio on, and the AP had been raised in
    between — the unit sat on its own hotspot with the home network in range.
    A link that lands a few polls later must leave the AP down."""
    radio = {"on": False}
    polls = {"n": 0}

    def router(args):
        if args[:2] == ("radio", "wifi") and len(args) == 2:
            return (0, "enabled" if radio["on"] else "disabled", "")
        if args[:3] == ("radio", "wifi", "on"):
            radio["on"] = True
            return (0, "", "")
        if "status" in args and "DEVICE,STATE" in args:
            return (0, "wlan0:disconnected", "")
        if "status" in args and "TYPE,STATE,CONNECTION" in args:
            if radio["on"]:
                polls["n"] += 1
            return (0, "wifi:connected:milo-Maison" if polls["n"] > 3 else "", "")
        if "NAME,TYPE" in args:
            return (0, f"{profile}:802-11-wireless", "")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher, no_delay():
        await service.evaluate_reachability()
        await settle_until(lambda: service._recovery_arm is None)

    assert polls["n"] > 3, "the autoconnect window was never polled"
    assert service.hotspot_active is False
    assert not fake.argv_containing("add")


@pytest.mark.asyncio
async def test_a_saved_network_gets_its_autoconnect_even_when_the_radio_was_already_on(service):
    """A WiFi-only unit whose box reboots: the radio was on all along, so there
    was nothing to turn back on — and the wait was granted only to a radio the
    recovery had just switched on. The AP took the radio 45 s in, from a box
    that was about to come back."""
    polls = {"n": 0}

    def router(args):
        if "status" in args and "DEVICE,STATE" in args:
            return (0, "wlan0:disconnected", "")
        if "status" in args and "TYPE,STATE,CONNECTION" in args:
            polls["n"] += 1
            return (0, "wifi:connected:milo-Maison" if polls["n"] > 3 else "", "")
        if "NAME,TYPE" in args:
            return (0, "milo-Maison:802-11-wireless", "")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher, no_delay():
        await service.evaluate_reachability()
        await settle_until(lambda: service._recovery_arm is None)

    assert not fake.argv_containing("on"), "the radio was already on"
    assert polls["n"] > 3, "the autoconnect window was never polled"
    assert service.hotspot_active is False
    assert not fake.argv_containing("add")


@pytest.mark.asyncio
async def test_the_radio_setting_is_given_back_once_it_costs_nothing(service):
    """The override is temporary by construction. With ethernet back the radio
    goes off again — and the journal says so, rather than the user's setting
    quietly staying overridden for the life of the unit."""
    service._radio_forced_on = True

    def router(args):
        if "status" in args and "TYPE,STATE,CONNECTION" in args:
            return (0, "ethernet:connected:Wired connection 1", "")
        if "show" in args and "eth0" in args:
            return (0, _device_show("Wired connection 1", "192.168.1.55"), "")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher:
        await service.evaluate_reachability()

    assert ("radio", "wifi", "off") in fake.calls
    assert service._radio_forced_on is False


@pytest.mark.asyncio
async def test_a_radio_carrying_the_only_link_is_left_alone(service):
    """Switching it off here would strand the unit a second time — the override
    stands until ethernet holds a lease, and is given back then."""
    service._radio_forced_on = True

    def router(args):
        if "status" in args and "TYPE,STATE,CONNECTION" in args:
            return (0, "wifi:connected:Maison", "")
        if "show" in args and "wlan0" in args:
            return (0, "GENERAL.CONNECTION:milo-Maison\nIP4.ADDRESS[1]:192.168.1.20/24", "")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher:
        await service.evaluate_reachability()

    assert ("radio", "wifi", "off") not in fake.calls
    assert service._radio_forced_on is True


@pytest.mark.asyncio
async def test_a_wifi_link_still_waiting_for_its_lease_is_not_switched_off(service):
    """Measured 2026-10-03: wlan0 read `connected` to NM before DHCP answered,
    the WiFi-has-an-address test said no, and the radio was switched off under
    the association the recovery had just made — the user saw WiFi turn itself
    off. With no ethernet lease, the radio stays on whatever wlan0's lease says."""
    service._radio_forced_on = True

    def router(args):
        if "status" in args and "TYPE,STATE,CONNECTION" in args:
            return (0, "wifi:connected:milo-Maison\nethernet:unavailable:", "")
        if "show" in args and "wlan0" in args:
            return (0, "GENERAL.STATE:100 (connected)\nGENERAL.CONNECTION:milo-Maison", "")
        if "show" in args and "eth0" in args:
            return (0, _device_show("--", None), "")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher:
        await service.evaluate_reachability()

    assert ("radio", "wifi", "off") not in fake.calls


@pytest.mark.asyncio
async def test_the_user_turning_the_radio_on_ends_the_override(service):
    """Measured 2026-10-03: the user switched WiFi back on during the recovery,
    and the restore later switched it off as if that "on" were the override's.
    The user's setting is the setting — nothing restores over it."""
    service._radio_forced_on = True

    def router(args):
        if "status" in args and "TYPE,STATE,CONNECTION" in args:
            return (0, "ethernet:connected:Wired connection 1", "")
        if "show" in args and "eth0" in args:
            return (0, _device_show("Wired connection 1", "192.168.1.55"), "")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher:
        await service.set_wifi_enabled(True)
        await service.evaluate_reachability()

    assert ("radio", "wifi", "off") not in fake.calls


@pytest.mark.asyncio
async def test_concurrent_evaluations_give_the_setting_back_once(service):
    """Every NM property change runs an evaluation, so they overlap; measured,
    two of them each switched the radio off. One restore, one `radio off`."""
    service._radio_forced_on = True

    def router(args):
        if "status" in args and "TYPE,STATE,CONNECTION" in args:
            return (0, "ethernet:connected:Wired connection 1", "")
        if "show" in args and "eth0" in args:
            return (0, _device_show("Wired connection 1", "192.168.1.55"), "")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher:
        await asyncio.gather(service.evaluate_reachability(), service.evaluate_reachability())

    assert fake.calls.count(("radio", "wifi", "off")) == 1


@pytest.mark.asyncio
async def test_a_radio_whose_device_never_returns_skips_the_autoconnect_wait(service):
    """No wlan0, nothing can autoconnect: waiting 30 s more only delays the
    access point attempt that follows."""
    def router(args):
        if args[:2] == ("radio", "wifi") and len(args) == 2:
            return (0, "disabled", "")
        if "status" in args and "DEVICE,STATE" in args:
            return (0, "wlan0:unavailable", "")
        if "NAME,TYPE" in args:
            return (0, "milo-Maison:802-11-wireless", "")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher, no_delay(), \
         patch("backend.core.network.service.RADIO_SETTLE_POLLS", 2):
        await service.evaluate_reachability()
        await settle_until(lambda: service._recovery_arm is None)

    assert not fake.argv_containing("NAME,TYPE"), "the autoconnect window was entered"


@pytest.mark.asyncio
async def test_the_restore_reads_ethernet_from_the_status_it_is_handed(service):
    """Every NM event runs an evaluation with the status already read; while
    the override stands, a second `device show eth0` per event is pure cost."""
    service._radio_forced_on = True
    status = NetworkStatus(
        wifi_enabled=True,
        ethernet=EthernetStatus(connected=True, ip_address="192.168.1.55"),
        wifi=WifiConnectionStatus(connected=False),
        hotspot_active=False,
        link_lost=False,
    )

    fake, patcher = with_nmcli()
    with patcher:
        await service.evaluate_reachability(status)

    assert ("radio", "wifi", "off") in fake.calls
    assert not [c for c in fake.calls if "show" in c and "eth0" in c]


@pytest.mark.asyncio
async def test_a_restore_that_fails_is_tried_again(service):
    """Dropping the override on a failed `radio off` would leave the user's
    setting overridden for good, with one warning as the only trace."""
    service._radio_forced_on = True
    status = NetworkStatus(
        wifi_enabled=True,
        ethernet=EthernetStatus(connected=True, ip_address="192.168.1.55"),
        wifi=WifiConnectionStatus(connected=False),
        hotspot_active=False,
        link_lost=False,
    )

    def router(args):
        if args == ("radio", "wifi", "off"):
            return (1, "", "Error: timeout")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher:
        await service.evaluate_reachability(status)
        await service.evaluate_reachability(status)

    assert fake.calls.count(("radio", "wifi", "off")) == 2
    assert service._radio_forced_on is True


@pytest.mark.asyncio
async def test_connecting_to_a_network_ends_the_override(service):
    """A network the user picked while the override stood must survive the
    cable's return — the restore would otherwise switch off the radio
    carrying it."""
    service._radio_forced_on = True
    nm = FakeStations(ethernet=True, in_range={"Maison": "secret"})

    fake, patcher = with_nmcli(nm.route)
    with patcher:
        await service.connect("Maison", "secret")
        await service.evaluate_reachability()

    assert ("radio", "wifi", "off") not in fake.calls


@pytest.mark.asyncio
async def test_saving_a_network_ends_the_override(service):
    """The wizard on the AP path saves the home network, then reboots. The
    override is kept on disk across that reboot, so a save has to end it as a
    connect does — or the first ethernet lease switches off the radio that was
    to carry the network just chosen."""
    service._radio_forced_on = True
    nm = FakeStations(ethernet=True)

    fake, patcher = with_nmcli(nm.route)
    with patcher:
        await service.save_network("Maison", "secret")
        await service.evaluate_reachability()

    assert ("radio", "wifi", "off") not in fake.calls


@pytest.mark.asyncio
async def test_the_hotspot_profile_is_created_with_autoconnect_disabled(service):
    """`connection.autoconnect no` is load-bearing, and nmcli defaults it to yes.

    A profile that auto-connects survives a reboot and raises the AP by itself,
    so `_hotspot_active` stays False and nothing ever tears it down. The AP must
    only exist because the reachability rule decided it should.
    """
    fake, patcher = with_nmcli()
    with patcher, no_delay():
        await service.evaluate_reachability()
        await settle_until(lambda: service.hotspot_active)

    add = next(c for c in fake.calls if "add" in c)
    assert "connection.autoconnect" in add
    assert add[add.index("connection.autoconnect") + 1] == "no"
    assert add[add.index("wifi.mode") + 1] == "ap"
    assert add[add.index("ipv4.method") + 1] == "shared"


@pytest.mark.asyncio
async def test_the_hotspot_profile_lives_in_memory_at_the_address_its_guard_watches(service):
    """`save no` keeps the open AP's profile off the card, so no boot finds one
    to raise. The address is pinned rather than left to `shared`'s pick: the
    request guard and the captive DNS answer name it, and a pick that moved
    would hand the AP's clients a subnet the guard never refuses. IPv6 off, or
    a client reaches every listener by its link-local address, past the
    IPv4-only refusals."""
    fake, patcher = with_nmcli()
    with patcher, no_delay():
        await service.evaluate_reachability()
        await settle_until(lambda: service.hotspot_active)

    add = next(c for c in fake.calls if "add" in c)
    assert add[add.index("save") + 1] == "no"
    assert add[add.index("ipv4.addresses") + 1] == f"{RECOVERY_AP_ADDRESS}/{RECOVERY_AP_PREFIX}"
    assert add[add.index("ipv6.method") + 1] == "disabled"


@pytest.mark.asyncio
async def test_a_hotspot_that_fails_to_come_up_leaves_no_profile_behind(service):
    """The rollback is what keeps a failed attempt from arming a later boot."""
    def router(args):
        if args[:2] == ("connection", "up"):
            return (1, "", "no wireless device")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher, no_delay():
        await service.evaluate_reachability()
        await settle_until(lambda: len(fake.argv_containing("delete")) == 2)

    assert service.hotspot_active is False


@pytest.mark.asyncio
async def test_a_link_returning_while_the_access_point_activates_takes_it_down(service):
    """A power cut that takes the box down with the unit: the AP's `connection
    up` is still in flight when eth0's lease lands, and the evaluation that sees
    the lease cancels the task waiting on it. NM finishes the activation anyway.
    A flag set only once `up` had returned was still False, so that evaluation
    skipped the teardown and every later one did too — an open AP, NATed to the
    LAN, until the next reboot, with `hotspot_active: false` published."""
    nm = FakeNetworkManager()
    nm.hold("connection", "up")
    fake, patcher = with_nmcli(nm.route)
    with patcher, no_delay():
        await service.evaluate_reachability()
        await settle_until(lambda: nm.in_flight)
        nm.ethernet = True
        await service.evaluate_reachability()
        nm.release()
        await asyncio.gather(*nm.in_flight)

    assert HOTSPOT_NAME not in nm.active
    assert service.hotspot_active is False


@pytest.mark.asyncio
async def test_an_access_point_activation_that_times_out_is_deleted(service):
    """nmcli gave up on `connection up`, NM did not: the activation it accepted
    completes on its own, and the profile used to be deleted only on a refusal.
    A timeout has to take the profile with it, or the AP comes up unowned."""
    nm = FakeNetworkManager()
    nm.hold("connection", "up")
    fake, patcher = with_nmcli(nm.route)
    with patcher, no_delay(), \
         patch("backend.core.network.service.HOTSPOT_NMCLI_TIMEOUT_S", 0.01):
        await service.evaluate_reachability()
        await settle_until(lambda: service._recovery_arm is None)
        nm.release()
        await asyncio.gather(*nm.in_flight)

    assert HOTSPOT_NAME not in nm.active
    assert service.hotspot_active is False


@pytest.mark.parametrize("left", ["active", "activating"])
@pytest.mark.asyncio
async def test_an_access_point_left_up_by_a_previous_process_is_adopted_and_taken_down_when_ethernet_returns(service, left):
    """NM keeps the AP across a backend restart; the flag that owns it is the
    process's. Without a look at NM in `initialize()` the new process starts
    with `hotspot_active` False, and the cable's return tears down nothing.
    An activation the restart cut short counts too: NM finishes it anyway.

    The real settle window, so nothing but the adoption can account for the
    teardown, and no D-Bus: the adoption is an nmcli read and must not wait
    on the bus coming up."""
    nm = FakeNetworkManager(profiles={HOTSPOT_NAME: "ap"}, **{left: {HOTSPOT_NAME}})
    linked = NetworkStatus(
        wifi_enabled=True,
        ethernet=EthernetStatus(connected=True, ip_address="192.168.1.55"),
        wifi=WifiConnectionStatus(connected=False),
        hotspot_active=False,
        link_lost=False,
    )
    fake, patcher = with_nmcli(nm.route)
    with patcher, patch("backend.core.network.service.MessageBus") as bus:
        bus.side_effect = OSError("no system bus")
        await service.initialize()
        await service.evaluate_reachability()  # main.py's first look: no link yet
        nm.ethernet = True
        await service.evaluate_reachability(linked)

    assert HOTSPOT_NAME not in nm.active | nm.activating
    assert service.hotspot_active is False


@pytest.mark.asyncio
async def test_a_speakers_setup_hotspot_the_server_had_joined_is_not_adopted(service):
    """A station profile named like the AP joins a speaker's own `Milō`
    hotspot (one made by hand). Taken for the unit's AP across a restart,
    it would read `hotspot_active: true` — speaker discovery empty, adoption
    refused as "server in hotspot mode" — on a unit that raised nothing."""
    nm = FakeNetworkManager(profiles={HOTSPOT_NAME: "infrastructure"}, active={HOTSPOT_NAME})
    fake, patcher = with_nmcli(nm.route)
    with patcher, patch("backend.core.network.service.MessageBus") as bus:
        bus.side_effect = OSError("no system bus")
        await service.initialize()

    assert service.hotspot_active is False


@pytest.mark.asyncio
async def test_no_recovery_ap_is_raised_while_an_adoption_holds_it(service):
    """The speaker adoption takes a WiFi-only server off its home network on
    purpose, for as long as an association and a push take — past the settle
    window at worst. Read as "no link" (a speaker's hotspot is none), it raised
    the AP mid-push: the AP's stale-profile delete took the profile the
    adoption was riding, the push failed, and the adoption's cleanup then
    deleted the AP's. Released, the hold looks again — and a unit still on the
    speaker's hotspot has no link, so it gets its AP, beside that profile."""
    nm = FakeNetworkManager(profiles={ADOPTION_PROFILE: "infrastructure"}, active={ADOPTION_PROFILE})
    fake, patcher = with_nmcli(nm.route)
    with patcher, no_delay():
        async with service.recovery_held():
            await service.evaluate_reachability()
            await asyncio.sleep(0.05)
            assert service.hotspot_active is False
            assert not fake.argv_containing("add")
        await settle_until(lambda: HOTSPOT_NAME in nm.active)
        await service.cleanup()

    assert service.hotspot_active is True
    assert nm.profiles[ADOPTION_PROFILE] == "infrastructure", "the AP took the adoption's profile"


@pytest.mark.asyncio
async def test_a_recovery_timer_already_running_when_an_adoption_starts_is_disarmed(service):
    """The server lost its link a moment before the adoption began: the timer,
    past its wait and still deciding, would raise the AP mid-adoption — the
    very collision the hold exists for."""
    nm = FakeNetworkManager()
    nm.hold("radio", "wifi")
    fake, patcher = with_nmcli(nm.route)
    with patcher, no_delay():
        await service.evaluate_reachability()
        await settle_until(lambda: nm.in_flight)
        async with service.recovery_held():
            nm.release()
            await asyncio.gather(*nm.in_flight)
            await asyncio.sleep(0.05)
            assert service.hotspot_active is False
            assert not fake.argv_containing("add")
        await service.cleanup()


@pytest.mark.asyncio
async def test_an_access_point_being_raised_when_an_adoption_starts_is_let_finish(service):
    """Cut short between its profile and its `connection up`, a raise leaves
    `hotspot_active` set over an AP NM never brought up: the adoption refused
    for an AP nobody can join, and with the flag up no evaluation raised a real
    one. The hold waits for the raise instead, and is entered on an AP that is
    really there."""
    nm = FakeNetworkManager()
    nm.hold("connection", "add")
    fake, patcher = with_nmcli(nm.route)
    with patcher, no_delay():
        await service.evaluate_reachability()
        await settle_until(lambda: nm.in_flight)

        async def adoption():
            async with service.recovery_held():
                return service.hotspot_active, HOTSPOT_NAME in nm.active

        held = asyncio.ensure_future(adoption())
        await asyncio.sleep(0.02)
        nm.release()
        assert await held == (True, True)
        await service.cleanup()


@pytest.mark.asyncio
async def test_a_radio_forced_on_before_a_restart_is_still_switched_off_when_ethernet_returns():
    """The radio half of the same gap. The user had switched WiFi off, the
    recovery turned it back on, and the backend restarted before the cable came
    back: NM keeps the radio on, and the override was a flag of the process that
    died, so the new one took the "on" for the user's and kept it for good."""
    nm = FakeNetworkManager(radio=False)
    fake, patcher = with_nmcli(nm.route)
    with patcher:
        before = NetworkService(AsyncMock(), AsyncMock())
        with no_delay():
            await before.evaluate_reachability()
            await settle_until(lambda: HOTSPOT_NAME in nm.active)
        await before.cleanup()

        after = NetworkService(AsyncMock(), AsyncMock())
        with patch("backend.core.network.service.MessageBus") as bus:
            bus.side_effect = OSError("no system bus")
            await after.initialize()
            nm.ethernet = True
            await after.evaluate_reachability()

    assert nm.radio is False
    assert HOTSPOT_NAME not in nm.active


@pytest.mark.asyncio
async def test_the_override_is_on_disk_before_the_radio_is_on(service, radio_override_marker):
    """A restart landing between the two must leave a marker over a radio still
    off, which the restore answers with a redundant "off" — never a radio on
    with no marker, which the next process takes for the user's "on" for good."""
    nm = FakeNetworkManager(radio=False)
    nm.hold("radio", "wifi", "on")
    fake, patcher = with_nmcli(nm.route)
    with patcher, no_delay():
        await service.evaluate_reachability()
        await settle_until(lambda: nm.in_flight)
        on_disk = radio_override_marker.exists()
        nm.release()
        await settle_until(lambda: service._recovery_arm is None)

    assert on_disk


# --------------------------------------------------------------------------- #
# Back to the home network — what a WiFi-only unit depends on once the AP is up
# --------------------------------------------------------------------------- #

STATION = "milo-Maison"
STATION_UP = ("connection", "up", STATION)


@pytest.mark.asyncio
async def test_a_wifi_only_unit_returns_to_its_home_network_after_the_access_point(service):
    """The box rebooted for longer than the settle window and the AP took the
    unit's one radio. NM never autoconnects a profile onto a device that already
    carries one, and only a link coming back took the AP down — which, with the
    AP on the only radio, could not happen: the unit sat on its own hotspot,
    off milo.local, Spotify Connect, AirPlay and its satellites, until a reboot.

    Here the box is still away at the first try and back at the second."""
    nm = FakeNetworkManager(profiles={STATION: "infrastructure"}, unreachable={STATION: 1})
    fake, patcher = with_nmcli(nm.route)
    with patcher, no_delay(), patch("backend.core.network.service.STATION_RETRY_S", 0):
        await service.evaluate_reachability()
        await settle_until(lambda: STATION in nm.active)
        joined = len(fake.calls)
        await asyncio.sleep(0.05)
        after_joining = fake.calls[joined:]
        await service.cleanup()

    tries = [i for i, call in enumerate(fake.calls) if call == STATION_UP]
    assert len(tries) == 2
    assert fake.calls[tries[0] - 1] == ("connection", "delete", HOTSPOT_NAME), \
        "the station was asked for while the AP still held the radio"
    assert ("connection", "up", HOTSPOT_NAME) in fake.calls[tries[0]:tries[1]], \
        "a failed try left the unit with neither its network nor its AP"
    assert after_joining == [], "the retry outlived the network it joined"
    assert HOTSPOT_NAME not in nm.profiles
    assert service.hotspot_active is False


@pytest.mark.parametrize("client", ["socket-held-open", "page-polling"])
@pytest.mark.asyncio
async def test_the_station_retry_spares_an_access_point_serving_a_client(service, client):
    """Each try takes the AP down for as long as `connection up` runs. Under
    someone repairing the unit over it — typing the box's new password — that
    is the page dropping mid-sentence, every few minutes. A page can sit on its
    socket for minutes without one HTTP request, so an open socket counts as
    much as a request served since the last try."""
    nm = FakeNetworkManager(profiles={STATION: "infrastructure"})

    async def a_page_polling_over_the_ap():
        while True:
            with service.serving_recovery_client():
                await asyncio.sleep(0)
            await asyncio.sleep(0.001)

    fake, patcher = with_nmcli(nm.route)
    with patcher, no_delay(), patch("backend.core.network.service.STATION_RETRY_S", 0.01), \
         patch("backend.core.network.service.STATION_RETRY_MAX_HOLDS", 1000):
        with contextlib.ExitStack() as on_the_ap:
            if client == "socket-held-open":
                on_the_ap.enter_context(service.serving_recovery_client())
            else:
                poller = asyncio.ensure_future(a_page_polling_over_the_ap())
                on_the_ap.callback(poller.cancel)
            await service.evaluate_reachability()
            await settle_until(lambda: HOTSPOT_NAME in nm.active)
            raised = len(fake.calls)
            await asyncio.sleep(0.1)  # ten retry periods
            while_served = fake.calls[raised:]

        # The client left: the next try goes ahead, so the loop was alive.
        await settle_until(lambda: STATION in nm.active)
        await service.cleanup()

    assert while_served == [], "the AP was taken from under its client"


@pytest.mark.asyncio
async def test_a_client_that_never_leaves_holds_the_retry_off_a_bounded_number_of_times(service, caplog):
    """A phone that once joined the open `Milō` rejoins it on its own while the
    box is away, and a widget or a forgotten tab keeps polling over it. Holding
    every try off for it would keep the unit off its home network for good —
    the very failure the retry exists for."""
    nm = FakeNetworkManager(profiles={STATION: "infrastructure"})
    fake, patcher = with_nmcli(nm.route)
    caplog.set_level(logging.INFO, logger="backend.core.network.service")
    with patcher, no_delay(), patch("backend.core.network.service.STATION_RETRY_S", 0), \
         patch("backend.core.network.service.STATION_RETRY_MAX_HOLDS", 2), \
         service.serving_recovery_client():
        await service.evaluate_reachability()
        await settle_until(lambda: STATION in nm.active)
        await service.cleanup()

    holds = [r for r in caplog.records if "a client is using the access point" in r.getMessage()]
    assert len(holds) == 2


@pytest.mark.asyncio
async def test_every_saved_network_gets_its_turn(service):
    """The WiFi panel's connect replaces the profiles of the SSID it joins, not
    the others: a unit moved to a new box keeps the old one's profile, and nmcli
    lists profiles by name. Always asking for the first would ask for a box long
    gone, every time."""
    nm = FakeNetworkManager(
        profiles={"milo-Ancienne": "infrastructure", STATION: "infrastructure"},
        unreachable={"milo-Ancienne": 1000},
    )
    fake, patcher = with_nmcli(nm.route)
    with patcher, no_delay(), patch("backend.core.network.service.STATION_RETRY_S", 0):
        await service.evaluate_reachability()
        await settle_until(lambda: STATION in nm.active)
        await service.cleanup()

    tries = [call[2] for call in fake.calls if call[:2] == ("connection", "up") and call[2] != HOTSPOT_NAME]
    assert tries == ["milo-Ancienne", STATION]


@pytest.mark.asyncio
async def test_a_network_saved_over_the_access_point_is_the_next_one_tried(service):
    """A server whose home WiFi changed is repaired by a phone on its AP, which
    saves the new network (Settings › Network) and cannot forget the old one
    over it. The old profile stays, and took the turn that was its own: while
    the phone held tries off, one came every quarter of an hour, so the network
    saved was tried a turn late, half the time."""
    nm = FakeNetworkManager(
        profiles={"milo-Ancienne": "infrastructure"},
        unreachable={"milo-Ancienne": 1000},
    )
    fake, patcher = with_nmcli(nm.route)
    with patcher, no_delay(), patch("backend.core.network.service.STATION_RETRY_S", 0.001), \
         patch("backend.core.network.service.STATION_RETRY_MAX_HOLDS", 1000):
        with service.serving_recovery_client():
            await service.evaluate_reachability()
            await settle_until(lambda: HOTSPOT_NAME in nm.active)
            await service.save_network("Maison", "secret")
        await settle_until(lambda: STATION in nm.active)
        await service.cleanup()

    tries = [call[2] for call in fake.calls if call[:2] == ("connection", "up") and call[2] != HOTSPOT_NAME]
    assert tries == [STATION]


@pytest.mark.asyncio
async def test_a_save_that_asks_for_a_try_gets_it_under_a_client_of_the_access_point(service):
    """Settings › Network over the AP is how a phone repairs a unit whose home
    network changed. Its save waited for the retry loop, which the phone that
    saved holds off: measured on a unit, ten minutes of "Not connected" on the
    phone with nothing said, the network joined only once the holds ran out.
    `try_now` is that turn, taken whoever is on the AP."""
    nm = FakeNetworkManager(profiles={})
    fake, patcher = with_nmcli(nm.route)
    with patcher, no_delay(), patch("backend.core.network.service.STATION_RETRY_S", 1000), \
         patch("backend.core.network.service.STATION_TRY_AFTER_SAVE_S", 0):
        with service.serving_recovery_client():
            await service.evaluate_reachability()
            await settle_until(lambda: HOTSPOT_NAME in nm.active)
            await service.save_network("Maison", "secret", try_now=True)
            await settle_until(lambda: STATION in nm.active)
        await service.cleanup()

    assert service.hotspot_active is False


@pytest.mark.asyncio
async def test_an_access_point_adopted_after_a_restart_still_retries_the_home_network(service):
    """The adoption gives the new process the AP's teardown; without the retry
    loop too, a backend restart while the box was away would leave the AP the
    previous process raised on the radio for good."""
    nm = FakeNetworkManager(
        profiles={HOTSPOT_NAME: "ap", STATION: "infrastructure"}, active={HOTSPOT_NAME},
    )
    fake, patcher = with_nmcli(nm.route)
    with patcher, patch("backend.core.network.service.MessageBus") as bus, \
         patch("backend.core.network.service.STATION_RETRY_S", 0):
        bus.side_effect = OSError("no system bus")
        await service.initialize()
        await settle_until(lambda: STATION in nm.active)
        await service.cleanup()

    assert HOTSPOT_NAME not in nm.profiles
    assert service.hotspot_active is False


@pytest.mark.asyncio
async def test_a_failed_home_network_try_raises_no_access_point_under_an_adoption(service):
    """A try takes the AP down for its `connection up`, and an adoption can
    begin in that window — `hotspot_active` reads False. Raised again when the
    try fails, the AP takes the radio mid-adoption; held, it comes back once the
    hold is released and the unit still has no link."""
    nm = FakeNetworkManager(
        profiles={HOTSPOT_NAME: "ap", STATION: "infrastructure"}, active={HOTSPOT_NAME},
        unreachable={STATION: 1},
    )
    nm.hold(*STATION_UP)
    fake, patcher = with_nmcli(nm.route)
    with patcher, no_delay(), patch("backend.core.network.service.MessageBus") as bus, \
         patch("backend.core.network.service.STATION_RETRY_S", 0):
        bus.side_effect = OSError("no system bus")
        await service.initialize()
        await settle_until(lambda: nm.in_flight)
        async with service.recovery_held():
            nm.release()
            await asyncio.gather(*nm.in_flight)
            await asyncio.sleep(0.05)
            assert HOTSPOT_NAME not in nm.active
            assert service.hotspot_active is False
        released = len(fake.calls)
        await settle_until(lambda: ("connection", "up", HOTSPOT_NAME) in fake.calls[released:])
        await service.cleanup()


# --------------------------------------------------------------------------- #
# The recovery gate while the AP comes down
# --------------------------------------------------------------------------- #

async def recovery_gate_answer(service) -> int:
    """What `api/middleware.py::RecoveryAccessPointGuard` answers a client of
    the AP sending `PUT /api/system/ssh` through nginx, on a unit set up."""
    scope = {
        "type": "http", "method": "PUT", "path": "/api/system/ssh",
        "client": ("127.0.0.1", 41000), "headers": [(b"x-real-ip", b"10.42.0.5")],
    }
    sent = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    settings = Mock(get_setting=AsyncMock(return_value=True))
    await RecoveryAccessPointGuard(app, service, settings)(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


@pytest.mark.asyncio
async def test_the_recovery_gate_is_shut_while_an_adoption_holds_wlan0_on_a_speakers_hotspot(service):
    """The adoption takes the server onto a fresh speaker's open AP, as a
    10.42.0.x client beside anyone else who joined it, and no AP of its own is
    up meanwhile. Judged on its own AP alone, the guard let any of them enable
    SSH or set the password for as long as the association and the push took."""
    nm = FakeNetworkManager(profiles={ADOPTION_PROFILE: "infrastructure"}, active={ADOPTION_PROFILE})
    fake, patcher = with_nmcli(nm.route)
    with patcher, no_delay():
        assert await recovery_gate_answer(service) == 200
        async with service.recovery_held():
            assert service.hotspot_active is False
            assert await recovery_gate_answer(service) == 403
        await service.cleanup()


@pytest.mark.parametrize("teardown", ["station-retry", "link-back"])
@pytest.mark.asyncio
async def test_the_recovery_gate_stays_shut_while_nm_is_still_removing_the_access_point(service, teardown):
    """The gate reads `hotspot_active`, and a flag dropped before `connection
    delete` returned opened it while NM still carried the AP and its clients:
    a 10.42.0.x client got SSH, the sudo password or the setup reset for as long
    as the delete ran. The station retry opens that window at a time anyone can
    predict, every STATION_RETRY_S; the cable's return opens it too."""
    nm = FakeNetworkManager(
        profiles={HOTSPOT_NAME: "ap", STATION: "infrastructure"}, active={HOTSPOT_NAME},
        unreachable={STATION: 1},
    )
    nm.hold("connection", "delete", HOTSPOT_NAME)
    retry_s = 0 if teardown == "station-retry" else 300
    fake, patcher = with_nmcli(nm.route)
    with patcher, patch("backend.core.network.service.MessageBus") as bus, \
         patch("backend.core.network.service.STATION_RETRY_S", retry_s):
        bus.side_effect = OSError("no system bus")
        await service.initialize()
        evaluation = None
        if teardown == "link-back":
            nm.ethernet = True
            evaluation = asyncio.ensure_future(service.evaluate_reachability())
        await settle_until(lambda: nm.in_flight)
        answer = await recovery_gate_answer(service)
        nm.release()
        await asyncio.gather(*nm.in_flight, *([evaluation] if evaluation else []))
        await service.cleanup()

    assert answer == 403


# nmcli(1)'s status 7, "Connection deletion failed": NM answered and kept the AP.
NM_REFUSED_DELETE = (7, "", "Error: not all connections deleted.")
DELETE_AP = ("connection", "delete", HOTSPOT_NAME)


@pytest.mark.parametrize("failure", ["timeout", "refused"])
@pytest.mark.asyncio
async def test_an_access_point_nm_did_not_remove_is_removed_at_the_next_evaluation(service, failure):
    """The cable is back and the delete never happened. Ownership dropped
    anyway left an open AP, NATed to the LAN, that nothing would take down
    again — `hotspot_active: false` published everywhere and the recovery gate
    open to its clients — until a reboot."""
    nm = FakeNetworkManager(profiles={HOTSPOT_NAME: "ap"}, active={HOTSPOT_NAME})
    fake, patcher = with_nmcli(nm.route)
    with patcher, patch("backend.core.network.service.MessageBus") as bus:
        bus.side_effect = OSError("no system bus")
        await service.initialize()
        nm.ethernet = True
        nm.fail(*DELETE_AP, answer=NM_REFUSED_DELETE if failure == "refused" else None)
        await service.evaluate_reachability()
        while_still_up = await recovery_gate_answer(service)
        await service.evaluate_reachability()
        await service.cleanup()

    assert while_still_up == 403
    assert HOTSPOT_NAME not in nm.active
    assert service.hotspot_active is False


READ_DEVICES = ("-t", "-f", "TYPE,STATE,CONNECTION", "device", "status")
READ_AP_MODE = ("-t", "-f", "802-11-wireless.mode", "connection", "show", HOTSPOT_NAME)
NM_NOT_RUNNING = (8, "", "Error: NetworkManager is not running.")


def refusing(nm, command, times):
    """`nm.route`, with the next `times` of `command` refused by NM."""
    left = [times]

    def route(args):
        if args == command and left[0]:
            left[0] -= 1
            return NM_NOT_RUNNING
        return nm.route(args)
    return route


@pytest.mark.parametrize("read", [READ_DEVICES, READ_AP_MODE], ids=["devices", "ap-mode"])
@pytest.mark.parametrize("failure", ["timeout", "refused"])
@pytest.mark.asyncio
async def test_an_access_point_whose_adoption_could_not_be_read_is_adopted_later(service, failure, read):
    """The restart's one look at NM failed, and a failed read counted as "no
    AP": the AP NM kept stayed up with nothing owning it, `hotspot_active`
    false, and the recovery gate open to its clients — for good once the link
    was back, since no evaluation then has an AP to take down. Unread, the gate
    stays shut; the read is tried again, and the AP adopted then goes down.
    Either read can fail: the devices, or the mode that tells our AP from a
    station profile of the same name."""
    nm = FakeNetworkManager(ethernet=True, profiles={HOTSPOT_NAME: "ap"}, active={HOTSPOT_NAME})
    nm.fail(*read, answer=NM_NOT_RUNNING if failure == "refused" else None)
    fake, patcher = with_nmcli(nm.route)
    with patcher, patch("backend.core.network.service.MessageBus") as bus, \
         patch("backend.core.network.service.ADOPTION_READ_RETRY_S", 0):
        bus.side_effect = OSError("no system bus")
        await service.initialize()
        while_unread = await recovery_gate_answer(service)
        await settle_until(lambda: HOTSPOT_NAME not in nm.active)
        once_down = await recovery_gate_answer(service)
        await service.cleanup()

    assert while_unread == 403
    assert once_down == 200
    assert service.hotspot_active is False


@pytest.mark.asyncio
async def test_an_unread_access_point_read_back_as_absent_opens_the_recovery_gate(service):
    """No AP was left up; the restart merely could not say so. Left unread, the
    gate refuses 10.42.0.0/24 — a home LAN may well be that subnet — the SSH
    switch and the password for the whole run, over an AP that does not exist."""
    nm = FakeNetworkManager(ethernet=True)
    fake, patcher = with_nmcli(refusing(nm, READ_DEVICES, 1))
    with patcher, patch("backend.core.network.service.MessageBus") as bus, \
         patch("backend.core.network.service.ADOPTION_READ_RETRY_S", 0):
        bus.side_effect = OSError("no system bus")
        await service.initialize()
        while_unread = await recovery_gate_answer(service)
        await settle_until(lambda: not service.recovery_ap_possible)
        once_read = await recovery_gate_answer(service)
        await service.cleanup()

    assert while_unread == 403
    assert once_read == 200
    assert service.hotspot_active is False


@pytest.mark.asyncio
async def test_an_access_point_unread_past_the_quick_tries_is_still_read_again(service):
    """The read gave up after its quick tries, for the rest of the run: an AP
    really left up stayed on air, open, unowned and with no home-network retry
    — every route the gate does not cover reachable from anyone in radio range
    — and an AP that was never there kept the gate shut on its subnet."""
    nm = FakeNetworkManager(ethernet=True, profiles={HOTSPOT_NAME: "ap"}, active={HOTSPOT_NAME})
    fake, patcher = with_nmcli(refusing(nm, READ_DEVICES, 1 + ADOPTION_READ_TRIES + 1))
    with patcher, patch("backend.core.network.service.MessageBus") as bus, \
         patch("backend.core.network.service.ADOPTION_READ_RETRY_S", 0), \
         patch("backend.core.network.service.ADOPTION_READ_SLOW_RETRY_S", 0):
        bus.side_effect = OSError("no system bus")
        await service.initialize()
        await settle_until(lambda: HOTSPOT_NAME not in nm.active)
        once_down = await recovery_gate_answer(service)
        await service.cleanup()

    assert once_down == 200
    assert service.hotspot_active is False


@pytest.mark.asyncio
async def test_an_access_point_unread_past_the_quick_tries_is_read_at_the_slow_pace(service, caplog):
    """Past its quick tries the read slows down: one nmcli spawn every
    `ADOPTION_READ_RETRY_S` for the life of the process, against an NM that may
    stay unreachable for hours, is load the slow interval exists to spare. The
    quick interval is collapsed and the slow one is an hour, so once the quick
    tries are spent the next read is out of reach: NM answering by then must
    leave the AP unread and up, where reading at the quick pace would adopt it
    and take it down at once."""
    nm = FakeNetworkManager(ethernet=True, profiles={HOTSPOT_NAME: "ap"}, active={HOTSPOT_NAME})
    unreachable = [True]

    def router(args):
        if args == READ_DEVICES and unreachable[0]:
            return NM_NOT_RUNNING
        return nm.route(args)

    fake, patcher = with_nmcli(router)
    caplog.set_level(logging.ERROR, logger="backend.core.network.service")
    with patcher, patch("backend.core.network.service.MessageBus") as bus, \
         patch("backend.core.network.service.ADOPTION_READ_RETRY_S", 0), \
         patch("backend.core.network.service.ADOPTION_READ_SLOW_RETRY_S", 3600):
        bus.side_effect = OSError("no system bus")
        await service.initialize()
        await settle_until(lambda: any(
            f"after {ADOPTION_READ_TRIES} tries" in r.getMessage() for r in caplog.records
        ))
        unreachable[0] = False
        reads_then = len(fake.argv_containing("device"))
        for _ in range(50):
            await asyncio.sleep(0)
        reads_since = len(fake.argv_containing("device")) - reads_then
        gate = await recovery_gate_answer(service)
        await service.cleanup()

    assert reads_since == 0
    assert HOTSPOT_NAME in nm.active
    assert gate == 403


@pytest.mark.parametrize("failure", ["timeout", "refused"])
@pytest.mark.asyncio
async def test_an_access_point_read_slowly_logs_its_outage_once(service, caplog, failure):
    """The slow re-reads run for as long as NM stays unreachable, so a warning
    per read — and, on a timeout, `_run_nmcli`'s error, which every UI shows as
    a banner — grew without bound: one banner a minute for hours. The outage is
    announced once, by the error at the end of the quick tries; the count of
    lines at warning and above must not depend on how many slow reads follow."""
    nm = FakeNetworkManager(ethernet=True, profiles={HOTSPOT_NAME: "ap"}, active={HOTSPOT_NAME})
    slow_reads = 20
    left = [1 + ADOPTION_READ_TRIES + slow_reads]

    async def stall():
        raise asyncio.TimeoutError

    def router(args):
        if args == READ_DEVICES and left[0]:
            left[0] -= 1
            return stall() if failure == "timeout" else NM_NOT_RUNNING
        return nm.route(args)

    fake, patcher = with_nmcli(router)
    caplog.set_level(logging.DEBUG, logger="backend.core.network.service")
    with patcher, patch("backend.core.network.service.MessageBus") as bus, \
         patch("backend.core.network.service.ADOPTION_READ_RETRY_S", 0), \
         patch("backend.core.network.service.ADOPTION_READ_SLOW_RETRY_S", 0):
        bus.side_effect = OSError("no system bus")
        await service.initialize()
        await settle_until(lambda: HOTSPOT_NAME not in nm.active)
        await service.cleanup()

    assert left[0] == 0
    loud = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "D-Bus unavailable" not in r.getMessage()
    ]
    per_read = 2 if failure == "timeout" else 1
    assert len(loud) == (1 + ADOPTION_READ_TRIES) * per_read + 1
    assert any("read at last" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("failure", ["timeout", "refused"])
@pytest.mark.asyncio
async def test_a_station_retry_whose_teardown_nm_did_not_carry_out_tries_again(service, failure):
    """A try that asked for the station anyway did it under an AP NM still
    carried, and with ownership gone the loop ended there: a WiFi-only unit
    back on its own hotspot for good, which is what the retry exists to end."""
    nm = FakeNetworkManager(profiles={HOTSPOT_NAME: "ap", STATION: "infrastructure"}, active={HOTSPOT_NAME})
    nm.fail(*DELETE_AP, answer=NM_REFUSED_DELETE if failure == "refused" else None)
    fake, patcher = with_nmcli(nm.route)
    with patcher, patch("backend.core.network.service.MessageBus") as bus, \
         patch("backend.core.network.service.STATION_RETRY_S", 0):
        bus.side_effect = OSError("no system bus")
        await service.initialize()
        await settle_until(lambda: STATION in nm.active)
        await service.cleanup()

    deletes = [i for i, call in enumerate(fake.calls) if call == DELETE_AP]
    tries = [i for i, call in enumerate(fake.calls) if call == STATION_UP]
    assert len(deletes) == 2
    assert tries[0] > deletes[1], "the station was asked for under an AP NM still carried"
    assert service.hotspot_active is False


@pytest.mark.asyncio
async def test_the_retry_loop_takes_down_an_access_point_left_up_once_a_link_is_back(service):
    """An ethernet-only unit has no saved network to try, so its loop did
    nothing: an AP whose teardown failed at the cable's return waited for the
    next NM event, which on a settled link can be hours away (a lease renewal)."""
    nm = FakeNetworkManager(ethernet=True, profiles={HOTSPOT_NAME: "ap"}, active={HOTSPOT_NAME})
    nm.fail(*DELETE_AP)
    fake, patcher = with_nmcli(nm.route)
    with patcher, patch("backend.core.network.service.MessageBus") as bus, \
         patch("backend.core.network.service.STATION_RETRY_S", 0):
        bus.side_effect = OSError("no system bus")
        await service.initialize()
        await settle_until(lambda: HOTSPOT_NAME not in nm.active)
        await service.cleanup()

    assert service.hotspot_active is False
    assert not fake.argv_containing("up")


@pytest.mark.parametrize("failure", ["timeout", "refused"])
@pytest.mark.asyncio
async def test_an_access_point_whose_activation_and_removal_both_fail_stays_owned(service, failure):
    """`connection up` timed out and NM finished the activation anyway; the
    delete that should have followed did not happen either. A flag dropped in
    a `finally` regardless left that AP up with no owner."""
    nm = FakeNetworkManager()
    nm.hold("connection", "up")
    # The first delete is the stale-profile clear-out before `connection add`.
    nm.fail(*DELETE_AP, answer=NM_REFUSED_DELETE if failure == "refused" else None, after=1)
    fake, patcher = with_nmcli(nm.route)
    with patcher, no_delay(), \
         patch("backend.core.network.service.HOTSPOT_NMCLI_TIMEOUT_S", 0.01):
        await service.evaluate_reachability()
        await settle_until(lambda: service._recovery_arm is None)
        nm.release()
        await asyncio.gather(*nm.in_flight)
        while_up = await recovery_gate_answer(service)
        nm.ethernet = True
        await service.evaluate_reachability()
        await service.cleanup()

    assert while_up == 403
    assert HOTSPOT_NAME not in nm.active
    assert service.hotspot_active is False


# --------------------------------------------------------------------------- #
# Fail-open on D-Bus
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_initialize_fails_open_when_networkmanager_dbus_is_absent(service):
    """CLAUDE.md's rule: no D-Bus must degrade, never raise — dev runs without NM.

    A raise here aborts initialize_services() and the whole backend never comes
    up, on a developer machine and on any unit where NM is slow to appear.
    """
    fake, patcher = with_nmcli()
    with patcher, patch("backend.core.network.service.MessageBus") as bus:
        bus.side_effect = OSError("no system bus")
        assert await service.initialize() is False

    assert service.hotspot_active is False


@pytest.mark.asyncio
async def test_the_nmcli_read_path_still_works_without_dbus(service):
    """Degraded means "no live updates", not "no status" — the badge must still fill."""
    fake, patcher = with_nmcli(_status_router(eth=("Wired connection 1", "192.168.1.42")))
    with patcher, patch("backend.core.network.service.MessageBus") as bus:
        bus.side_effect = OSError("no system bus")
        await service.initialize()

    with patcher:
        status = await service.get_network_status()

    assert status.ethernet.connected is True
    assert status.ethernet.ip_address == "192.168.1.42"


@pytest.mark.asyncio
async def test_cleanup_is_idempotent_on_a_service_that_never_connected(service):
    """cleanup() runs on the failed-initialize path, before anything was attached."""
    await service.cleanup()
    await service.cleanup()


# --------------------------------------------------------------------------- #
# The live signal read (GET /api/network/wifi/signal)
# --------------------------------------------------------------------------- #

class FakeApProxy:
    """Stands in for the NM AccessPoint object dbus-next hands back."""

    def __init__(self, ssid: bytes, strength: int):
        self._ssid = ssid
        self._strength = strength

    def get_interface(self, name):
        assert name == "org.freedesktop.NetworkManager.AccessPoint", name
        return self

    async def get_ssid(self):
        return self._ssid

    async def get_strength(self):
        return self._strength


@pytest.mark.asyncio
async def test_wifi_signal_is_read_from_the_ap_without_spawning_nmcli(service):
    """The signal poll must not cost what a full status costs.

    A view showing the signal arc re-reads it every few seconds for as long as
    it is on screen, which is only affordable because this path answers from
    the anchored AccessPoint proxy. Routing it through get_network_status()
    instead — the obvious "reuse" — would put four nmcli forks on that cadence,
    which is precisely the regression this endpoint was split out to end, and
    nothing else in either suite would notice.

    The returned 63 is the non-triviality guard (a read broken into returning
    None must fail here, not pass quietly); the claim is the empty call list.
    """
    service._ap_proxy = FakeApProxy(b"Freebox-CA3555", 63)

    fake, patcher = with_nmcli()
    with patcher:
        signal = await service.get_wifi_signal()

    assert signal == 63
    assert fake.calls == []


@pytest.mark.asyncio
async def test_wifi_signal_fails_open_when_no_access_point_is_anchored(service):
    """No NM D-Bus (dev host) or no association → None, and still no nmcli.

    The arc renders greyed on null; raising here would surface as an error
    banner every few seconds on a machine whose only fault is having no WiFi.
    """
    fake, patcher = with_nmcli()
    with patcher:
        signal = await service.get_wifi_signal()

    assert signal is None
    assert fake.calls == []




# --------------------------------------------------------------------------- #
# Which profiles a forget deletes — and which it must not
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_forgetting_a_network_takes_every_profile_that_carries_it(service):
    """One SSID can own several profiles — Milō's `milo-<ssid>`, one named by
    the SSID as `nmcli device wifi connect` writes it, Imager's `preconfigured`
    — and any one left behind still autoconnects to a network the user forgot.
    A profile counts by the SSID it carries, not by its name."""
    nm = FakeStations([
        ("milo-Livebox", "u-1", "Livebox", "k"),
        ("Livebox", "u-2", "Livebox", "k"),
        ("preconfigured", "u-3", "Livebox", "k"),
        ("Maison", "u-4", "Maison", "k"),
    ])
    fake, patcher = with_nmcli(nm.route)
    with patcher:
        await service.forget_network("Livebox")

    assert set(nm.profiles) == {"u-4"}


@pytest.mark.asyncio
async def test_forgetting_a_network_spares_a_different_ssid_that_ends_with_its_name(service):
    """`Guest-Livebox` is someone else's network, not a variant of `Livebox`.

    The name arrives free-form — an unvalidated path segment on
    `DELETE /api/network/wifi/saved/{ssid}`, a `min_length=1` string on
    WifiConnectRequest — and whatever it selects is handed to `nmcli connection
    delete`. Selection wide enough to reach a profile it was not given loses
    credentials the user must retype, which on a wifi-only unit means from the
    setup AP after a reboot.
    """
    nm = FakeStations([
        ("milo-Livebox", "u-1", "Livebox", "k"),
        ("Guest-Livebox", "u-2", "Guest-Livebox", "k"),
        ("milo-Guest-Livebox", "u-3", "Guest-Livebox", "k"),
    ])
    fake, patcher = with_nmcli(nm.route)
    with patcher:
        await service.forget_network("Livebox")

    assert set(nm.profiles) == {"u-2", "u-3"}


@pytest.mark.asyncio
async def test_forgetting_a_network_never_deletes_a_wired_profile(service):
    """The route is `/wifi/saved/{ssid}` and takes the segment verbatim.

    `DELETE /api/network/wifi/saved/Wired%20connection%201` names the wired
    profile; the connection-type filter is the only thing between that URL and
    the ethernet link the appliance is actually reachable on.
    """
    nm = FakeStations([("milo-Wired connection 1", "u-1", "Wired connection 1", "k")])
    fake, patcher = with_nmcli(nm.route)
    with patcher:
        await service.forget_network("Wired connection 1")

    assert nm.profiles == {}
    assert fake.argv_containing("delete") == [("connection", "delete", "uuid", "u-1")]


# --------------------------------------------------------------------------- #
# Saving credentials — the become-client path, run once per unit, over the AP
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_saving_credentials_leaves_the_live_connection_alone(service):
    """POST /api/setup/become-client calls this while the operator is on the AP.

    It saves, persists setup_completed, then reboots. Bringing the new profile
    up here would drop the hotspot mid-request: the caller never sees the
    response, the flag is never written, and the unit comes back still
    unconfigured with a pending_client_role.json it will act on. `connect()` is
    the method that may take the link down; this one may not.
    """
    nm = FakeStations(hotspot=True)
    fake, patcher = with_nmcli(nm.route)
    with patcher:
        await service.save_network("Maison", "secret")

    assert [p["ssid"] for p in nm.profiles.values()] == ["Maison"], "no profile was created — the save did nothing"
    assert nm.hotspot is True
    assert not fake.argv_containing("up")


@pytest.mark.asyncio
async def test_a_saved_network_replaces_the_profiles_its_ssid_had_once_it_is_written(service):
    """The wizard on the AP cannot try the key it saves, so the save replaces
    whatever the SSID held — Imager's `preconfigured` included, which NM could
    otherwise keep picking with its stale key — and leaves other networks be.
    The new profile is on disk and autoconnects: it is what the unit joins after
    the wizard's reboot."""
    nm = FakeStations([
        ("preconfigured", "u-1", "Maison", "old"),
        ("milo-Maison", "u-2", "Maison", "older"),
        ("milo-Voisins", "u-3", "Voisins", "k"),
    ])
    fake, patcher = with_nmcli(nm.route)
    with patcher:
        await service.save_network("Maison", "secret")

    kept = {(p["ssid"], p["psk"], p["on_disk"], p["autoconnect"]) for p in nm.profiles.values()}
    assert kept == {("Maison", "secret", True, True), ("Voisins", "k", True, True)}


@pytest.mark.asyncio
async def test_a_save_nm_refuses_keeps_the_profile_the_ssid_had(service):
    """The add comes first: a key NM refuses (too short for WPA) used to arrive
    after the SSID's profiles were gone, and the save failed with nothing left."""
    nm = FakeStations([("milo-Maison", "u-1", "Maison", "secret")])
    nm.fail("connection", "add", answer=(2, "", "802-11-wireless-security.psk: property is invalid"))
    fake, patcher = with_nmcli(nm.route)
    with patcher, pytest.raises(RuntimeError, match="WiFi save failed"):
        await service.save_network("Maison", "short")

    assert set(nm.profiles) == {"u-1"}


@pytest.mark.asyncio
async def test_a_saved_profile_keeps_its_password_in_the_system_file(service):
    """psk-flags=0 is what makes the profile usable on a headless unit.

    NM's default defers the secret to an agent; there is none here, so the
    profile would come up asking for a password nobody can type — after the
    reboot, on a box with no screen and no network to reach it by.
    """
    nm = FakeStations()
    fake, patcher = with_nmcli(nm.route)
    with patcher:
        await service.save_network("Maison", "secret")

    add = fake.argv_containing("add")[0]
    assert "wifi-sec.psk-flags" in add
    assert add[add.index("wifi-sec.psk-flags") + 1] == "0"


@pytest.mark.asyncio
async def test_an_open_network_is_saved_with_no_security_settings_at_all(service):
    """A wpa-psk key-mgmt on an open network makes NM refuse to activate it."""
    nm = FakeStations()
    fake, patcher = with_nmcli(nm.route)
    with patcher:
        await service.save_network("Cafe", None)

    add = fake.argv_containing("add")[0]
    assert "Cafe" in add
    assert not [a for a in add if a.startswith("wifi-sec.")]


@pytest.mark.asyncio
async def test_a_refused_profile_creation_is_raised_not_swallowed(service):
    """become-client rolls its marker file back on this exception.

    Returning quietly would let it write setup_completed and reboot into the
    client role with no wifi profile — a satellite that joins no network and
    can only be recovered by reflashing the card.
    """
    def router(args):
        if args[:2] == ("connection", "add"):
            return (1, "", "802-11-wireless-security.psk: property is invalid")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher, pytest.raises(RuntimeError, match="WiFi save failed"):
        await service.save_network("Maison", "secret")


# --------------------------------------------------------------------------- #
# No test in this file may reach the real NetworkManager
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def never_the_real_nmcli():
    """The default spawn RAISES, so a path this file forgot to double fails.

    Modelled on `test_rotary.py::never_the_real_gpio`. The repo is checked out
    on the appliance, this machine's `nmcli` is the live NetworkManager, and the
    argv this service builds includes `connection delete`, `device disconnect
    wlan0` and `radio wifi off`. Each `with_nmcli(...)` below re-patches the same
    name for its own scope; what this covers is everything that does not.
    """
    async def _refuse(program, *args, **kwargs):
        raise AssertionError(
            f"a real process was spawned: {program} {' '.join(map(str, args))}"
        )

    with patch("backend.core.network.service.asyncio.create_subprocess_exec",
               new=_refuse):
        yield


@pytest.fixture(autouse=True)
def radio_override_marker(tmp_path):
    """The radio override's marker, in a temp dir. The live one is under
    /var/lib/milo, which the suite refuses to write, and every test that runs
    the recovery or sets the radio reaches it."""
    marker = tmp_path / "wifi_radio_forced"
    with patch("backend.core.network.service.WIFI_RADIO_FORCED_FILE", marker):
        yield marker


# --------------------------------------------------------------------------- #
# The credentials handed to a speaker being adopted
# --------------------------------------------------------------------------- #
#
# Shapes from this appliance, 2026-08-26:
#
#   nmcli -t -f NAME,UUID,DEVICE connection show --active
#       Wired connection 1:<uuid>:eth0
#       milo-Freebox-CA3555:<uuid>:wlan0
#       tun0:<uuid>:tun0
#       lo:<uuid>:lo
#
#   nmcli -s -t -f 802-11-wireless.ssid,802-11-wireless-security.psk \
#         connection show uuid <uuid>
#       802-11-wireless.ssid:<ssid>
#       802-11-wireless-security.psk:<psk>

FREEBOX_UUID = "3b7f2a9c-6d41-4e0b-9a55-1c2d3e4f5a6b"

ACTIVE_CONNECTIONS = (
    "Wired connection 1:a1f0c0de-0000-4000-8000-000000000001:eth0\n"
    f"milo-Freebox-CA3555:{FREEBOX_UUID}:wlan0\n"
    "tun0:a1f0c0de-0000-4000-8000-000000000002:tun0\n"
    "lo:a1f0c0de-0000-4000-8000-000000000003:lo"
)


def creds_router(active=ACTIVE_CONNECTIONS, ssid="Freebox-CA3555", psk="hunter2",
                 profile_rc=0, key_mgmt=None, hidden=None):
    def router(args):
        if "--active" in args:
            return (0, active, "")
        if args[:2] == ("-s", "-t"):
            body = ""
            if ssid is not None:
                body += f"802-11-wireless.ssid:{ssid}\n"
            if hidden is not None:
                body += f"802-11-wireless.hidden:{hidden}\n"
            if key_mgmt is not None:
                body += f"802-11-wireless-security.key-mgmt:{key_mgmt}\n"
            if psk is not None:
                body += f"802-11-wireless-security.psk:{psk}"
            return (profile_rc, body, "" if profile_rc == 0 else "not found")
        return (0, "", "")
    return router


@pytest.mark.asyncio
async def test_the_profile_shared_is_read_by_the_uuid_found_on_wlan0(service):
    """Not by the SSID or the name, which a connect being tried shares with
    the profile it replaces."""
    fake, patcher = with_nmcli(creds_router())
    with patcher:
        await service.shareable_wifi()
    assert fake.calls[1][-2:] == ("uuid", FREEBOX_UUID)


@pytest.mark.asyncio
async def test_an_open_home_network_is_shared_with_an_empty_key_not_none(service):
    """The speaker and the satellite write a key-less profile for an empty
    key; None is what a refusal carries, and the adoption would refuse an
    open network it can share."""
    fake, patcher = with_nmcli(creds_router(psk=None))
    with patcher:
        wifi = await service.shareable_wifi()
    assert (wifi.refusal, wifi.password) == (None, "")


@pytest.mark.parametrize("psk", ["", None])
@pytest.mark.asyncio
async def test_a_wpa_psk_profile_whose_key_reads_back_empty_is_unreadable(service, psk):
    """A WPA network has a key: read back empty (rc 0), it is a read that
    failed, not an open network. Handed over, it rebuilt Bureau's standby link
    as an open profile that never associates. Told apart from "no WiFi", so the
    satellite push can read it again rather than wait for the next admission."""
    fake, patcher = with_nmcli(creds_router(key_mgmt="wpa-psk", hidden="no", psk=psk))
    with patcher:
        wifi = await service.shareable_wifi()
    assert (wifi.refusal, wifi.password) == (WifiShareRefusal.UNREADABLE, None)
    assert wifi.profile == "milo-Freebox-CA3555"


@pytest.mark.asyncio
async def test_a_hidden_network_whose_key_reads_back_empty_is_still_named_hidden(service):
    """Read as None, the adoption would have nothing to refuse and push the
    hidden SSID to a speaker that can never join it."""
    fake, patcher = with_nmcli(creds_router(key_mgmt="wpa-psk", hidden="yes", psk=""))
    with patcher:
        wifi = await service.shareable_wifi()
    assert wifi.refusal is WifiShareRefusal.HIDDEN


@pytest.mark.asyncio
async def test_a_passphrase_containing_a_colon_survives_the_terse_format(service):
    """nmcli's terse mode separates on ':' and escapes literal ones. Reading the
    raw split would truncate the password at the first colon, and the speaker
    would fail to join with no error anyone can see from here."""
    fake, patcher = with_nmcli(creds_router(psk=r"pa\:ss\:word"))
    with patcher:
        wifi = await service.shareable_wifi()
    assert wifi.password == "pa:ss:word"


@pytest.mark.asyncio
async def test_a_profile_that_cannot_be_read_is_reported_not_guessed(service, caplog):
    """A refused secret read (no polkit, deleted profile) must not become an
    empty password the user then watches fail on the speaker. The caller
    reports the refusal; the read itself says, at debug, which profile failed."""
    fake, patcher = with_nmcli(creds_router(profile_rc=1))
    with patcher, caplog.at_level(logging.DEBUG, logger="backend.core.network.service"):
        wifi = await service.shareable_wifi()
    assert (wifi.refusal, wifi.password) == (WifiShareRefusal.UNREADABLE, None)
    assert "milo-Freebox-CA3555" in caplog.text


@pytest.mark.asyncio
async def test_a_profile_without_an_ssid_field_shares_nothing(service):
    """802-11-wireless.ssid is absent on a profile NM stores by BSSID."""
    fake, patcher = with_nmcli(creds_router(ssid=None))
    with patcher:
        wifi = await service.shareable_wifi()
    assert wifi.refusal is not None and wifi.password is None


@pytest.mark.asyncio
async def test_a_failed_active_connection_list_is_unreadable_not_no_network(service):
    """First read of the pair; without the exit-code check an nmcli that died
    reads as "no wifi", which is the same answer as an ethernet-only server."""
    fake, patcher = with_nmcli(lambda args: (1, "", "NetworkManager not running"))
    with patcher:
        wifi = await service.shareable_wifi()
    assert wifi.refusal is WifiShareRefusal.UNREADABLE


# --------------------------------------------------------------------------- #
# The one reader of the network this server can hand over
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_the_network_shared_is_the_one_wlan0_is_on(service):
    """The satellites' standby link and the speaker adoption both hand a peer
    this SSID and key, and the peer rebuilds the network from them."""
    fake, patcher = with_nmcli(creds_router(hidden="no", key_mgmt="wpa-psk"))
    with patcher:
        wifi = await service.shareable_wifi()
    assert wifi == ServerWifi(
        ssid="Freebox-CA3555", password="hunter2", refusal=None,
        profile="milo-Freebox-CA3555", key_mgmt="wpa-psk",
    )


@pytest.mark.parametrize("active", [
    f"{HOTSPOT_NAME}:{FREEBOX_UUID}:wlan0",
    f"{ADOPTION_PROFILE}:{FREEBOX_UUID}:wlan0",
    "Wired connection 1:u-0:eth0",
], ids=["recovery-ap", "adoption", "ethernet-only"])
@pytest.mark.asyncio
async def test_a_unit_off_its_network_has_none_to_share(service, active):
    """Only the profile wlan0 carries now is read, never a saved one: on the AP
    — where a client is anyone in range — there is no key to give."""
    fake, patcher = with_nmcli(creds_router(active=active))
    with patcher:
        wifi = await service.shareable_wifi()
    assert wifi.refusal is WifiShareRefusal.NO_NETWORK and wifi.password is None
    assert not [c for c in fake.calls if c[:2] == ("-s", "-t")]


@pytest.mark.parametrize("hidden, key_mgmt, refusal", [
    ("yes", "wpa-psk", WifiShareRefusal.HIDDEN),
    ("no", "sae", WifiShareRefusal.UNSUPPORTED_SECURITY),
    ("no", "wpa-eap", WifiShareRefusal.UNSUPPORTED_SECURITY),
])
@pytest.mark.asyncio
async def test_a_network_its_name_and_key_cannot_rebuild_is_refused_by_name(service, hidden, key_mgmt, refusal):
    """The adoption names the refusal to the user and the satellite push logs
    it once per network, so the reason and the network travel with it — the
    key never does."""
    fake, patcher = with_nmcli(creds_router(hidden=hidden, key_mgmt=key_mgmt))
    with patcher:
        wifi = await service.shareable_wifi()
    assert (wifi.refusal, wifi.ssid, wifi.key_mgmt, wifi.password) == (refusal, "Freebox-CA3555", key_mgmt, None)


def _key_reads(*psks):
    """creds_router whose profile read answers each key in turn, the last one
    repeating; an exception is raised by that read instead."""
    answers = list(psks)

    def router(args):
        if args[:2] != ("-s", "-t"):
            return creds_router()(args)
        psk = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(psk, BaseException):
            async def raises():
                raise psk
            return raises()
        return creds_router(hidden="no", key_mgmt="wpa-psk", psk=psk)(args)
    return router


def _waits(sleep):
    """The waits a patched `asyncio.sleep` saw, the fake nmcli's own yields left out."""
    return [c.args[0] for c in sleep.await_args_list if c.args != (0,)]


@pytest.mark.asyncio
async def test_a_key_read_back_empty_is_read_again_within_the_tries_given(service):
    """Measured during a reboot: polkit stopped, NM answered an empty key with
    rc 0. Given up at once, a satellite kept its stale standby key until the
    next admission."""
    fake, patcher = with_nmcli(_key_reads("", "hunter2"))
    with patcher, patch("backend.core.network.service.asyncio.sleep", new=AsyncMock()) as sleep:
        wifi = await service.shareable_wifi(tries=3, retry_delay_s=7)
    assert wifi.password == "hunter2"
    assert _waits(sleep) == [7]


@pytest.mark.parametrize("failure", ["empty-key", "nmcli-failed", "nmcli-timeout"])
@pytest.mark.asyncio
async def test_a_profile_that_never_reads_is_unreadable_not_absent(service, failure):
    """"No network" and "could not read it" are different answers to the user
    adopting a speaker, and an empty key handed over rebuilds a WPA network as
    an open one. One read only when one try is asked for: the adoption reads
    inside its own deadline."""
    if failure == "empty-key":
        router = creds_router(hidden="no", key_mgmt="wpa-psk", psk="")
    elif failure == "nmcli-failed":
        router = creds_router(profile_rc=8)
    else:
        router = _key_reads(asyncio.TimeoutError())
    fake, patcher = with_nmcli(router)
    with patcher, patch("backend.core.network.service.asyncio.sleep", new=AsyncMock()) as sleep:
        wifi = await service.shareable_wifi()
    assert wifi.refusal is WifiShareRefusal.UNREADABLE and wifi.password is None
    assert _waits(sleep) == []
    if failure == "empty-key":
        assert (wifi.ssid, wifi.profile) == ("Freebox-CA3555", "milo-Freebox-CA3555")


@pytest.mark.asyncio
async def test_a_stalled_read_is_bounded_by_the_budget_given(service):
    """The adoption reads under its lock, inside a deadline: a read that hangs
    must come back unreadable within the caller's bound, not nmcli's default."""
    def router(args):
        if args[:2] == ("-s", "-t"):
            return asyncio.get_running_loop().create_future()
        return creds_router()(args)

    fake, patcher = with_nmcli(router)
    with patcher:
        wifi = await asyncio.wait_for(service.shareable_wifi(timeout_s=0.05), timeout=1.0)
    assert wifi.refusal is WifiShareRefusal.UNREADABLE


# --------------------------------------------------------------------------- #
# Connecting, forgetting, listing
# --------------------------------------------------------------------------- #

HOME = ("milo-Maison", "u-home", "Maison", "secret")


@pytest.mark.asyncio
async def test_a_key_that_joins_becomes_the_networks_one_profile_on_disk(service):
    """The key the user typed replaces the SSID's other profiles — Imager's
    `preconfigured` included — once it joined, and only then. It is on disk and
    autoconnects: a profile left in memory would be gone at the next reboot.
    Another network's profile is not the SSID's and stays."""
    nm = FakeStations(
        [HOME, ("preconfigured", "u-imager", "Maison", "old"), ("milo-Voisins", "u-voisins", "Voisins", "k")],
        active="u-home", in_range={"Maison": "n3w-key"},
    )
    fake, patcher = with_nmcli(nm.route)
    with patcher:
        status = await service.connect("Maison", "n3w-key")

    [maison] = [p for p in nm.profiles.values() if p["ssid"] == "Maison"]
    assert (maison["psk"], maison["on_disk"], maison["autoconnect"]) == ("n3w-key", True, True)
    assert nm.profiles[nm.active] is maison
    assert "u-voisins" in nm.profiles
    # The wizard advances on this payload, read from what NM now carries.
    assert status.wifi.connected is True


@pytest.mark.asyncio
async def test_a_key_that_does_not_join_leaves_the_working_profile_and_brings_it_back(service):
    """A mistyped key used to delete the home network's profile before the try:
    the unit fell off its network for good, onto an AP a client set up may only
    read. The profile tried is gone with its failure, the old one is untouched,
    and it is asked for again at once rather than left to an autoconnect that
    a `device disconnect` had blocked."""
    nm = FakeStations([HOME], active="u-home", in_range={"Maison": "secret"})
    fake, patcher = with_nmcli(nm.route)
    with patcher, pytest.raises(RuntimeError, match="Secrets were required"):
        await service.connect("Maison", "typo")

    assert set(nm.profiles) == {"u-home"}
    assert nm.profiles["u-home"]["psk"] == "secret"
    assert nm.active == "u-home"


@pytest.mark.asyncio
async def test_a_connect_from_the_access_point_that_fails_raises_the_access_point_again(service):
    """One radio: the AP goes down for the try. If the try fails, the AP comes
    back at once — not after the settle window plus an autoconnect wait, a
    minute and more during which the person who typed the key has no way in.
    Nothing saved before the try is lost either."""
    nm = FakeStations([HOME], hotspot=True, in_range={})
    service._hotspot_active = True
    fake, patcher = with_nmcli(nm.route)
    with patcher, pytest.raises(RuntimeError, match="could not be found"):
        await service.connect("Maison", "secret")
    await service.cleanup()

    assert nm.hotspot is True and service.hotspot_active is True
    assert set(nm.profiles) == {"u-home"}


@pytest.mark.asyncio
async def test_a_failed_try_raises_no_access_point_where_the_unit_is_reachable_or_held(service):
    """The fall-back takes `_retry_station`'s conditions: a cable back meanwhile
    is a way in already, and an adoption holding the radio looks again itself
    once it lets go."""
    nm = FakeStations(hotspot=True, in_range={}, ethernet=True)
    service._hotspot_active = True
    fake, patcher = with_nmcli(nm.route)
    with patcher, pytest.raises(RuntimeError):
        await service.connect("Maison", "secret")

    assert nm.hotspot is False and not nm.hotspot_profile

    nm = FakeStations(hotspot=True, in_range={})
    service._hotspot_active = True
    fake, patcher = with_nmcli(nm.route)
    with patcher, pytest.raises(RuntimeError):
        async with service.recovery_held():
            await service.connect("Maison", "secret")
        await service.cleanup()

    assert not fake.argv_containing("ap")


@pytest.mark.asyncio
async def test_a_profile_nm_kept_in_memory_replaces_nothing(service):
    """The SSID's other profiles are deleted only once the new one is on disk:
    with NM keeping it in /run, deleting them would leave the unit with no
    profile for its network after the next reboot."""
    nm = FakeStations([HOME], active="u-home", in_range={"Maison": "n3w-key"}, persists=False)
    fake, patcher = with_nmcli(nm.route)
    with patcher, caplog_at_error() as records:
        await service.connect("Maison", "n3w-key")

    assert "u-home" in nm.profiles
    assert [r for r in records if "could not be written to disk" in r.getMessage()]


@pytest.mark.asyncio
async def test_a_connect_on_a_unit_with_no_hotspot_deletes_no_hotspot_profile(service):
    nm = FakeStations(in_range={"Maison": "secret"})
    fake, patcher = with_nmcli(nm.route)
    with patcher:
        await service.connect("Maison", "secret")
    assert ("connection", "delete", HOTSPOT_NAME) not in fake.calls


@pytest.mark.asyncio
async def test_a_connection_that_never_comes_up_is_raised_as_a_timeout(service):
    """`connection up` is bounded at 30 s. Without the arm the wizard's spinner
    sits on an `asyncio.TimeoutError` traceback instead of a message that names
    the network — and NM finishes an activation whose nmcli gave up, so the
    profile tried is deleted, which is what ends it."""
    async def stalls():
        raise asyncio.TimeoutError()

    nm = FakeStations([HOME], active="u-home", in_range={"Maison": "secret"})

    def route(args):
        if args[:3] == ("connection", "up", "uuid") and args[3] != "u-home":
            return stalls()
        return nm.route(args)

    fake, patcher = with_nmcli(route)
    with patcher, pytest.raises(RuntimeError, match="'Maison' timed out"):
        await service.connect("Maison", "secret")

    assert set(nm.profiles) == {"u-home"}
    assert nm.active == "u-home"


@pytest.mark.asyncio
async def test_a_refused_activation_names_the_network_that_refused(service):
    """Wrong password is the common case, and nmcli's stderr is the only text
    that says so; the wizard shows this string."""
    nm = FakeStations(in_range={"Maison": "secret"})
    fake, patcher = with_nmcli(nm.route)
    with patcher, pytest.raises(RuntimeError, match="Secrets were required"):
        await service.connect("Maison", "wrong")
    assert nm.profiles == {}


@pytest.mark.asyncio
async def test_the_saved_list_names_each_network_once_by_the_ssid_it_carries(service):
    """The settings panel lists these, and hands one back to `forget_network`,
    which matches the SSID a profile carries: `milo-Freebox` is a NM profile
    name, `preconfigured` names no network at all, and two profiles of one SSID
    are one network. Neither the unit's own AP nor the adoption's profile is a
    network the user saved."""
    nm = FakeStations([
        ("milo-Freebox-CA3555", "u-1", "Freebox-CA3555", "k"),
        ("preconfigured", "u-2", "Voisins", "k"),
        ("Voisins", "u-3", "Voisins", "k"),
    ], hotspot=True)
    fake, patcher = with_nmcli(nm.route)
    with patcher:
        saved = await service.get_saved_networks()

    assert [n.ssid for n in saved] == ["Freebox-CA3555", "Voisins"]


@pytest.mark.asyncio
async def test_a_saved_list_that_cannot_be_read_is_raised_not_emptied(service):
    """An empty list reads as "no saved networks" and the panel offers to set
    one up — over a unit that has one."""
    fake, patcher = with_nmcli(lambda args: (1, "", "NetworkManager not running"))
    with patcher, pytest.raises(RuntimeError, match="Failed to list WiFi profiles"):
        await service.get_saved_networks()


@pytest.mark.asyncio
async def test_turning_the_radio_off_is_reported_when_it_refuses(service):
    """`PUT /api/network/wifi/radio` is idempotent from the UI's point of view;
    a silent failure leaves the toggle showing off with the radio on."""
    fake, patcher = with_nmcli(lambda args: (1, "", "not authorized"))
    with patcher, pytest.raises(RuntimeError, match="not authorized"):
        await service.set_wifi_enabled(False)


@pytest.mark.asyncio
async def test_the_radio_verb_follows_the_requested_state(service):
    fake, patcher = with_nmcli()
    with patcher:
        await service.set_wifi_enabled(True)
        await service.set_wifi_enabled(False)
    assert fake.calls == [("radio", "wifi", "on"), ("radio", "wifi", "off")]


# --------------------------------------------------------------------------- #
# The WiFi regulatory domain — a privileged helper, so argv IS the contract
# --------------------------------------------------------------------------- #

class FakeSudoSpawn:
    """Stands in for the one privileged helper this service invokes."""

    def __init__(self, rc=0, stdout="", stderr="", hang=False):
        self.argv = None
        self.proc = FakeProc(rc, stdout, stderr)
        if hang:
            # Raised rather than slept: `wait_for`'s own bound is the production
            # behaviour under test, and a real 10 s sleep would put that wall
            # clock into the suite. Sleeping past a patched `asyncio.wait_for`
            # is the other option and it is worse — `service.py` imports asyncio
            # bare, so patching through it is global (B6 lesson).
            async def times_out(input=None):
                raise asyncio.TimeoutError()
            self.proc.communicate = times_out

    async def __call__(self, program, *args, **kwargs):
        self.argv = (program, *args)
        return self.proc


def sudo_spawn(**kw):
    fake = FakeSudoSpawn(**kw)
    return fake, patch(
        "backend.core.network.service.asyncio.create_subprocess_exec", new=fake
    )


@pytest.mark.asyncio
async def test_the_country_helper_is_called_by_the_exact_path_sudoers_grants(service):
    """`/etc/sudoers.d/milo-backend` grants
    `milo ALL=(root) NOPASSWD: /usr/local/bin/milo-set-wifi-country` by absolute
    path. A relative name, a symlink or a different directory is not the same
    rule: sudo would prompt for a password nobody is at the console to type, and
    the regulatory domain would silently stay wrong — which caps WiFi transmit
    power on every unit shipped outside the default region."""
    fake, patcher = sudo_spawn(stdout="regdom set to FR")
    with patcher:
        await service.set_country("FR")

    assert fake.argv == ("sudo", "-n", "/usr/local/bin/milo-set-wifi-country", "FR")


@pytest.mark.asyncio
async def test_the_country_is_only_persisted_after_the_helper_took(service):
    """`SettingsService` is what `GET /api/settings/bulk` reads back. Persisting
    before the helper runs makes the panel report a domain the radio is not on,
    and the mismatch survives a reboot because nothing re-applies it."""
    fake, patcher = sudo_spawn(rc=1, stderr="invalid country code")
    with patcher, pytest.raises(RuntimeError, match="invalid country code"):
        await service.set_country("ZZ")

    service.settings_service.set_setting.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_successful_country_change_is_persisted_under_its_settings_key(service):
    fake, patcher = sudo_spawn(stdout="ok")
    with patcher:
        await service.set_country("FR")
    service.settings_service.set_setting.assert_awaited_once_with("wifi.country", "FR")


@pytest.mark.asyncio
async def test_a_helper_that_hangs_is_killed_and_reported(service):
    """It rewrites `/boot/firmware/cmdline.txt`; a run that never returns would
    hold the request open with no ceiling and leave the file half-written with
    nothing said about it."""
    fake, patcher = sudo_spawn(hang=True)
    with patcher, pytest.raises(RuntimeError, match="timed out"):
        await service.set_country("FR")
    assert fake.proc.killed is True


@pytest.mark.asyncio
async def test_a_helper_that_fails_without_stderr_still_says_something(service):
    """A script killed by a signal prints nothing; "Unknown error" is what the
    settings panel shows instead of an empty banner."""
    fake, patcher = sudo_spawn(rc=2)
    with patcher, pytest.raises(RuntimeError, match="Unknown error"):
        await service.set_country("FR")


@pytest.mark.asyncio
async def test_set_country_calls_are_serialized(service):
    """The setup wizard's network step sends one country on mount, unawaited,
    while its picker sends another. Two helpers at once each rewrite
    `/boot/firmware/cmdline.txt`, and the setting could be persisted in the
    other order: the second call runs only once the first has persisted."""
    events = []
    release = asyncio.Event()

    async def spawn(program, *args, **kwargs):
        events.append(("spawn", args[-1]))

        async def answer():
            await release.wait()
            return 0, "ok", ""
        return FakeProc(0, "", "", answer=answer())

    service.settings_service.set_setting.side_effect = (
        lambda key, value: events.append(("set_setting", value))
    )
    with patch("backend.core.network.service.asyncio.create_subprocess_exec", new=spawn):
        calls = asyncio.gather(service.set_country("FR"), service.set_country("BE"))
        for _ in range(10):
            await asyncio.sleep(0)
        assert events == [("spawn", "FR")]
        release.set()
        await calls

    assert events == [
        ("spawn", "FR"), ("set_setting", "FR"), ("spawn", "BE"), ("set_setting", "BE"),
    ]


@pytest.mark.asyncio
async def test_the_stored_country_is_read_back_from_settings(service):
    service.settings_service.get_setting = AsyncMock(return_value="FR")
    assert await service.get_country() == "FR"
    service.settings_service.get_setting.assert_awaited_once_with("wifi.country")


# --------------------------------------------------------------------------- #
# The NetworkManager D-Bus tiers
# --------------------------------------------------------------------------- #
#
# The header above used to say these were deliberately left bare because "a mock
# of dbus-next would assert the mock". What is asserted below is not dbus-next:
# it is the four wiring decisions the service makes on top of it, each of which
# fails silently and each of which has an observable at this boundary —
#   * WHICH property changes are worth a status re-read (NM emits many),
#   * that the IP4Config object is subscribed to separately (NM updates
#     `Addresses` in place on a DHCP lease without re-emitting Device.Ip4Config),
#   * that a listener is detached before its replacement is attached,
#   * that a status identical to the last one is not broadcast again.
# Same reasoning as `test_bt_remote.py`'s BlueZ bridge: the boundary stands for
# the outside world, and every assertion is about what the unit did to it.

NM_ROOT = "/org/freedesktop/NetworkManager"
ETH_PATH = "/org/freedesktop/NetworkManager/Devices/1"
WLAN_PATH = "/org/freedesktop/NetworkManager/Devices/2"
IP4_ETH = "/org/freedesktop/NetworkManager/IP4Config/1"
IP4_WLAN = "/org/freedesktop/NetworkManager/IP4Config/2"
AP_PATH = "/org/freedesktop/NetworkManager/AccessPoint/7"

from backend.core.network.service import (            # noqa: E402 — grouped with its block
    DBUS_PROPERTIES_IFACE, NM_ACCESS_POINT_IFACE, NM_DEVICE_IFACE,
    NM_DEVICE_TYPE_ETHERNET, NM_DEVICE_TYPE_WIFI, NM_DEVICE_WIRED_IFACE,
    NM_DEVICE_WIRELESS_IFACE, NM_IP4_CONFIG_IFACE,
)


class FakeProperties:
    """The org.freedesktop.DBus.Properties interface of one object."""

    def __init__(self, path):
        self.path = path
        self.handlers = []

    def on_properties_changed(self, handler):
        self.handlers.append(handler)

    def off_properties_changed(self, handler):
        self.handlers.remove(handler)

    def emit(self, iface, changed):
        for handler in list(self.handlers):
            handler(iface, changed, [])


class FakeObject:
    def __init__(self, path, interfaces):
        self.props = FakeProperties(path)
        self._ifaces = dict(interfaces)
        self._ifaces[DBUS_PROPERTIES_IFACE] = self.props

    def get_interface(self, name):
        if name not in self._ifaces:
            raise KeyError(name)
        return self._ifaces[name]


class FakeNM:
    """The NetworkManager object tree, as far as this service walks it."""

    def __init__(self, *, devices=None, ip4=None, ap_path=AP_PATH,
                 ssid=b"Freebox-CA3555", strength=71,
                 bitrate_kbps=390000, wired_speed=1000, carrier=True):
        self.devices = devices if devices is not None else {
            "eth0": ETH_PATH, "wlan0": WLAN_PATH,
        }
        self.ip4 = ip4 if ip4 is not None else {ETH_PATH: IP4_ETH, WLAN_PATH: IP4_WLAN}
        self.ap_path = ap_path
        self.disconnected = False
        # Flipped by a test to model NM destroying the objects under us — a
        # roam, `nmcli device delete`, or NetworkManager itself restarting.
        self.dead = False
        self.introspects: list[str] = []
        self.objects = {}

        async def get_device(name):
            if name not in self.devices:
                raise Exception(f"no device {name}")
            return self.devices[name]

        self.objects[NM_ROOT] = FakeObject(
            NM_ROOT, {"org.freedesktop.NetworkManager":
                      types.SimpleNamespace(call_get_device_by_ip_iface=get_device)}
        )
        for iface_name, path in self.devices.items():
            wireless = iface_name == "wlan0"
            ifaces = {NM_DEVICE_IFACE: types.SimpleNamespace(
                get_ip4_config=self._ip4_getter(path),
                get_device_type=self._const(NM_DEVICE_TYPE_WIFI if wireless
                                            else NM_DEVICE_TYPE_ETHERNET))}
            if wireless:
                ifaces[NM_DEVICE_WIRELESS_IFACE] = types.SimpleNamespace(
                    get_active_access_point=self._ap_getter(),
                    get_bitrate=self._const(bitrate_kbps))
            else:
                ifaces[NM_DEVICE_WIRED_IFACE] = types.SimpleNamespace(
                    get_speed=self._const(wired_speed), get_carrier=self._const(carrier))
            self.objects[path] = FakeObject(path, ifaces)
        for path in set(self.ip4.values()):
            self.objects[path] = FakeObject(path, {})
        self.objects[AP_PATH] = FakeObject(AP_PATH, {
            NM_ACCESS_POINT_IFACE: types.SimpleNamespace(
                get_ssid=self._const(ssid), get_strength=self._const(strength),
                # What `nmcli dev wifi` prints as RATE: the AP's ceiling, measured
                # at 270000 while the link itself had negotiated 390000.
                get_max_bitrate=self._const(270000))
        })

    def _ip4_getter(self, device_path):
        async def get():
            self._check()
            return self.ip4[device_path]
        return get

    def _ap_getter(self):
        async def get():
            self._check()
            return self.ap_path
        return get

    def _check(self):
        if self.dead:
            raise Exception("org.freedesktop.DBus.Error.UnknownObject")

    @staticmethod
    def _const(value):
        async def get():
            return value
        return get

    # --- the MessageBus surface --------------------------------------------
    async def connect(self):
        return self

    async def introspect(self, service, path):
        self.introspects.append(path)
        if path not in self.objects:
            raise Exception(f"unknown object {path}")
        return path

    def get_proxy_object(self, service, path, introspection):
        return self.objects[path]

    def disconnect(self):
        self.disconnected = True

    def props(self, path):
        return self.objects[path].props


class _RaisingProxy:
    """A proxy whose object NM has destroyed: dbus-next raises on the interface."""

    def get_interface(self, name):
        raise Exception("org.freedesktop.DBus.Error.UnknownObject")


def with_nm(nm):
    return patch("backend.core.network.service.MessageBus", return_value=nm)


@contextlib.contextmanager
def caplog_at_error():
    """Collect ERROR records emitted by this service, and nothing else."""
    records = []
    handler = logging.Handler(level=logging.ERROR)
    handler.emit = records.append
    logger = logging.getLogger("backend.core.network.service")
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


async def settle():
    """Drain the fire-and-forget refresh tasks the handlers spawn.

    Bounded, and by task completion rather than by a count of loop turns: a
    handler that spawns a refresh which itself spawns one would otherwise be
    counted half-done, and the assertion would depend on how many `sleep(0)`
    happened to be enough.
    """
    for _ in range(20):
        others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if not others:
            return
        await asyncio.wait(others, timeout=2)


@pytest.fixture
def wired(service):
    """A service whose nmcli reads all answer, so only D-Bus is under test."""
    def route(args):
        if args[:2] == ("radio", "wifi"):
            return (0, "enabled", "")
        return (0, "GENERAL.CONNECTION:milo-Freebox-CA3555\n"
                   "IP4.ADDRESS[1]:192.168.1.39/24", "")
    return route


@pytest.mark.asyncio
async def test_initialize_subscribes_both_devices_and_primes_the_broadcast(service, wired):
    nm = FakeNM()
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        assert await service.initialize() is True

    assert set(service._device_listeners) == {"eth0", "wlan0"}
    assert nm.props(ETH_PATH).handlers and nm.props(WLAN_PATH).handlers
    assert nm.props(IP4_ETH).handlers and nm.props(IP4_WLAN).handlers
    service.state_machine.broadcast.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_board_without_wlan0_still_initializes(service, wired):
    """Not every unit has a WiFi card. A missing interface must skip its tier,
    not fail the whole subscription and take eth0's live updates with it."""
    nm = FakeNM(devices={"eth0": ETH_PATH}, ip4={ETH_PATH: IP4_ETH})
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        assert await service.initialize() is True

    assert list(service._device_listeners) == ["eth0"]
    assert service._wireless_listener is None


@pytest.mark.asyncio
async def test_a_bus_that_never_comes_up_fails_open(service, wired):
    """A dev host with no system bus at all must still serve the nmcli read
    path; the D-Bus tier simply does not exist there."""
    async def refuse():
        raise OSError("no system bus")

    fake, patcher = with_nmcli(wired)
    with patch("backend.core.network.service.MessageBus",
               return_value=types.SimpleNamespace(connect=refuse)), patcher:
        assert await service.initialize() is False

    assert service._bus is None


@pytest.mark.asyncio
async def test_a_subscription_that_fails_halfway_leaves_no_handler_behind(service, wired):
    """Fail open, and fail *clean*. eth0 subscribes before wlan0, so a failure
    on the second tier leaves the first attached to a bus `initialize` is about
    to forget: the handlers keep firing against a service that believes it has
    no D-Bus, and nothing can ever detach them because `_bus` is None."""
    nm = FakeNM()
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher, \
            patch.object(NetworkService, "_subscribe_wireless",
                         side_effect=RuntimeError("NM went away")):
        assert await service.initialize() is False

    assert nm.props(ETH_PATH).handlers == []
    assert service._device_listeners == {}
    assert nm.disconnected is True


@pytest.mark.asyncio
async def test_cleanup_detaches_every_handler_and_drops_the_bus(service, wired):
    nm = FakeNM()
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        await service.cleanup()

    assert nm.props(ETH_PATH).handlers == []
    assert nm.props(WLAN_PATH).handlers == []
    assert nm.props(IP4_ETH).handlers == []
    assert nm.disconnected is True
    assert service._bus is None


@pytest.mark.asyncio
async def test_a_wifi_link_reads_the_negotiated_rate_not_the_access_points_ceiling(service, wired):
    """The multiroom analysis weighs the server's link by this rate. `nmcli dev
    wifi` RATE is the AP's MaxBitrate — a constant of the access point, not of
    this link — and its implicit rescan blocks the radio for seconds, in the
    middle of the ping window the analysis is timing."""
    nm = FakeNM(bitrate_kbps=390000, strength=64)
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        await settle()
        before = len(fake.calls)
        link = await service.read_link("wlan0")

    assert (link.kind, link.speed_mbps, link.signal_percent) == ("wifi", 390.0, 64)
    assert fake.calls[before:] == []


@pytest.mark.asyncio
async def test_a_wired_link_reads_its_negotiated_speed(service, wired):
    nm = FakeNM(wired_speed=100)
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        await settle()
        link = await service.read_link("eth0")

    assert (link.kind, link.speed_mbps, link.signal_percent) == ("ethernet", 100.0, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("nm_kwargs, iface", [
    ({"carrier": False}, "eth0"),
    ({"bitrate_kbps": 0}, "wlan0"),
])
async def test_a_link_with_no_rate_reports_none_rather_than_zero(service, wired, nm_kwargs, iface):
    """A zero would be read as a link with no room at all; unknown is what the
    caller can declare as assumed."""
    nm = FakeNM(**nm_kwargs)
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        await settle()
        link = await service.read_link(iface)

    assert link.speed_mbps is None


@pytest.mark.asyncio
async def test_a_link_nothing_can_read_is_unknown(service, wired):
    """No bus (a dev host), or an interface NM does not manage: fail open."""
    assert await service.read_link("eth0") is None

    nm = FakeNM()
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        await settle()
        assert await service.read_link("unmanaged0") is None


@pytest.mark.asyncio
async def test_cleanup_twice_is_harmless(service, wired):
    nm = FakeNM()
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        await service.cleanup()
        await service.cleanup()


@pytest.mark.asyncio
async def test_a_property_change_on_another_interface_is_ignored(service, wired):
    """The Properties signal carries every interface of the object. Re-reading
    the status on each one costs four nmcli forks, and NM emits them in bursts."""
    nm = FakeNM()
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        await settle()
        before = len(fake.calls)
        # `State` is a watched name, so the interface check is the ONLY thing
        # that can stop this one — and the broadcast is not the observable,
        # because an identical status is deduped anyway.
        nm.props(ETH_PATH).emit("org.freedesktop.NetworkManager.Device.Statistics",
                                {"State": 100})
        await settle()

    assert len(fake.calls) == before


@pytest.mark.asyncio
async def test_a_property_outside_the_watched_set_is_ignored(service, wired):
    """`Ip4Connectivity`, `Metered` and friends change constantly on a live NM
    and none of them moves anything on screen."""
    nm = FakeNM()
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        await settle()
        before = len(fake.calls)
        nm.props(ETH_PATH).emit(NM_DEVICE_IFACE, {"Ip4Connectivity": 4})
        await settle()

    assert len(fake.calls) == before


@pytest.mark.asyncio
async def test_a_link_state_change_re_reads_the_status(service, wired):
    """Unplugging the cable is a `State` change, and it is the whole point of
    the subscription: the badge must move without waiting for a poll."""
    nm = FakeNM()
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        await settle()
        before = len(fake.calls)
        nm.props(ETH_PATH).emit(NM_DEVICE_IFACE, {"State": 30})
        await settle()

    assert len(fake.calls) > before


@pytest.mark.asyncio
async def test_a_dhcp_lease_reaches_the_ui_through_the_ip4_object(service, wired):
    """NM rewrites `IP4Config.AddressData` in place when the lease completes and
    does NOT re-emit Device.Ip4Config. Without this second subscription the
    address appears only on the next unrelated event — the badge sits on
    "connecting" through the whole DHCP window."""
    nm = FakeNM()
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        await settle()
        before = len(fake.calls)
        nm.props(IP4_ETH).emit(NM_IP4_CONFIG_IFACE, {"AddressData": [{"address": "192.168.1.55"}]})
        await settle()

    assert len(fake.calls) > before


@pytest.mark.asyncio
async def test_an_unrelated_ip4_property_does_not_re_read(service, wired):
    nm = FakeNM()
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        await settle()
        before = len(fake.calls)
        nm.props(IP4_ETH).emit(NM_IP4_CONFIG_IFACE, {"Gateway": "192.168.1.1"})
        nm.props(IP4_ETH).emit("org.freedesktop.NetworkManager.Device", {"AddressData": []})
        await settle()

    assert len(fake.calls) == before


@pytest.mark.asyncio
async def test_a_new_ip4_object_replaces_the_old_listener(service, wired):
    """Every DHCP renewal that changes the lease gives NM a fresh IP4Config
    object. Attaching without detaching leaves a handler on a dead object and
    doubles the refresh work on every renewal for the life of the unit."""
    nm = FakeNM()
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        old_props = nm.props(IP4_ETH)
        new_path = "/org/freedesktop/NetworkManager/IP4Config/9"
        nm.objects[new_path] = FakeObject(new_path, {})
        nm.ip4[ETH_PATH] = new_path
        nm.props(ETH_PATH).emit(NM_DEVICE_IFACE, {"Ip4Config": new_path})
        await settle()

    assert old_props.handlers == []
    assert nm.objects[new_path].props.handlers


@pytest.mark.asyncio
async def test_an_unchanged_ip4_path_is_not_re_anchored(service, wired):
    """`Ip4Config` is re-emitted with the same path on events that changed
    nothing; tearing the listener down and back up on each one is a window in
    which a lease update is missed."""
    nm = FakeNM()
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        handlers = list(nm.props(IP4_ETH).handlers)
        nm.props(ETH_PATH).emit(NM_DEVICE_IFACE, {"Ip4Config": IP4_ETH})
        await settle()

    assert nm.props(IP4_ETH).handlers == handlers


@pytest.mark.asyncio
async def test_the_null_object_path_anchors_no_listener(service, wired):
    """NM spells "no IP4 config" as the object path `/`, which is a real path
    that introspects to nothing. Subscribing to it raises inside the handler."""
    nm = FakeNM(ip4={ETH_PATH: "/", WLAN_PATH: IP4_WLAN})
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()

    assert "eth0" not in service._ip4_listener
    assert "wlan0" in service._ip4_listener


@pytest.mark.asyncio
async def test_a_roam_re_anchors_the_access_point_proxy(service, wired):
    """`get_wifi_signal()` reads whatever proxy is anchored. Left on the old AP
    it returns the RSSI of an access point this unit is no longer associated
    with — the arc would sit at the old level for as long as the link lasts."""
    nm = FakeNM()
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        new_ap = "/org/freedesktop/NetworkManager/AccessPoint/9"
        nm.objects[new_ap] = FakeObject(new_ap, {
            NM_ACCESS_POINT_IFACE: types.SimpleNamespace(
                get_ssid=FakeNM._const(b"Freebox-5GHz"), get_strength=FakeNM._const(88))
        })
        nm.ap_path = new_ap
        nm.props(WLAN_PATH).emit(NM_DEVICE_WIRELESS_IFACE, {"ActiveAccessPoint": new_ap})
        await settle()
        assert await service.get_wifi_signal() == 88


@pytest.mark.asyncio
async def test_an_access_point_signal_that_names_the_same_ap_re_anchors_nothing(service, wired):
    """NM re-emits `ActiveAccessPoint` on events that did not change it (a
    re-key, a signal-quality update). Re-introspecting the AP object on each one
    is a D-Bus round trip per event, and it opens a window in which
    `get_wifi_signal()` reads a proxy that is being replaced."""
    nm = FakeNM()
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        await settle()
        before = list(nm.introspects)
        nm.props(WLAN_PATH).emit(NM_DEVICE_WIRELESS_IFACE, {"ActiveAccessPoint": AP_PATH})
        await settle()

    assert nm.introspects == before


@pytest.mark.asyncio
async def test_a_wireless_property_that_is_not_the_access_point_is_ignored(service, wired):
    """`LastScan` ticks every few seconds on a live wlan0."""
    nm = FakeNM()
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        anchored = service._ap_proxy
        nm.ap_path = "/org/freedesktop/NetworkManager/AccessPoint/9"
        nm.props(WLAN_PATH).emit(NM_DEVICE_WIRELESS_IFACE, {"LastScan": 12345})
        await settle()

    assert service._ap_proxy is anchored


@pytest.mark.asyncio
async def test_dissociating_drops_the_access_point_proxy(service, wired):
    """NM spells "not associated" as `/` here too; keeping the old proxy would
    report a signal for a link that is down."""
    nm = FakeNM()
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        nm.ap_path = "/"
        nm.props(WLAN_PATH).emit(NM_DEVICE_WIRELESS_IFACE, {"ActiveAccessPoint": "/"})
        await settle()
        assert await service.get_wifi_signal() is None


@pytest.mark.asyncio
async def test_the_live_ssid_comes_from_the_access_point_not_the_profile_name(service, wired):
    """The profile is named `milo-<ssid>` by this service, so falling back to it
    is only a derivation. The AP's own SSID is what the card is associated with,
    and the two differ after a manual `nmcli` edit or on Imager's profile."""
    def imager_profile(args):
        if args[:2] == ("radio", "wifi"):
            return (0, "enabled", "")
        return (0, "GENERAL.CONNECTION:preconfigured\n"
                   "IP4.ADDRESS[1]:192.168.1.39/24", "")

    nm = FakeNM(ssid=b"Freebox-CA3555")
    fake, patcher = with_nmcli(imager_profile)
    with with_nm(nm), patcher:
        await service.initialize()
        status = await service.get_network_status()

    # The derivation would answer "preconfigured" — a profile name, not
    # a network. Only the AP knows what the card is actually associated with.
    assert status.wifi.ssid == "Freebox-CA3555"
    assert status.wifi.signal == 71


@pytest.mark.asyncio
async def test_without_an_anchored_access_point_the_ssid_is_derived_from_the_profile(service, wired):
    """Fail open: a dev host with no NM D-Bus still shows a network name."""
    fake, patcher = with_nmcli(wired)
    with patcher:
        status = await service.get_network_status()

    assert status.wifi.ssid == "Freebox-CA3555"
    assert status.wifi.signal is None


@pytest.mark.asyncio
async def test_an_identical_status_is_not_broadcast_twice(service, wired):
    """Every NM event lands here. Without the dedup a burst of six property
    changes is six identical WebSocket frames to every client."""
    nm = FakeNM()
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        await settle()
        service.state_machine.broadcast.reset_mock()
        nm.props(ETH_PATH).emit(NM_DEVICE_IFACE, {"State": 100})
        nm.props(ETH_PATH).emit(NM_DEVICE_IFACE, {"State": 100})
        await settle()

    service.state_machine.broadcast.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_status_read_that_fails_broadcasts_nothing(service, wired, caplog):
    """An nmcli that died mid-burst would otherwise broadcast a fabricated
    all-disconnected status and blank the badges on a working unit."""
    nm = FakeNM()
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        await settle()
        service.state_machine.broadcast.reset_mock()
        with patch.object(service, "get_network_status",
                          side_effect=RuntimeError("nmcli died")), \
                caplog.at_level(logging.ERROR):
            nm.props(ETH_PATH).emit(NM_DEVICE_IFACE, {"State": 30})
            await settle()

    service.state_machine.broadcast.assert_not_awaited()
    assert "nmcli died" in caplog.text


# --------------------------------------------------------------------------- #
# Fail-open arms — a dev host, a stopped NM, a board with no radio
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_a_scan_that_returns_nothing_is_an_empty_list_not_a_crash(service):
    """A card that saw no beacon at all: the panel shows "no networks", and the
    empty last field of an empty terse output must not parse as one."""
    fake, patcher = with_nmcli(lambda args: (0, "", ""))
    with patcher:
        assert await service.scan_networks() == []


@pytest.mark.asyncio
async def test_a_scan_row_missing_its_columns_is_skipped(service):
    """nmcli truncates a row when a driver answers late; the fields are read by
    index, so a short row would raise IndexError and lose the whole scan."""
    fake, patcher = with_nmcli(lambda args: (0, "Truncated:42\nMaison:71:WPA2:*", ""))
    with patcher:
        assert [n.ssid for n in await service.scan_networks()] == ["Maison"]


@pytest.mark.asyncio
async def test_a_refused_profile_creation_during_connect_names_the_reason(service):
    """`save_network` has the same arm; this is the *connect* one, and it is the
    only place the wizard learns that the SSID it typed cannot be written."""
    def route(args):
        if args[:2] == ("connection", "add"):
            return (1, "", "802-11-wireless.ssid: property is invalid")
        return (0, "", "")

    fake, patcher = with_nmcli(route)
    with patcher, pytest.raises(RuntimeError, match="property is invalid"):
        await service.connect("\x00bad", "secret")


@pytest.mark.asyncio
async def test_a_hotspot_profile_that_cannot_be_created_is_raised(service):
    """A card that refuses AP mode. `_start_hotspot` swallows it into a log —
    but only because this raises first; a silent success would leave the unit
    claiming to be reachable on an SSID that never appears."""
    def route(args):
        if args[:2] == ("connection", "add"):
            return (1, "", "ap mode not supported")
        return (0, "", "")

    fake, patcher = with_nmcli(route)
    with patcher, pytest.raises(RuntimeError, match="ap mode not supported"):
        await service._activate_hotspot()


@pytest.mark.asyncio
async def test_a_device_status_that_cannot_be_read_is_unknown_not_disconnected(service):
    """nmcli prints the rows it managed to read on stdout AND exits non-zero
    (14th blind spot). Parsing them past the exit code is how a partial answer
    becomes "there is a network" — that half has not moved, and must not.

    What did move is the other half. This used to fold a failed read into
    "no network", which was right while the AP existed for first boot alone:
    a fresh unit whose nmcli failed would otherwise have been unreachable with
    only a reflash to recover it. Under the reachability rule that fear is
    gone — the evaluation re-runs on every NM property change and at every boot
    — while the cost of the old reading appeared: one timed-out nmcli would put
    an open access point on a perfectly connected unit. Unknown is now unknown,
    and neither caller acts on it.
    """
    def route(args):
        if args[:2] == ("-t", "-f") and "status" in args:
            return (1, "ethernet:connected:Wired connection 1",
                    "Error: NetworkManager is not running.")
        return (0, "", "")

    fake, patcher = with_nmcli(route)
    with patcher:
        assert await service._has_active_connection() is None

    with patcher, no_delay():
        await service.evaluate_reachability()
        await asyncio.sleep(0.05)

    assert service.hotspot_active is False
    assert not fake.argv_containing("add")


@pytest.mark.asyncio
async def test_a_short_device_status_row_is_skipped(service):
    """`wifi-p2p:disconnected:` is on every real unit and has an empty third
    column; a row read by index must tolerate it."""
    def route(args):
        if "status" in args:
            return (0, "wifi-p2p\nethernet:connected:Wired connection 1", "")
        return (0, "", "")

    fake, patcher = with_nmcli(route)
    with patcher:
        assert await service._has_active_connection() is True


@pytest.mark.asyncio
async def test_an_unreadable_interface_is_reported_disconnected_not_absent(service):
    """A board with no eth0 at all: `nmcli device show eth0` exits non-zero, and
    the badge must read disconnected rather than raise through the status route
    the frontend polls."""
    fake, patcher = with_nmcli(lambda args: (1, "", "Error: Device 'eth0' not found."))
    with patcher:
        status = await service.get_network_status()
    assert status.ethernet.connected is False
    assert status.wifi.connected is False


@pytest.mark.asyncio
async def test_a_saved_ssid_that_cannot_be_read_is_none(service):
    """It is decoration on the WiFi panel; an exception here would take the
    whole status payload with it."""
    def route(args):
        if args[:2] == ("radio", "wifi"):
            return (0, "enabled", "")
        if "show" in args and "device" in args:
            return (0, "GENERAL.CONNECTION:--\nIP4.ADDRESS[1]:--", "")
        return (1, "", "boom")

    fake, patcher = with_nmcli(route)
    with patcher:
        status = await service.get_network_status()
    assert status.wifi.saved_ssid is None


@pytest.mark.asyncio
async def test_forgetting_a_network_while_nm_is_down_raises_and_deletes_nothing(service):
    """A forget is the one caller with no `connection add` behind it to report
    the failure. A listing that never happened answered as a success lets the
    settings panel drop an SSID that NetworkManager still holds and rejoins."""
    fake, patcher = with_nmcli(lambda args: (1, "", "NetworkManager not running"))
    with patcher, pytest.raises(RuntimeError, match="NetworkManager not running"):
        await service.forget_network("Maison")
    assert not fake.argv_containing("delete")


REFUSED_DELETE = (1, "", "Error: unknown connection")


@pytest.mark.asyncio
async def test_a_profile_that_refuses_to_be_deleted_fails_the_forget(service, caplog):
    """A profile that survives keeps its credentials and its autoconnect, so the
    forget must not read as done: the exception and the operator log both name
    the profile NetworkManager kept."""
    nm = FakeStations([HOME])
    nm.fail("connection", "delete", "uuid", "u-home", answer=REFUSED_DELETE)
    fake, patcher = with_nmcli(nm.route)
    with patcher, caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as raised:
        await service.forget_network("Maison")
    assert "milo-Maison" in str(raised.value)
    assert "milo-Maison" in caplog.text


@pytest.mark.asyncio
async def test_one_refused_delete_does_not_stop_the_others_and_only_survivors_are_named(service):
    """One refusal must not stop the others — a twin left behind still
    autoconnects — and the error names only what survived, which is what the
    operator has to remove by hand."""
    nm = FakeStations([("Maison", "u-bare", "Maison", "k"), HOME])
    nm.fail("connection", "delete", "uuid", "u-bare", answer=REFUSED_DELETE)
    fake, patcher = with_nmcli(nm.route)
    with patcher, pytest.raises(RuntimeError) as raised:
        await service.forget_network("Maison")
    assert set(nm.profiles) == {"u-bare"}
    assert "Maison" in str(raised.value)
    assert "milo-Maison" not in str(raised.value)


def test_a_forget_nm_refused_answers_an_error_on_the_route(service):
    """`useNetwork.js` drops the SSID from the settings panel only on a success,
    so the route is where a kept profile must surface: a 200 shows a network
    forgotten that the unit rejoins at its next autoconnect."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.network import create_network_router

    nm = FakeStations([HOME])
    nm.fail("connection", "delete", "uuid", "u-home", answer=REFUSED_DELETE)
    app = FastAPI()
    app.include_router(create_network_router(service))
    fake, patcher = with_nmcli(nm.route)
    with patcher:
        response = TestClient(app).delete("/api/network/wifi/saved/Maison")
    assert response.status_code == 500
    assert "milo-Maison" in response.json()["detail"]


def test_forget_takes_an_ssid_with_a_slash(service):
    """`useNetwork.js` sends the SSID through `encodeURIComponent`, and uvicorn
    decodes `%2F` before routing: a default `{ssid}` stops at the slash, so
    forgetting "Livebox/5GHz" answered 404 with no log and the profile kept
    autoconnecting."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.network import create_network_router

    nm = FakeStations([("milo-Livebox/5GHz", "u-1", "Livebox/5GHz", "k")])
    app = FastAPI()
    app.include_router(create_network_router(service))
    fake, patcher = with_nmcli(nm.route)
    with patcher:
        response = TestClient(app).delete("/api/network/wifi/saved/Livebox%2F5GHz")
    assert response.status_code == 200
    assert nm.profiles == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["listing", "delete"])
@pytest.mark.parametrize("call", ["save_network", "connect"])
async def test_saving_and_connecting_still_succeed_when_the_old_profile_survives(service, call, failure):
    """Both clear the SSID's old profiles once their own is in place, and the
    network is saved or joined by then: a clear-out that raised here would fail
    the wizard over a profile left beside the one that works."""
    nm = FakeStations([HOME], in_range={"Maison": "n3w-key"})
    if failure == "listing":
        nm.fail("-t", "-f", "NAME,UUID,TYPE", "connection", "show", answer=(8, "", "NetworkManager not running"))
    else:
        nm.fail("connection", "delete", "uuid", "u-home", answer=REFUSED_DELETE)
    fake, patcher = with_nmcli(nm.route)
    with patcher:
        await getattr(service, call)("Maison", "n3w-key")
    assert [p["psk"] for p in nm.profiles.values()] == ["secret", "n3w-key"]


@pytest.mark.asyncio
async def test_an_nmcli_that_hangs_is_killed(service):
    """Every read in this file goes through `_run_nmcli`. A `device wifi list`
    stalled behind a driver scan would otherwise hold the status refresh open
    for ever, and every NM event queues behind it."""
    class Hanging:
        def __init__(self):
            self.proc = FakeProc(0, "", "")

            async def times_out(input=None):
                raise asyncio.TimeoutError()
            self.proc.communicate = times_out

        async def __call__(self, program, *args, **kwargs):
            return self.proc

    hanging = Hanging()
    with patch("backend.core.network.service.asyncio.create_subprocess_exec",
               new=hanging), pytest.raises(asyncio.TimeoutError):
        await service.get_wifi_enabled()
    assert hanging.proc.killed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("call", ["connect", "save_network"])
async def test_nmcli_timeout_log_never_carries_the_psk(service, call):
    """Both put the PSK in `connection add`'s argv, and a timeout logs that argv
    at ERROR: errors.log is world-readable, the line is the banner on every UI,
    and the diagnostic report copies it. The timed-out command must still be
    named, or the line is useless to whoever reads it."""
    psk = "c0rrect-h0rse-battery"

    async def times_out():
        raise asyncio.TimeoutError()

    def route(args):
        if args[:2] == ("connection", "add"):
            return times_out()
        return (0, "", "")

    _, patcher = with_nmcli(route)
    with patcher, caplog_at_error() as records, pytest.raises(asyncio.TimeoutError):
        await getattr(service, call)("Maison", psk)

    messages = [r.getMessage() for r in records]
    timed_out = [m for m in messages if "nmcli timed out" in m]
    assert timed_out, f"no timeout logged: {messages}"
    assert not [m for m in messages if psk in m]
    assert "connection add" in timed_out[0] and "Maison" in timed_out[0]


@pytest.mark.asyncio
async def test_a_failed_key_read_logs_nothing_above_debug(service):
    """The satellite push reads up to three times and warns once; a reader that
    logged its own error put three banners up on every UI for one NM hiccup,
    ahead of the push's warning that already says it."""
    async def times_out():
        raise asyncio.TimeoutError()

    def route(args):
        if "--active" in args:
            return (0, f"milo-Maison:{FREEBOX_UUID}:wlan0", "")
        if args[0] == "-s":
            return times_out()
        return (0, "", "")

    _, patcher = with_nmcli(route)
    with patcher, caplog_at_error() as records:
        wifi = await service.shareable_wifi(tries=2, retry_delay_s=0)

    assert wifi.refusal is WifiShareRefusal.UNREADABLE
    assert records == []


def test_a_psk_field_list_is_not_mistaken_for_a_key():
    """A logged nmcli argv masks the value after `*.psk`; a `-f` list naming the
    PSK field carries no key, and masking what follows it would blank the verb
    of the very line that says which nmcli hung."""
    line = _redact_nmcli_args((
        "-s", "-t", "-f", "802-11-wireless.ssid,802-11-wireless-security.psk",
        "connection", "show", "uuid", FREEBOX_UUID,
    ))
    assert f"connection show uuid {FREEBOX_UUID}" in line
    assert _redact_nmcli_args(("wifi-sec.psk", "hunter2")) == "wifi-sec.psk ***"


@pytest.mark.asyncio
async def test_every_dbus_read_fails_open_when_the_object_is_gone(service, wired):
    """NM destroys these objects on a roam or a device removal, and a read that
    lands on a dead path raises out of dbus-next. Each of the four is on the
    status path the frontend polls: one unguarded raise blanks the whole panel.
    """
    nm = FakeNM()
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        await settle()
        # Every object of the tree disappears, as after `nmcli device delete`.
        nm.dead = True
        nm.objects.clear()
        service._ap_proxy = nm.objects.get(AP_PATH, _RaisingProxy())
        assert await service._read_active_ap_path() is None
        assert await service._read_active_ap_info() == (None, None)
        await service._reanchor_ip4("eth0")
        await service._anchor_ap_proxy(AP_PATH)
        assert service._ap_proxy is None
        assert await service.get_wifi_signal() is None


@pytest.mark.asyncio
async def test_reanchoring_an_interface_that_was_never_subscribed_is_a_no_op(service):
    """`_reanchor_ip4` is spawned from a handler; a device removed between the
    signal and the task would otherwise raise inside a fire-and-forget task,
    where the only trace is one BG-task error line."""
    await service._reanchor_ip4("wlan0")
    assert service._ip4_listener == {}


@pytest.mark.asyncio
async def test_an_ip4_listener_that_cannot_attach_leaves_the_path_recorded(service, wired):
    """The path is remembered even when the attach failed, so the next identical
    signal does not retry a subscription that is already known to be dead."""
    nm = FakeNM()
    fake, patcher = with_nmcli(wired)
    with with_nm(nm), patcher:
        await service.initialize()
        await settle()
        new_path = "/org/freedesktop/NetworkManager/IP4Config/9"   # never introspectable
        nm.ip4[ETH_PATH] = new_path
        await service._reanchor_ip4("eth0")

    assert "eth0" not in service._ip4_listener
    assert service._ip4_path["eth0"] == new_path


# --------------------------------------------------------------------------- #
# The regulatory-domain request model
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("code", ["fr", "Fr", "F1", "F "])
def test_a_country_code_that_is_not_two_uppercase_letters_is_refused(code):
    """`set_country` hands this straight to `sudo milo-set-wifi-country`, which
    writes it into `/boot/firmware/cmdline.txt` as `cfg80211.ieee80211_regdom=`.
    A value the kernel does not recognise is not rejected at boot — it is
    ignored, and the card silently falls back to the world-wide domain with the
    lowest transmit power and no 5 GHz channels."""
    from pydantic import ValidationError
    from backend.core.network.models import WifiCountryRequest

    with pytest.raises(ValidationError):
        WifiCountryRequest(country_code=code)


def test_a_valid_country_code_passes_through_unchanged():
    from backend.core.network.models import WifiCountryRequest
    assert WifiCountryRequest(country_code="FR").country_code == "FR"


@pytest.mark.asyncio
async def test_a_fresh_unit_with_no_profiles_reads_as_empty_everywhere(service):
    """The state of every unit out of the box, and `nmcli -t` answers it with an
    empty string — which `split("\\n")` turns into one empty row, not none."""
    fake, patcher = with_nmcli(lambda args: (0, "", ""))
    with patcher:
        assert await service.get_saved_networks() == []
        assert (await service.shareable_wifi()).refusal is WifiShareRefusal.NO_NETWORK
        await service.forget_network("Maison")
    assert not fake.argv_containing("delete")


@pytest.mark.asyncio
async def test_a_disabled_radio_reports_no_wifi_without_reading_the_device(service):
    """`nmcli radio wifi off` leaves `device show wlan0` answering nothing
    useful; the status must come from the radio state, not from parsing it."""
    def route(args):
        if args[:2] == ("radio", "wifi"):
            return (0, "disabled", "")
        return (1, "", "Error: Device 'wlan0' not found.")

    fake, patcher = with_nmcli(route)
    with patcher:
        status = await service.get_network_status()
    assert status.wifi_enabled is False
    assert status.wifi.connected is False


@pytest.mark.asyncio
async def test_a_wifi_device_that_cannot_be_read_is_reported_disconnected(service):
    """Radio on, device gone — a USB dongle pulled out. The badge must go grey
    rather than take the status route down with it."""
    def route(args):
        if args[:2] == ("radio", "wifi"):
            return (0, "enabled", "")
        if "wlan0" in args:
            return (1, "", "Error: Device 'wlan0' not found.")
        return (0, "", "")

    fake, patcher = with_nmcli(route)
    with patcher:
        status = await service.get_network_status()
    assert status.wifi.connected is False


@pytest.mark.asyncio
async def test_a_missing_hotspot_profile_is_not_an_error(service):
    """`_activate_hotspot` clears any stale profile before creating one, so the
    "unknown connection" answer is the normal one and must stay at debug — an
    error here would put a banner on the UI every time the AP is raised."""
    fake, patcher = with_nmcli(lambda args: (10, "", "Error: unknown connection 'Milō'."))
    with patcher, caplog_at_error() as records:
        await service._delete_hotspot_profile()
    assert records == []


@pytest.mark.asyncio
async def test_the_access_point_path_is_not_read_without_a_wireless_device(service):
    """A board with no wlan0 never anchors a proxy; the AP read is on the status
    path, which such a unit still serves."""
    assert await service._read_active_ap_path() is None


# ============================================================================
# The link change → connectivity re-check hook.
#
# NetworkManager emits a connectivity change only when its own verdict moves,
# and that verdict is address-family agnostic. Measured on the unit
# 2026-09-20: the ethernet cable was pulled, the appliance spent 43 seconds
# with no IPv4 default route at all, and NM never left `full` — wlan0 carries
# its own IPv6 default (proto ra, metric 600) and the probe kept succeeding
# over it. No NM event meant ConnectivityService never re-evaluated, so the
# appliance reported a working internet it did not have.
#
# A link change is the only other signal available, and this service is the
# only thing that sees one.
# ============================================================================

async def _settle_bg(service, max_yields: int = 50) -> None:
    """Let spawned tasks finish, without a wall-clock budget."""
    for _ in range(max_yields):
        if not service._bg._tasks:
            return
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_a_link_change_forces_a_connectivity_recheck(service):
    """Without this the appliance only ever learns about the internet when NM
    volunteers it, and NM stays silent for exactly the failure that matters."""
    connectivity = MagicMock()
    connectivity.recheck = AsyncMock()
    service.set_connectivity_service(connectivity)

    _fake, patcher = with_nmcli(_status_router(eth=("Wired connection 1", "192.168.1.55")))
    with patcher:
        await service._refresh_and_broadcast()
        await _settle_bg(service)

    connectivity.recheck.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_unchanged_link_does_not_spend_a_probe(service):
    """The re-check costs one IPv4 request. NM re-emits device properties
    several times a second while a card renegotiates, and a link that has not
    changed cannot have moved the uplink."""
    connectivity = MagicMock()
    connectivity.recheck = AsyncMock()
    service.set_connectivity_service(connectivity)

    _fake, patcher = with_nmcli(_status_router(eth=("Wired connection 1", "192.168.1.55")))
    with patcher:
        await service._refresh_and_broadcast()
        await _settle_bg(service)
        await service._refresh_and_broadcast()
        await _settle_bg(service)

    connectivity.recheck.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_link_change_is_harmless_before_the_service_is_wired(service):
    """Services are constructed before STEP 2 injects this one, and a D-Bus
    signal can land in that window. Unguarded it is an AttributeError inside a
    background task, which BackgroundTaskSet logs and nothing else notices."""
    _fake, patcher = with_nmcli(_status_router(eth=("Wired connection 1", "192.168.1.55")))
    with patcher:
        await service._refresh_and_broadcast()
        await _settle_bg(service)


# --------------------------------------------------------------------------- #
# A link coming up, told to its listeners (the setup wizard's first link)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_a_link_listener_hears_each_link_that_comes_up_and_only_then(service):
    """A unit set up over its AP counts as set up at its first link
    (`api/setup.py`). Heard on the AP alone, a mistyped key is set up again; not
    heard on a link that came up, the wizard stays on a unit that joined."""
    heard = []

    async def listener():
        heard.append(1)

    service.add_link_listener(listener)
    links = {"eth": ("--", None), "wlan": ("--", None)}
    _fake, patcher = with_nmcli(lambda args: _status_router(**links)(args))

    async def link_change(**moved):
        links.update(moved)
        await service._refresh_and_broadcast()
        await _settle_bg(service)
        return len(heard)

    with patcher:
        assert await link_change() == 0                                         # no link at boot
        assert await link_change(wlan=(HOTSPOT_NAME, "10.42.0.1")) == 0         # its own AP
        assert await link_change(wlan=("milo-Home", "192.168.1.11")) == 1       # WiFi joins
        assert await link_change(eth=("Wired connection 1", "192.168.1.10")) == 1  # still linked
        assert await link_change(wlan=("--", None)) == 1                        # WiFi out, cable in
        assert await link_change(eth=("--", None)) == 1                         # no link
        assert await link_change(eth=("Wired connection 1", "192.168.1.10")) == 2  # cable back
        await service.cleanup()


@pytest.mark.asyncio
async def test_the_listener_has_written_before_the_link_is_broadcast(service):
    """A wizard that sees the link come up reads whether the unit is set up:
    read before the listener wrote it, it stays on the wizard of a unit set up."""
    order = []

    async def listener():
        order.append("listener")

    async def broadcast(_event):
        order.append("broadcast")

    service.add_link_listener(listener)
    service.state_machine.broadcast = AsyncMock(side_effect=broadcast)
    _fake, patcher = with_nmcli(_status_router(eth=("Wired connection 1", "192.168.1.10")))
    with patcher:
        await service._refresh_and_broadcast()
        await _settle_bg(service)

    assert order == ["listener", "broadcast"]


@pytest.mark.asyncio
async def test_the_access_point_rising_and_ending_is_broadcast(service):
    """The status carries the AP (`hotspot_active`, `link_lost`). The AP
    rising over a unit already without a link moves no link, so the status the
    NM signals produce is the one already sent, deduplicated: the kiosk, whose
    socket outlives every network change, never heard of the AP — and once it
    had, never heard that it ended. Nothing here drives a refresh but the AP."""
    nm = FakeNetworkManager()
    fake, patcher = with_nmcli(nm.route)

    def hotspot_broadcasts():
        return [
            call.args[0].hotspot_active
            for call in service.state_machine.broadcast.await_args_list
            if isinstance(call.args[0], NetworkStatusChanged)
        ]

    with patcher, no_delay():
        await service.evaluate_reachability()
        await settle_until(lambda: HOTSPOT_NAME in nm.active)
        await _settle_bg(service)
        assert hotspot_broadcasts()[-1:] == [True]

        nm.ethernet = True
        await service.evaluate_reachability()
        await settle_until(lambda: not service.hotspot_active)
        await _settle_bg(service)
        await service.cleanup()

    assert hotspot_broadcasts()[-1:] == [False]


@pytest.mark.asyncio
async def test_the_unit_is_lost_from_the_access_points_rise_until_a_link(service):
    """The "WiFi lost" banner reads `link_lost`. Read from the AP, it went away
    for the half-minute of every home-network try, which takes the AP down
    while the unit is still unlinked; read from the link alone, it rose at
    every boot before the lease, for the whole of a speaker adoption and at
    every reassociation — none of which raises the AP."""
    nm = FakeNetworkManager(profiles={STATION: "infrastructure"})
    fake, patcher = with_nmcli(nm.route)
    with patcher, no_delay(), patch("backend.core.network.service.STATION_RETRY_S", 0):
        unlinked = await service.get_network_status()

        nm.hold(*STATION_UP)
        await service.evaluate_reachability()
        await settle_until(lambda: nm.in_flight)
        trying = await service.get_network_status()

        nm.release()
        await settle_until(lambda: STATION in nm.active)
        linked = await service.get_network_status()
        await service.evaluate_reachability(linked)

        nm.active.clear()
        dropped = await service.get_network_status()
        await service.cleanup()

    assert (unlinked.linked, unlinked.link_lost) == (False, False)
    assert (trying.hotspot_active, trying.linked, trying.link_lost) == (False, False, True)
    assert (linked.linked, linked.link_lost) == (True, False)
    assert (dropped.hotspot_active, dropped.linked, dropped.link_lost) == (False, False, False)


@pytest.mark.asyncio
async def test_an_access_point_adopted_late_is_broadcast(service):
    """A backend restarted under its own AP, no link, NM too busy to answer the
    restart's look: the clients were sent `hotspot_active: false`, and the AP
    adopted at the read again moves no link, so no NM signal brings a new
    status. Unsent, the kiosk keeps no "WiFi lost" banner on a unit on its AP."""
    nm = FakeNetworkManager(profiles={HOTSPOT_NAME: "ap"}, active={HOTSPOT_NAME})

    def hotspot_broadcasts():
        return [
            call.args[0].hotspot_active
            for call in service.state_machine.broadcast.await_args_list
            if isinstance(call.args[0], NetworkStatusChanged)
        ]

    _fake, patcher = with_nmcli(nm.route)
    with patcher:
        await service._refresh_and_broadcast(evaluate=False)
    assert hotspot_broadcasts() == [False]

    _fake, patcher = with_nmcli(refusing(nm, READ_DEVICES, 1))
    with patcher, no_delay(), patch("backend.core.network.service.MessageBus") as bus, \
         patch("backend.core.network.service.ADOPTION_READ_RETRY_S", 0):
        bus.side_effect = OSError("no system bus")
        await service.initialize()
        await settle_until(lambda: service.hotspot_active)
        await _settle_bg(service)
        await service.cleanup()

    assert hotspot_broadcasts()[-1:] == [True]


@pytest.mark.asyncio
async def test_a_status_superseded_while_its_listener_runs_is_not_sent_last(service):
    """A cable plugged into a unit on its AP: the refresh that sees the link
    spawns the evaluation that takes the AP down, whose own refresh sends
    `hotspot_active: False` while the setup listener still writes. Sent after
    it, the older `True` would be the last word on every client — the "WiFi
    lost" banner on a unit set up on Ethernet — and the dedup would keep it."""
    nm = FakeNetworkManager()
    fake, patcher = with_nmcli(nm.route)

    async def slow_listener():
        # settings.json written twice, on a slow card: longer than the AP's
        # removal and the refresh that follows it.
        for _ in range(200):
            await asyncio.sleep(0)

    service.add_link_listener(slow_listener)

    def hotspot_broadcasts():
        return [
            call.args[0].hotspot_active
            for call in service.state_machine.broadcast.await_args_list
            if isinstance(call.args[0], NetworkStatusChanged)
        ]

    with patcher, no_delay():
        await service.evaluate_reachability()
        await settle_until(lambda: HOTSPOT_NAME in nm.active)
        await _settle_bg(service)
        assert hotspot_broadcasts()[-1:] == [True]

        nm.ethernet = True
        await service._refresh_and_broadcast()
        await settle_until(lambda: not service.hotspot_active)
        await _settle_bg(service)
        await service.cleanup()

    assert hotspot_broadcasts()[-1:] == [False]


@pytest.mark.asyncio
async def test_a_listener_that_fails_costs_the_broadcast_nothing(service, caplog):
    async def listener():
        raise OSError("settings.json is read-only")

    service.add_link_listener(listener)
    _fake, patcher = with_nmcli(_status_router(eth=("Wired connection 1", "192.168.1.10")))
    with patcher, caplog.at_level(logging.ERROR):
        await service._refresh_and_broadcast()
        await _settle_bg(service)

    service.state_machine.broadcast.assert_awaited_once()
    assert any("read-only" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- #
# The server's WiFi handed to the satellites
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_satellites_are_handed_the_wifi_when_it_comes_up_or_moves_and_only_then(service):
    """Every satellite keeps the server's WiFi as a standby link.

    A push on every link change would run the satellites' helper for an
    Ethernet flap that moved nothing; no push on a change of network would
    leave them holding one the server has left.
    """
    satellite_wifi = Mock()
    satellite_wifi.push_all = AsyncMock()
    service.set_satellite_wifi_service(satellite_wifi)
    links = {"eth": ("Wired connection 1", "192.168.1.10"), "wlan": ("--", None)}
    fake, patcher = with_nmcli(lambda args: _status_router(**links)(args))

    async def link_change(**moved):
        links.update(moved)
        await service._refresh_and_broadcast()
        await _settle_bg(service)
        return satellite_wifi.push_all.await_count

    with patcher:
        assert await link_change() == 0                                        # Ethernet only
        assert await link_change(wlan=("milo-Home", "192.168.1.11")) == 1      # WiFi comes up
        assert await link_change(eth=("--", None)) == 1                        # cable out, same WiFi
        assert await link_change(wlan=("milo-Office", "192.168.1.12")) == 2    # another network
        await service.cleanup()
