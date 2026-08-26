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
import logging
import types

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
        self.killed = False

    async def communicate(self, input=None):
        return self._out, self._err

    def kill(self):
        self.killed = True


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




# --------------------------------------------------------------------------- #
# Which profiles a forget deletes — and which it must not
# --------------------------------------------------------------------------- #

def _profiles_router(listing, deleted):
    """Answers `connection show` with a listing, records every delete."""
    def router(args):
        if args[:2] == ("connection", "delete"):
            deleted.append(args[2])
            return (0, "", "")
        if "show" in args and "connection" in args:
            return (0, listing, "")
        return (0, "", "")
    return router


@pytest.mark.asyncio
async def test_forgetting_a_network_takes_its_netplan_and_milo_twins_with_it(service):
    """One SSID can own three profiles, and leaving one behind breaks the next connect.

    A surviving netplan profile is what made `nmcli device wifi connect` fail
    with 'key-mgmt: property is missing'; the whole delete-then-add shape exists
    to clear it. Dropping one of the three names silently restores that failure,
    on the first-boot path nothing else exercises.
    """
    deleted = []
    fake, patcher = with_nmcli(_profiles_router("\n".join([
        "Livebox:802-11-wireless",
        "milo-Livebox:802-11-wireless",
        "netplan-wlan0-Livebox:802-11-wireless",
        "Maison:802-11-wireless",
    ]), deleted))
    with patcher:
        await service.forget_network("Livebox")

    assert deleted, "nothing was deleted — the listing never reached the filter"
    assert set(deleted) == {"Livebox", "milo-Livebox", "netplan-wlan0-Livebox"}


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
    deleted = []
    fake, patcher = with_nmcli(_profiles_router("\n".join([
        "milo-Livebox:802-11-wireless",
        "Guest-Livebox:802-11-wireless",
    ]), deleted))
    with patcher:
        await service.forget_network("Livebox")

    assert deleted == ["milo-Livebox"]


@pytest.mark.asyncio
async def test_forgetting_a_network_never_deletes_a_wired_profile(service):
    """The route is `/wifi/saved/{ssid}` and takes the segment verbatim.

    `DELETE /api/network/wifi/saved/Wired%20connection%201` matches the wired
    profile by name; the connection-type filter is the only thing between that
    URL and the ethernet link the appliance is actually reachable on.
    """
    deleted = []
    fake, patcher = with_nmcli(_profiles_router("\n".join([
        "Wired connection 1:802-3-ethernet",
        "milo-Wired connection 1:802-11-wireless",
    ]), deleted))
    with patcher:
        await service.forget_network("Wired connection 1")

    assert deleted == ["milo-Wired connection 1"]


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
    fake, patcher = with_nmcli()
    with patcher:
        await service.save_network("Maison", "secret")

    assert fake.argv_containing("add"), "no profile was created — the save did nothing"
    assert not fake.argv_containing("up")
    assert not fake.argv_containing("disconnect")


@pytest.mark.asyncio
async def test_a_saved_profile_keeps_its_password_in_the_system_file(service):
    """psk-flags=0 is what makes the profile usable on a headless unit.

    NM's default defers the secret to an agent; there is none here, so the
    profile would come up asking for a password nobody can type — after the
    reboot, on a box with no screen and no network to reach it by.
    """
    fake, patcher = with_nmcli()
    with patcher:
        await service.save_network("Maison", "secret")

    add = fake.argv_containing("add")[0]
    assert "wifi-sec.psk-flags" in add
    assert add[add.index("wifi-sec.psk-flags") + 1] == "0"


@pytest.mark.asyncio
async def test_an_open_network_is_saved_with_no_security_settings_at_all(service):
    """A wpa-psk key-mgmt on an open network makes NM refuse to activate it."""
    fake, patcher = with_nmcli()
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


