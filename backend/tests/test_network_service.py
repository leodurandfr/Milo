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

Deliberately **not** covered, and named in the checklist as still bare: the
two D-Bus subscription tiers (device / wireless property signals and the AP
proxy re-anchoring behind them). They have no observable outside a live NM
session, and a mock of dbus-next would assert the mock. The one D-Bus-adjacent
thing asserted below is the *absence* of a cost — that reading the live signal
spawns no process — which is measured at the nmcli boundary, not at dbus-next's.
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.core.network.service import HOTSPOT_NAME, NetworkService, _parse_nmcli_line


# --------------------------------------------------------------------------- #
# The nmcli boundary
# --------------------------------------------------------------------------- #

class FakeProc:
    def __init__(self, rc, stdout, stderr):
        self.returncode = rc
        self._out = stdout.encode()
        self._err = stderr.encode()

    async def communicate(self, input=None):
        return self._out, self._err

    def kill(self):
        pass


class FakeNmcli:
    """Stands in for the nmcli binary; records argv, answers from a router."""

    def __init__(self, router=None):
        self.calls: list[tuple[str, ...]] = []
        self._router = router or (lambda args: (0, "", ""))

    async def __call__(self, program, *args, **kwargs):
        assert program == "nmcli", f"unexpected program spawned: {program}"
        self.calls.append(args)
        return FakeProc(*self._router(args))

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


@pytest.mark.asyncio
async def test_the_setup_hotspot_is_not_reported_as_a_wifi_connection(service):
    """The AP Milō raises for its own wizard is not a network it joined.

    Reporting it as connected would let the wizard believe setup succeeded
    while the unit is still serving its own captive portal.
    """
    fake, patcher = with_nmcli(_status_router(wlan=(HOTSPOT_NAME, "10.42.0.1")))
    with patcher:
        status = await service.get_network_status()

    assert status.wifi.connected is False


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


# --------------------------------------------------------------------------- #
# The AP transition — what a blank card depends on
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_a_completed_setup_raises_no_hotspot_and_clears_a_stale_profile(service):
    """After setup the AP must never come back, and its profile must not linger.

    A surviving profile is what lets NM raise the AP on its own at some later
    boot, behind the backend's back.
    """
    service.settings_service.get_setting = AsyncMock(return_value=True)
    fake, patcher = with_nmcli()
    with patcher:
        started = await service.maybe_start_hotspot(service.settings_service)

    assert started is False
    assert service.hotspot_active is False
    assert fake.argv_containing("delete")


@pytest.mark.asyncio
async def test_an_unconfigured_unit_with_a_live_network_does_not_raise_the_hotspot(service):
    """An adopted-but-unconfigured unit stays reachable on the network it has."""
    service.settings_service.get_setting = AsyncMock(return_value=False)

    def router(args):
        if "status" in args:
            return (0, "ethernet:connected:Wired connection 1", "")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher:
        started = await service.maybe_start_hotspot(service.settings_service)

    assert started is False
    assert not fake.argv_containing("add")


@pytest.mark.asyncio
async def test_the_units_own_hotspot_does_not_count_as_a_live_network(service):
    """Otherwise a restart while the AP is up would decide setup can be skipped."""
    service.settings_service.get_setting = AsyncMock(return_value=False)

    def router(args):
        if "status" in args:
            return (0, f"wifi:connected:{HOTSPOT_NAME}", "")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher:
        started = await service.maybe_start_hotspot(service.settings_service)

    assert started is True
    assert service.hotspot_active is True


@pytest.mark.asyncio
async def test_the_hotspot_profile_is_created_with_autoconnect_disabled(service):
    """`connection.autoconnect no` is load-bearing, and nmcli defaults it to yes.

    A profile that auto-connects survives a reboot and raises the AP by itself,
    so `_hotspot_active` stays False and nothing ever tears it down. The AP must
    only exist because maybe_start_hotspot ran.
    """
    service.settings_service.get_setting = AsyncMock(return_value=False)
    fake, patcher = with_nmcli()
    with patcher:
        assert await service.maybe_start_hotspot(service.settings_service) is True

    add = next(c for c in fake.calls if "add" in c)
    assert "connection.autoconnect" in add
    assert add[add.index("connection.autoconnect") + 1] == "no"
    assert add[add.index("wifi.mode") + 1] == "ap"
    assert add[add.index("ipv4.method") + 1] == "shared"


@pytest.mark.asyncio
async def test_a_hotspot_that_fails_to_come_up_leaves_no_profile_behind(service):
    """The rollback is what keeps a failed first boot from arming a later one."""
    service.settings_service.get_setting = AsyncMock(return_value=False)

    def router(args):
        if args[:2] == ("connection", "up"):
            return (1, "", "no wireless device")
        return (0, "", "")

    fake, patcher = with_nmcli(router)
    with patcher:
        started = await service.maybe_start_hotspot(service.settings_service)

    assert started is False
    assert service.hotspot_active is False
    # one cleanup before creating, one rolling back after the failed activation
    assert len(fake.argv_containing("delete")) == 2


# --------------------------------------------------------------------------- #
# Fail-open on D-Bus
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_initialize_fails_open_when_networkmanager_dbus_is_absent(service):
    """CLAUDE.md's rule: no D-Bus must degrade, never raise — dev runs without NM.

    A raise here aborts initialize_services() and the whole backend never comes
    up, on a developer machine and on any unit where NM is slow to appear.
    """
    with patch("backend.core.network.service.MessageBus") as bus:
        bus.side_effect = OSError("no system bus")
        assert await service.initialize() is False

    assert service.hotspot_active is False


@pytest.mark.asyncio
async def test_the_nmcli_read_path_still_works_without_dbus(service):
    """Degraded means "no live updates", not "no status" — the badge must still fill."""
    with patch("backend.core.network.service.MessageBus") as bus:
        bus.side_effect = OSError("no system bus")
        await service.initialize()

    fake, patcher = with_nmcli(_status_router(eth=("Wired connection 1", "192.168.1.42")))
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