# --------------------------------------------------------------------------- #
# The credentials handed to a speaker being adopted
# --------------------------------------------------------------------------- #
#
# Verbatim shapes from this appliance, 2026-08-26:
#
#   nmcli -t -f NAME,DEVICE connection show --active
#       Wired connection 1:eth0
#       milo-Freebox-CA3555:wlan0
#       tailscale0:tailscale0
#       lo:lo
#
#   nmcli -s -t -f 802-11-wireless.ssid,802-11-wireless-security.psk \
#         connection show milo-Freebox-CA3555
#       802-11-wireless.ssid:<ssid>
#       802-11-wireless-security.psk:<psk>

ACTIVE_CONNECTIONS = (
    "Wired connection 1:eth0\n"
    "milo-Freebox-CA3555:wlan0\n"
    "tailscale0:tailscale0\n"
    "lo:lo"
)


def creds_router(active=ACTIVE_CONNECTIONS, ssid="Freebox-CA3555", psk="hunter2",
                 profile_rc=0):
    def router(args):
        if "--active" in args:
            return (0, active, "")
        if args[:2] == ("-s", "-t"):
            body = ""
            if ssid is not None:
                body += f"802-11-wireless.ssid:{ssid}\n"
            if psk is not None:
                body += f"802-11-wireless-security.psk:{psk}"
            return (profile_rc, body, "" if profile_rc == 0 else "not found")
        return (0, "", "")
    return router


@pytest.mark.asyncio
async def test_the_credentials_pushed_to_a_speaker_are_the_ones_on_wlan0(service):
    """`GET /discovery/server-wifi-creds` pre-fills the adoption form with these.

    The active list carries eth0, tailscale0 and lo alongside wlan0 — picking
    any of them would hand the speaker a name that is not a WiFi network at all,
    and it would reboot onto nothing.
    """
    fake, patcher = with_nmcli(creds_router())
    with patcher:
        assert await service.get_active_wifi_credentials() == {
            "ssid": "Freebox-CA3555", "password": "hunter2",
        }
    # The profile is read by the name found on wlan0, not by the SSID.
    assert fake.calls[1][-1] == "milo-Freebox-CA3555"


@pytest.mark.asyncio
async def test_the_setup_hotspot_is_never_offered_as_home_credentials(service):
    """During first-boot setup wlan0 carries the unit's own AP. Handing "Milō"
    to a speaker sends it to an access point that stops existing the moment
    setup completes."""
    fake, patcher = with_nmcli(creds_router(active=f"{HOTSPOT_NAME}:wlan0"))
    with patcher:
        assert await service.get_active_wifi_credentials() is None


@pytest.mark.asyncio
async def test_an_ethernet_only_server_offers_nothing(service):
    """The UI reads `available: false` and falls back to manual entry; a dict
    with an empty ssid would pre-fill the form with a blank it looks filled."""
    fake, patcher = with_nmcli(creds_router(active="Wired connection 1:eth0\nlo:lo"))
    with patcher:
        assert await service.get_active_wifi_credentials() is None


@pytest.mark.asyncio
async def test_an_open_home_network_yields_an_empty_password_not_none(service):
    """`AdoptSpeakerRequest.wifi_password` is a string; None would serialise as
    JSON null and the satellite would build a WPA profile for an open AP."""
    fake, patcher = with_nmcli(creds_router(psk=None))
    with patcher:
        assert await service.get_active_wifi_credentials() == {
            "ssid": "Freebox-CA3555", "password": "",
        }


@pytest.mark.asyncio
async def test_a_passphrase_containing_a_colon_survives_the_terse_format(service):
    """nmcli's terse mode separates on ':' and escapes literal ones. Reading the
    raw split would truncate the password at the first colon, and the speaker
    would fail to join with no error anyone can see from here."""
    fake, patcher = with_nmcli(creds_router(psk=r"pa\:ss\:word"))
    with patcher:
        creds = await service.get_active_wifi_credentials()
    assert creds["password"] == "pa:ss:word"


@pytest.mark.asyncio
async def test_a_profile_that_cannot_be_read_is_reported_not_guessed(service, caplog):
    """A refused secret read (no polkit, deleted profile) must not become an
    empty password the user then watches fail on the speaker."""
    fake, patcher = with_nmcli(creds_router(profile_rc=1))
    with patcher, caplog.at_level(logging.ERROR):
        assert await service.get_active_wifi_credentials() is None
    assert "milo-Freebox-CA3555" in caplog.text


@pytest.mark.asyncio
async def test_a_profile_without_an_ssid_field_yields_nothing(service):
    """802-11-wireless.ssid is absent on a profile NM stores by BSSID."""
    fake, patcher = with_nmcli(creds_router(ssid=None))
    with patcher:
        assert await service.get_active_wifi_credentials() is None


@pytest.mark.asyncio
async def test_a_failed_active_connection_list_yields_nothing(service):
    """First read of the pair; without the exit-code check an nmcli that died
    reads as "no wifi", which is the same answer as an ethernet-only server."""
    fake, patcher = with_nmcli(lambda args: (1, "", "NetworkManager not running"))
    with patcher:
        assert await service.get_active_wifi_credentials() is None


# --------------------------------------------------------------------------- #
# Connecting, forgetting, listing
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_connecting_disconnects_the_device_before_rewriting_its_profile(service):
    """NM will not fully remove a profile that is currently active, so a connect
    that skips the disconnect leaves the old profile behind and `connection up`
    activates the stale one — the unit reports the new SSID and stays on the old
    network."""
    fake, patcher = with_nmcli()
    with patcher, patch.object(service, "get_network_status", new=AsyncMock()):
        await service.connect("Maison", "secret")

    order = [c for c in fake.calls]
    disconnect = order.index(("device", "disconnect", "wlan0"))
    add = next(i for i, c in enumerate(order) if c[:2] == ("connection", "add"))
    assert disconnect < add


@pytest.mark.asyncio
async def test_connecting_tears_the_setup_hotspot_down_first(service):
    """NM auto-reconnects an AP profile the moment wlan0 is free, which would
    put the card back in AP mode behind the connect. Only done when the hotspot
    is up: `connection delete Milō` on every connect would log an error on every
    normal reconnect."""
    fake, patcher = with_nmcli()
    service._hotspot_active = True
    with patcher, patch.object(service, "get_network_status", new=AsyncMock()):
        await service.connect("Maison", "secret")

    assert fake.argv_containing(HOTSPOT_NAME)
    assert service.hotspot_active is False


@pytest.mark.asyncio
async def test_a_connect_on_a_unit_with_no_hotspot_deletes_no_hotspot_profile(service):
    fake, patcher = with_nmcli()
    with patcher, patch.object(service, "get_network_status", new=AsyncMock()):
        await service.connect("Maison", "secret")
    assert ("connection", "delete", HOTSPOT_NAME) not in fake.calls


@pytest.mark.asyncio
async def test_a_connection_that_never_comes_up_is_raised_as_a_timeout(service):
    """`connection up` is bounded at 30 s, and the bound is raised from the
    spawn boundary here rather than from a patched private. Without the arm the
    wizard's spinner sits on an `asyncio.TimeoutError` traceback instead of a
    message that names the network."""
    async def spawn(program, *args, **kwargs):
        assert program == "nmcli"
        if args[:2] == ("connection", "up"):
            raise asyncio.TimeoutError()
        return FakeProc(0, "", "")

    with patch("backend.core.network.service.asyncio.create_subprocess_exec",
               new=spawn):
        with pytest.raises(RuntimeError, match="'Maison' timed out"):
            await service.connect("Maison", "secret")


@pytest.mark.asyncio
async def test_a_refused_activation_names_the_network_that_refused(service):
    """Wrong password is the common case, and nmcli's stderr is the only text
    that says so; the wizard shows this string."""
    def route(args):
        if args[:2] == ("connection", "up"):
            return (4, "", "Error: Connection activation failed: Secrets were required")
        return (0, "", "")

    fake, patcher = with_nmcli(route)
    with patcher, pytest.raises(RuntimeError, match="Secrets were required"):
        await service.connect("Maison", "wrong")


@pytest.mark.asyncio
async def test_a_successful_connect_answers_with_the_new_status(service):
    """The wizard advances on this payload; returning None would leave it
    showing "connecting" on a unit that is connected."""
    fake, patcher = with_nmcli()
    status = object()
    with patcher, patch.object(service, "get_network_status",
                               new=AsyncMock(return_value=status)):
        assert await service.connect("Maison", "secret") is status


@pytest.mark.asyncio
async def test_the_saved_list_shows_the_ssid_not_the_milo_profile_name(service):
    """The settings panel lists these; `milo-Freebox` is a NM profile name and
    is not what the user recognises — nor what `forget_network` expects back."""
    def route(args):
        return (0, "milo-Freebox-CA3555:802-11-wireless\n"
                   "Wired connection 1:802-3-ethernet\n"
                   "tailscale0:tun\n"
                   "Voisins:802-11-wireless", "")

    fake, patcher = with_nmcli(route)
    with patcher:
        saved = await service.get_saved_networks()

    assert [n.ssid for n in saved] == ["Freebox-CA3555", "Voisins"]


@pytest.mark.asyncio
async def test_a_netplan_profile_is_not_offered_as_a_saved_network(service):
    """A netplan twin carries the same SSID as its milo profile; listing both
    puts the same network on screen twice, and the second entry's name is not
    an SSID at all."""
    def route(args):
        return (0, "netplan-wlan0-Maison:802-11-wireless\n"
                   "milo-Maison:802-11-wireless", "")

    fake, patcher = with_nmcli(route)
    with patcher:
        assert [n.ssid for n in await service.get_saved_networks()] == ["Maison"]


@pytest.mark.asyncio
async def test_a_saved_list_that_cannot_be_read_is_raised_not_emptied(service):
    """An empty list reads as "no saved networks" and the panel offers to set
    one up — over a unit that has one."""
    fake, patcher = with_nmcli(lambda args: (1, "", "NetworkManager not running"))
    with patcher, pytest.raises(RuntimeError, match="Failed to list saved networks"):
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

    assert fake.argv == ("sudo", "/usr/local/bin/milo-set-wifi-country", "FR")


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
                 ssid=b"Freebox-CA3555", strength=71):
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
            ifaces = {NM_DEVICE_IFACE: types.SimpleNamespace(
                get_ip4_config=self._ip4_getter(path))}
            if iface_name == "wlan0":
                ifaces[NM_DEVICE_WIRELESS_IFACE] = types.SimpleNamespace(
                    get_active_access_point=self._ap_getter())
            self.objects[path] = FakeObject(path, ifaces)
        for path in set(self.ip4.values()):
            self.objects[path] = FakeObject(path, {})
        self.objects[AP_PATH] = FakeObject(AP_PATH, {
            NM_ACCESS_POINT_IFACE: types.SimpleNamespace(
                get_ssid=self._const(ssid), get_strength=self._const(strength))
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
    and the two differ after a manual `nmcli` edit or a netplan profile."""
    def netplan_profile(args):
        if args[:2] == ("radio", "wifi"):
            return (0, "enabled", "")
        return (0, "GENERAL.CONNECTION:netplan-wlan0-Freebox\n"
                   "IP4.ADDRESS[1]:192.168.1.39/24", "")

    nm = FakeNM(ssid=b"Freebox-CA3555")
    fake, patcher = with_nmcli(netplan_profile)
    with with_nm(nm), patcher:
        await service.initialize()
        status = await service.get_network_status()

    # The derivation would answer "netplan-wlan0-Freebox" — a profile name, not
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
    """First boot with a card that refuses AP mode. `maybe_start_hotspot`
    swallows it into a log — but only because this raises first; a silent
    success would leave the wizard waiting for an SSID that never appears."""
    def route(args):
        if args[:2] == ("connection", "add"):
            return (1, "", "ap mode not supported")
        return (0, "", "")

    fake, patcher = with_nmcli(route)
    service.settings_service.get_setting = AsyncMock(return_value=False)
    with patcher:
        assert await service.maybe_start_hotspot(service.settings_service) is False


@pytest.mark.asyncio
async def test_a_device_status_that_cannot_be_read_does_not_block_the_hotspot(service):
    """`_has_active_connection` gates the first-boot AP. Reading a failure as
    "there is a network" is the one answer that must not happen: the unit would
    boot with no hotspot and no LAN, and only a reflash recovers it."""
    def route(args):
        if args[:2] == ("-t", "-f") and "status" in args:
            # 14th blind spot: nmcli prints the rows it managed to read on
            # stdout AND exits non-zero. Parsing them past the exit code is how
            # a partial answer becomes "there is a network".
            return (1, "ethernet:connected:Wired connection 1",
                    "Error: NetworkManager is not running.")
        return (0, "", "")

    fake, patcher = with_nmcli(route)
    service.settings_service.get_setting = AsyncMock(return_value=False)
    with patcher:
        assert await service.maybe_start_hotspot(service.settings_service) is True


@pytest.mark.asyncio
async def test_a_short_device_status_row_is_skipped(service):
    """`wifi-p2p:disconnected:` is on every real unit and has an empty third
    column; a row read by index must tolerate it."""
    def route(args):
        if "status" in args:
            return (0, "wifi-p2p\nethernet:connected:Wired connection 1", "")
        return (0, "", "")

    fake, patcher = with_nmcli(route)
    service.settings_service.get_setting = AsyncMock(return_value=False)
    with patcher:
        assert await service.maybe_start_hotspot(service.settings_service) is False


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
async def test_forgetting_a_network_while_nm_is_down_deletes_nothing(service):
    """`_delete_ssid_profiles` runs before every save and every connect. Reading
    a failed list as an empty one is what must not happen the other way round —
    here the failure simply deletes nothing, and the caller's own `connection
    add` reports the real problem."""
    fake, patcher = with_nmcli(lambda args: (1, "", "NetworkManager not running"))
    with patcher:
        await service.forget_network("Maison")
    assert not fake.argv_containing("delete")


@pytest.mark.asyncio
async def test_a_profile_that_refuses_to_be_deleted_is_logged(service, caplog):
    """The delete is best-effort by design, but a profile that survives is what
    makes the next `connection up` activate the old credentials — the operator
    log is the only place that says so."""
    def route(args):
        if args[:2] == ("connection", "delete"):
            return (1, "", "Error: unknown connection")
        return (0, "milo-Maison:802-11-wireless", "")

    fake, patcher = with_nmcli(route)
    with patcher, caplog.at_level(logging.ERROR):
        await service.forget_network("Maison")
    assert "milo-Maison" in caplog.text


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
        assert await service.get_active_wifi_credentials() is None
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
    """`maybe_start_hotspot` deletes it on every completed-setup boot, so the
    "unknown connection" answer is the normal one and must stay at debug — an
    error here would put a banner on the UI at every boot."""
    fake, patcher = with_nmcli(lambda args: (10, "", "Error: unknown connection 'Milō'."))
    service.settings_service.get_setting = AsyncMock(return_value=True)
    with patcher, caplog_at_error() as records:
        assert await service.maybe_start_hotspot(service.settings_service) is False
    assert records == []


@pytest.mark.asyncio
async def test_the_access_point_path_is_not_read_without_a_wireless_device(service):
    """A board with no wlan0 never anchors a proxy; the AP read is on the status
    path, which such a unit still serves."""
    assert await service._read_active_ap_path() is None
