"""
Network management service (Ethernet + WiFi) using NetworkManager.

Action paths (scan, connect, save, forget, hotspot, set_country) run through
nmcli — their D-Bus equivalents would require a PolicyKit dance and are far
more verbose. Status *change* triggers come from a direct NM D-Bus
subscription so the UI sees physical link / DHCP / WiFi roam events the
moment NM has them, with no polling and no race against DHCP lease
completion.

Two subscription tiers (eth0 + wlan0):
  1. Device.PropertiesChanged (base interface) — eth0 + wlan0
     Watches State, Ip4Config, ActiveConnection.
  2. Device.Wireless.PropertiesChanged — wlan0 only
     Watches ActiveAccessPoint (associate / roam / dissociate). Also anchors
     the AccessPoint proxy below, re-anchored on every change so we never
     read a stale AP across roams.

The associated AP's Strength is NOT subscribed to. It was, and it cost more
than it bought: NM re-samples RSSI every few seconds, each sample triggered a
full status re-read (four nmcli forks, ~55 ms CPU), and measurement on the
appliance found 7 broadcasts in 120 s of which 0 changed anything on screen —
the only consumer is a four-arc icon quantised at 25/50/75. The strength is
therefore read on demand through `get_wifi_signal()`, which the WiFi panel
polls while it is on screen and nothing calls when it is not.

Fails open: if NM D-Bus is unavailable (dev environment, NM stopped),
initialize() logs and returns False. The existing nmcli-based status path
keeps working — just without live updates.
"""
import asyncio
import contextlib
import enum
import logging
import re
import time
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Iterator, List, Literal, Optional, Set, Tuple

from dbus_next.aio import MessageBus
from dbus_next.constants import BusType

from backend.config.constants import (
    RECOVERY_AP_ADDRESS, RECOVERY_AP_PREFIX, SET_WIFI_COUNTRY_CMD, WIFI_RADIO_FORCED_FILE,
)
from backend.core.models.ws_events import NetworkStatusChanged
from backend.shared.background import BackgroundTaskSet

from backend.core.network.models import (
    WifiNetwork, WifiConnectionStatus, EthernetStatus,
    NetworkStatus, SavedNetwork,
)


# Setup hotspot SSID (shared across all Milō devices). Acceptable trade-off:
# if multiple fresh devices broadcast their hotspot at the same time, the
# scanner deduplicates by SSID and only one of them is adoptable at a time.
HOTSPOT_NAME = "Milō"

# The station profile the speaker adoption joins a speaker's setup hotspot
# through (`core/multiroom/wifi_adoption.py`). Its SSID is HOTSPOT_NAME, its
# name must not be: the recovery AP deletes the profile called HOTSPOT_NAME
# before raising itself. Kept out of the `milo-` prefix `_get_saved_ssid` reads.
ADOPTION_PROFILE = "Milō adoption"

# The WiFi profiles this unit creates for its own use. Neither stands for a
# network the unit is on: not a link, not credentials, not a home network.
OWN_PROFILES = frozenset({HOTSPOT_NAME, ADOPTION_PROFILE})

# nmcli's exit status for "Connection, device, or access point does not exist"
# (nmcli(1), EXIT STATUS): a delete that found nothing to remove.
NMCLI_NO_SUCH_CONNECTION = 10

# How long the unit may sit with no usable link before it raises the access
# point. Sized on NetworkManager's own behaviour rather than on taste: a cold
# boot has no connection until autoconnect plus a DHCP transaction completes,
# and NM retries a failing profile several times before it gives up. Shorter
# than this and the AP steals the radio from an association that was about to
# succeed.
RECOVERY_AP_DELAY_S = 45.0

# While the access point holds the radio, how often the home network is tried
# again. NetworkManager never autoconnects a profile onto a device that already
# carries one, so on a WiFi-only unit nothing else gives the radio back to the
# box once it returns. Each try takes the AP down for as long as `connection up`
# runs, so a try is held off while someone is using the AP.
STATION_RETRY_S = 300.0

# How many tries in a row a client of the AP can hold off. Someone repairing the
# unit over it is done well within that; a device that merely rejoined the open
# AP and keeps polling it would otherwise keep the unit off its home network.
STATION_RETRY_MAX_HOLDS = 2
# A save over the AP that asks for a try now (Settings › Network, repairing a
# unit whose home network changed) gets it this long after the save: time for
# the answer to reach the phone before the AP it came through goes down. Holding
# the try off for the phone, as the retry loop does, kept a repair waiting up to
# (STATION_RETRY_MAX_HOLDS + 1) × STATION_RETRY_S with nothing on screen.
STATION_TRY_AFTER_SAVE_S = 5.0

# How often a restart that could not read whether NM kept the access point up
# reads it again: quickly for a few tries, then slowly for as long as it takes.
# Until one read answers, the recovery gate counts the AP as up — and nothing
# owns an AP really left up, so the reading never stops while it is unanswered.
ADOPTION_READ_TRIES = 5
ADOPTION_READ_RETRY_S = 10.0
ADOPTION_READ_SLOW_RETRY_S = 60.0

# The bound on each nmcli call that creates and activates the access point.
HOTSPOT_NMCLI_TIMEOUT_S = 20.0

# The bound on a `connection up` of a station profile, the one being tried
# included: NM gives up on a wrong key or a network out of range well inside it.
STATION_UP_TIMEOUT_S = 30.0

# How long `shareable_wifi` waits before reading again a key that read back empty.
SHARE_READ_RETRY_S = 10.0

# `nmcli connection add` prints "Connection '<name>' (<uuid>) successfully added."
# — translated with the locale, the UUID in parentheses either way.
_ADDED_UUID = re.compile(r"\(([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\)")

# `nmcli radio wifi on` returns before NM has re-enumerated wlan0. 10 s of
# half-second polls, which is generous: the device reappeared in under 2 s on
# the appliance.
RADIO_SETTLE_POLLS = 20
RADIO_SETTLE_INTERVAL_S = 0.5

# Once the recovery path has turned the radio back on, how long a saved network
# gets to associate before the access point takes the radio. Measured on the
# appliance 2026-10-03: NM started autoconnect 2.5 s after the radio came back
# and the device read `connected` 5.4 s after it — but the AP was raised in
# between, so the unit sat on its own hotspot with the home network in range.
AUTOCONNECT_POLLS = 30
AUTOCONNECT_INTERVAL_S = 1.0

# NM's own DHCPv4 timeout (the `ipv4.dhcp-timeout` default; its journal says
# "timeout in 45 seconds"). An activated wlan0 still without a lease past it is
# no longer joining: under `ipv4.may-fail` it can stay activated on IPv6 alone
# for good, and "Connecting…" would never resolve.
DHCP_LEASE_WAIT_S = 45.0

# NMDeviceState values bounding an activation (prepare … activated).
NM_DEVICE_STATE_PREPARE = 40
NM_DEVICE_STATE_ACTIVATED = 100

NM_SERVICE = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
NM_IFACE = "org.freedesktop.NetworkManager"
NM_DEVICE_IFACE = "org.freedesktop.NetworkManager.Device"
NM_DEVICE_WIRELESS_IFACE = "org.freedesktop.NetworkManager.Device.Wireless"
NM_DEVICE_WIRED_IFACE = "org.freedesktop.NetworkManager.Device.Wired"
NM_ACCESS_POINT_IFACE = "org.freedesktop.NetworkManager.AccessPoint"
NM_IP4_CONFIG_IFACE = "org.freedesktop.NetworkManager.IP4Config"
DBUS_PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

# NMDeviceType values `read_link` can weigh.
NM_DEVICE_TYPE_ETHERNET = 1
NM_DEVICE_TYPE_WIFI = 2

# Base-Device properties whose change should force a status re-broadcast.
_DEVICE_BASE_PROPS = {"State", "Ip4Config", "ActiveConnection"}
# IP4Config properties whose change should force a re-broadcast. NM updates
# these in place when DHCP completes, without re-emitting Device.Ip4Config
# PropertiesChanged, so we must subscribe to the IP4Config object itself.
_IP4_CONFIG_PROPS = {"AddressData", "Addresses"}


@dataclass(frozen=True)
class InterfaceLink:
    """What one interface negotiated: its kind, its rate in Mbit/s (None when
    NM reports none), and for Wi-Fi the associated AP's strength (0-100)."""
    kind: Literal["ethernet", "wifi"]
    speed_mbps: Optional[float]
    signal_percent: Optional[int]


class NetworkService:
    """Network management service (Ethernet + WiFi) wrapping nmcli + NetworkManager D-Bus."""

    WIFI_INTERFACE = "wlan0"

    def __init__(self, state_machine, settings_service):
        self.logger = logging.getLogger(__name__)
        self.state_machine = state_machine
        self.settings_service = settings_service
        self._hotspot_active: bool = False
        # True from the recovery AP's rise until a link comes back
        # (`NetworkStatus.link_lost`), whether or not the AP is up meanwhile.
        self._link_lost: bool = False
        # True while a restart could not read whether NM kept an AP up: neither
        # adopted nor ruled out, read again by `_retry_hotspot_adoption`.
        self._hotspot_unread: bool = False
        # One teardown of the AP at a time: a second one waits for the first,
        # then finds the AP gone, or tries again where the first could not.
        self._hotspot_teardown = asyncio.Lock()
        # The pending "still no link" timer. A tracked task on self, not a
        # fire-and-forget one: it has to be cancellable the instant a link
        # appears, or an AP is raised seconds after the unit became reachable.
        self._recovery_arm: Optional[asyncio.Task] = None
        # How many `recovery_held()` are open: none arms the timer meanwhile.
        self._recovery_holds: int = 0
        # The loop that tries the home network again while the AP is up, tracked
        # for the same reason: anything that takes the AP down ends it.
        self._station_retry: Optional[asyncio.Task] = None
        # How many tries have been made, so each saved network gets its turn.
        self._station_tries: int = 0
        # The UUID of the profile `save_network` wrote last, tried first at the
        # next try (`_retry_station`): over the AP, a save is how a client
        # repairs a unit whose home network changed, and the old network's
        # profile is still there to take the turn.
        self._station_saved: Optional[str] = None
        # Requests and sockets from the AP's clients open right now, and when
        # the last one ended — a try is skipped under either.
        self._recovery_clients: int = 0
        self._recovery_client_left: Optional[float] = None
        # True while the WiFi radio is on only because the recovery path turned
        # it on. Restored to the user's setting the moment it stops costing
        # reachability — see _restore_radio_preference. Kept on disk as well
        # (_set_radio_override), since NM keeps the radio on across a restart.
        self._radio_forced_on: bool = False
        # Loop time at which wlan0 was first seen activated without an IPv4
        # lease — bounds how long that reads as "connecting".
        self._lease_wait_since: Optional[float] = None
        self._connect_lock = asyncio.Lock()
        # One country change at a time: the helper rewrites the boot cmdline.txt.
        self._country_lock = asyncio.Lock()

        # D-Bus state
        self._bus: Optional[MessageBus] = None
        # Cached wlan0 proxy — reused to read ActiveAccessPoint between events.
        self._wlan_proxy = None
        # iface_name → (properties_iface, handler), tier 1 (base Device)
        self._device_listeners: dict = {}
        # iface_name → device proxy (used to re-read Ip4Config path on demand)
        self._device_proxies: dict = {}
        # Tier 1b — per-device IP4Config listener, re-anchored when
        # Device.Ip4Config path changes. Required because NM updates
        # IP4Config.Addresses in place on DHCP lease without bubbling a
        # fresh Device.Ip4Config PropertiesChanged signal.
        self._ip4_path: dict = {}
        self._ip4_listener: dict = {}
        # (properties_iface, handler) on wlan0, tier 2 (Device.Wireless)
        self._wireless_listener: Optional[Tuple] = None
        # AccessPoint proxy for the associated AP, re-anchored by tier 2 each
        # time ActiveAccessPoint changes path. Read on every status refresh for
        # live SSID + Strength (avoids `nmcli dev wifi`, which can stall on
        # scans) and by get_wifi_signal(). No listener hangs off it — see the
        # module docstring on why Strength is polled on demand, not subscribed.
        self._ap_path: Optional[str] = None
        self._ap_proxy = None
        # Broadcast plumbing
        self._last_broadcast: Optional[NetworkStatus] = None
        self._broadcast_lock = asyncio.Lock()
        # Serializes listener mutations to avoid leaks when path changes bounce.
        self._listener_setup_lock = asyncio.Lock()
        # Holds strong refs to fire-and-forget tasks (CPython can otherwise GC
        # a pending task that has no external reference).
        self._bg = BackgroundTaskSet(self.logger, "network")
        # Set after construction: this service sees link changes, and
        # ConnectivityService is the only thing that can turn one into a verdict.
        self._connectivity_service = None
        # Set after construction, for the same reason: this service is the one
        # that sees the server's WiFi come up or change network.
        self._satellite_wifi_service = None
        # Called, in order, each time the unit gains a link (`add_link_listener`).
        self._link_listeners: List[Callable[[], Awaitable[None]]] = []

    def set_connectivity_service(self, connectivity_service) -> None:
        self._connectivity_service = connectivity_service

    def set_satellite_wifi_service(self, satellite_wifi_service) -> None:
        self._satellite_wifi_service = satellite_wifi_service

    def add_link_listener(self, listener: Callable[[], Awaitable[None]]) -> None:
        """Await `listener()` each time the unit gains a link to a network: an
        Ethernet lease, or WiFi associated to a network that is not a Milō access
        point (`_get_wifi_info` excludes both). Once per gain, the first status
        read included, and before any linked status is broadcast: the listeners
        run under the broadcast lock, so no newer refresh sends one meanwhile,
        and a client that reads anything after a linked status reads what the
        listener wrote. A listener therefore holds every refresh while it runs,
        and must not wait on one. What a link means beyond that is the
        listener's: this layer knows nothing of the setup wizard (`api/setup.py`,
        the only one).
        """
        self._link_listeners.append(listener)

    @property
    def hotspot_active(self) -> bool:
        """Whether the recovery access point is up, being raised, or not yet
        removed — NM carries it, and its clients, until a delete succeeds."""
        return self._hotspot_active

    @property
    def recovery_ap_possible(self) -> bool:
        """Whether the AP may be carrying clients: up, or not yet read back
        after a restart."""
        return self._hotspot_active or self._hotspot_unread

    @property
    def open_ap_reachable(self) -> bool:
        """Whether a client of an open Milō access point can reach this unit:
        its own recovery AP may be carrying clients, or an adoption holds wlan0
        on a speaker's hotspot, where the unit is one client among strangers
        (`recovery_held`). What `api/middleware.py::RecoveryAccessPointGuard`
        refuses on."""
        return self.recovery_ap_possible or self._recovery_holds > 0

    @contextlib.contextmanager
    def serving_recovery_client(self) -> Iterator[None]:
        """Held around a request or a socket from a client of the access point
        (`api/middleware.py::RecoveryClientTracker`). The home-network retry is
        held off while one is open, or after one was served since its last try."""
        self._recovery_clients += 1
        try:
            yield
        finally:
            self._recovery_clients -= 1
            self._recovery_client_left = time.monotonic()

    @contextlib.asynccontextmanager
    async def recovery_held(self) -> AsyncIterator[None]:
        """Held while the unit leaves its network on purpose — the speaker
        adoption, which takes a WiFi-only server onto a speaker's hotspot for
        as long as an association, a push and a restore take. No recovery AP is
        raised meanwhile, and a timer still waiting is disarmed: an AP raised
        then takes the radio from under the adoption. A timer already raising
        the AP is let finish instead — cut short, it leaves the AP owned and
        half made (`_start_hotspot`) — so read `hotspot_active` only inside.
        Released, the reachability is looked at again, in the background, so
        the holder's own error stands."""
        self._recovery_holds += 1
        try:
            arm = self._recovery_arm
            if arm is not None and self._hotspot_active:
                await asyncio.wait({arm})
            else:
                self._cancel_recovery_arm()
            yield
        finally:
            self._recovery_holds -= 1
            if not self._recovery_holds:
                self._bg.spawn(self.evaluate_reachability(), label="recovery_hold_released")

    # =========================================================================
    # Public API
    # =========================================================================

    async def scan_networks(self, rescan: bool = True) -> List[WifiNetwork]:
        """Scan for available WiFi networks.

        `rescan=False` reads NetworkManager's cache (`--rescan no`), filled by
        its own background scans: an active scan takes the radio off its
        channel for up to ~130 ms at a time, which a link carrying audio cannot
        absorb.

        Deduplicates SSIDs (the associated AP, else the strongest) and sorts by
        signal descending.
        """
        rc, stdout, stderr = await self._run_nmcli(
            "-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE",
            "device", "wifi", "list", "--rescan", "yes" if rescan else "no",
            timeout=15.0
        )

        if rc != 0:
            self.logger.error("WiFi scan failed: %s", stderr)
            raise RuntimeError(f"WiFi scan failed: {stderr}")

        if not stdout:
            return []

        networks: dict[str, WifiNetwork] = {}
        for line in stdout.split("\n"):
            if not line:
                continue

            fields = _parse_nmcli_line(line)
            if len(fields) < 4:
                continue

            ssid = fields[0]
            if not ssid:
                continue

            try:
                signal = int(fields[1])
            except ValueError:
                signal = 0

            security = fields[2] if fields[2] else ""
            in_use = fields[3] == "*"

            # Deduplicate: the associated AP stands for its SSID, else the
            # strongest — a dual-band router's other radio is often stronger
            # than the one the unit is on, and must not replace its signal.
            kept = networks.get(ssid)
            if kept is None or (not kept.in_use and (in_use or signal > kept.signal)):
                networks[ssid] = WifiNetwork(
                    ssid=ssid,
                    signal=signal,
                    security=security,
                    in_use=in_use,
                )

        return sorted(networks.values(), key=lambda n: n.signal, reverse=True)

    async def get_network_status(self) -> NetworkStatus:
        """Get combined network status (both ethernet and WiFi)."""
        wifi_enabled = await self.get_wifi_enabled()
        ethernet = await self._get_ethernet_info()

        if wifi_enabled:
            wifi = await self._get_wifi_info()
        else:
            wifi = WifiConnectionStatus(connected=False)

        status = NetworkStatus(
            wifi_enabled=wifi_enabled,
            ethernet=ethernet,
            wifi=wifi,
            hotspot_active=self.hotspot_active,
            link_lost=self._link_lost,
        )
        # A link read here is one, before the evaluation that clears the flag.
        if status.linked:
            status.link_lost = False
        return status

    async def get_wifi_enabled(self) -> bool:
        """Check if WiFi radio is enabled."""
        rc, stdout, _ = await self._run_nmcli("radio", "wifi")
        return rc == 0 and stdout.strip() == "enabled"

    async def set_wifi_enabled(self, enabled: bool) -> None:
        """Enable or disable WiFi radio."""
        await self._set_radio(enabled)
        # Whoever sets the radio now owns the setting, the user above all: an
        # explicit "on" while the recovery override stood would otherwise be
        # undone by _restore_radio_preference as if it were the override's.
        await self._set_radio_override(False)

    async def _set_radio(self, enabled: bool) -> None:
        """Switch the radio, leaving the recovery override as it stands."""
        state = "on" if enabled else "off"
        rc, _, stderr = await self._run_nmcli("radio", "wifi", state)
        if rc != 0:
            raise RuntimeError(f"Failed to set WiFi radio {state}: {stderr}")
        self.logger.info("WiFi radio set to %s", state)

    async def save_network(self, ssid: str, password: Optional[str] = None, *, try_now: bool = False) -> None:
        """Save WiFi credentials without connecting.

        Creates a NM profile for the given SSID, on disk and autoconnecting.
        The current connection (e.g. hotspot) is left untouched, so nothing
        validates the key here. While the AP holds the radio, the home-network
        retry tries this profile first at its next turn, ahead of the ones saved
        before it.

        `try_now` (Settings › Network over the AP) has that turn come
        STATION_TRY_AFTER_SAVE_S after the save, whoever is on the AP — and
        keeps the SSID's other profiles until the network is joined, by a try
        or by NetworkManager's autoconnect, as a connect does
        (`_keep_station_profile`): replaced at the save, the one
        working profile went with a mistyped key, or with a right one typed
        while the box was off, and the unit stayed on its AP once the box was
        back. A save with no try (the wizard, which reboots next, so no try of
        this process would come to replace them) replaces the SSID's other
        profiles once the new one is written; the wizard counts the unit as set
        up only once a link has come up (`api/setup.py`), so a mistyped key
        brings the wizard back on the AP.
        """
        async with self._connect_lock:
            # Saving a network is choosing the radio as much as connecting is:
            # the wizard saves, then reboots, and the override must not switch
            # off the radio carrying the network it chose once ethernet appears.
            await self._set_radio_override(False)
            self.logger.info("Saving WiFi network: %s", ssid)
            try:
                uuid = await self._write_station_profile(ssid, password)
            except RuntimeError as e:
                self.logger.error("Failed to save WiFi profile for '%s': %s", ssid, e)
                raise RuntimeError(f"WiFi save failed: {e}") from e
            self._station_saved = uuid

            if try_now and self._hotspot_active:
                self.logger.info(
                    "WiFi profile saved for '%s': tried in a few seconds, its other profiles kept until a try joins",
                    ssid,
                )
                self._bg.spawn(self._try_station_after_save(), label="station_try_after_save")
                return

            # Best effort: the network is saved, and a profile left over is
            # one more NM tries.
            with contextlib.suppress(RuntimeError):
                await self._delete_ssid_profiles(ssid, keep=uuid)
            if self._hotspot_active:
                self.logger.info("WiFi profile saved for '%s': tried at the access point's next retry", ssid)
            else:
                self.logger.info("WiFi profile saved for '%s': left to NetworkManager's autoconnect", ssid)

    async def _try_station_after_save(self) -> None:
        """The turn a save over the AP asked for: the network just saved goes
        first (`_retry_station`), and the AP comes back at once if it fails."""
        await asyncio.sleep(STATION_TRY_AFTER_SAVE_S)
        try:
            await self._retry_station()
        except Exception as e:
            self.logger.error("The network just saved could not be tried: %s", e)

    async def connect(self, ssid: str, password: Optional[str] = None) -> NetworkStatus:
        """Connect to a WiFi network, without ever losing the one that worked.

        The new profile is tried before anything is removed: written in memory
        with autoconnect off, brought up, and only once it is up written to disk
        and made the SSID's one profile. A try that fails takes nothing with it
        — a mistyped key used to delete the home network's working profile
        first, and leave the unit on its access point. Emits no WebSocket event
        of its own: the status broadcast comes from NM's own property signals,
        through the D-Bus tier below.
        """
        async with self._connect_lock:
            return await self._connect_impl(ssid, password)

    async def _connect_impl(self, ssid: str, password: Optional[str] = None) -> NetworkStatus:
        """Internal connect implementation (called under _connect_lock)."""
        # Choosing a network is choosing the radio: the recovery override must
        # not switch off the link the user just asked for.
        await self._set_radio_override(False)
        self.logger.info("Connecting to WiFi network: %s (password provided: %s)", ssid, password is not None)

        # This connect is the unit becoming reachable; if it fails, the
        # evaluation or the fall-back below decides again.
        self._cancel_recovery_arm()
        try:
            previous = await self._active_station()
        except (RuntimeError, asyncio.TimeoutError) as e:
            self.logger.warning("Could not read which WiFi profile is up before trying '%s': %s", ssid, e)
            previous = None

        try:
            uuid = await self._write_station_profile(ssid, password, trial=True)
        except RuntimeError as e:
            self.logger.error("Failed to create WiFi profile for '%s': %s", ssid, e)
            raise RuntimeError(f"WiFi connection failed: {e}") from e

        # One radio cannot be AP and station: the AP goes before the try, and
        # comes back if the try fails.
        hotspot_was_up = self._hotspot_active
        if hotspot_was_up and not await self._stop_hotspot("switching to station mode"):
            await self._discard_trial(uuid)
            raise RuntimeError(f"WiFi connection to '{ssid}' not tried: the access point holds the radio")

        try:
            rc, _, stderr = await self._run_nmcli(
                "connection", "up", "uuid", uuid, timeout=STATION_UP_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            rc, stderr = None, None
        if rc != 0:
            # NM finishes an activation whose nmcli timed out: the delete is
            # what ends it.
            await self._discard_trial(uuid)
            await self._restore_after_failed_try(previous, hotspot_was_up)
            if rc is None:
                raise RuntimeError(f"WiFi connection to '{ssid}' timed out")
            error_msg = stderr or "Connection failed"
            self.logger.error("WiFi connection to '%s' failed: %s", ssid, error_msg)
            raise RuntimeError(f"WiFi connection failed: {error_msg}")

        self.logger.info("Successfully connected to WiFi: %s", ssid)
        await self._keep_station_profile(ssid, uuid)
        return await self.get_network_status()

    async def _write_station_profile(self, ssid: str, password: Optional[str], *, trial: bool = False) -> str:
        """Add `milo-<ssid>` for wlan0 and return its UUID — the one writer of a
        station profile. psk-flags 0 keeps the key in the profile itself: a
        headless unit has no secret agent to ask. A `trial` profile is kept in
        memory with autoconnect off, until `_keep_station_profile` keeps it.
        Raises RuntimeError with NM's answer."""
        args = ["connection", "add"]
        if trial:
            args += ["save", "no"]
        args += [
            "type", "wifi",
            "ifname", self.WIFI_INTERFACE,
            "con-name", _station_profile_name(ssid),
            "ssid", ssid,
        ]
        if password is not None:
            args += [
                "wifi-sec.key-mgmt", "wpa-psk",
                "wifi-sec.psk", password,
                "wifi-sec.psk-flags", "0",
            ]
        if trial:
            args += ["connection.autoconnect", "no"]

        rc, stdout, stderr = await self._run_nmcli(*args)
        if rc != 0:
            raise RuntimeError(stderr or "Failed to create connection profile")
        match = _ADDED_UUID.search(stdout)
        if match is None:
            raise RuntimeError(f"NetworkManager named no UUID for the new profile: {stdout!r}")
        return match.group(1)

    async def _keep_station_profile(self, ssid: str, uuid: str) -> None:
        """Make a trial profile that came up the SSID's profile: on disk with
        autoconnect on, then the SSID's other profiles removed — in that order,
        so a power cut between the two leaves both rather than neither.

        nmcli saves a modify unless it is `--temporary`, which is what takes an
        in-memory profile to disk; the file NM reports back is checked anyway,
        since the deletion that follows is the one step here that cannot be
        undone, and a profile left in /run is gone at the next reboot."""
        rc, _, stderr = await self._run_nmcli(
            "connection", "modify", "uuid", uuid, "connection.autoconnect", "yes"
        )
        if rc != 0 or not await self._profile_on_disk(uuid):
            self.logger.error(
                "WiFi '%s' joined, but its profile could not be written to disk (%s): the profiles"
                " it was to replace are kept, and it is lost at the next reboot",
                ssid, stderr or "kept in memory",
            )
            return
        with contextlib.suppress(RuntimeError):
            await self._delete_ssid_profiles(ssid, keep=uuid)

    async def _profile_on_disk(self, uuid: str) -> bool:
        rc, stdout, _ = await self._run_nmcli("-t", "-f", "UUID,FILENAME", "connection", "show")
        if rc != 0:
            return False
        for fields in map(_parse_nmcli_line, stdout.split("\n")):
            if len(fields) >= 2 and fields[0] == uuid:
                return bool(fields[1]) and not fields[1].startswith("/run/")
        return False

    async def _discard_trial(self, uuid: str) -> None:
        """Remove a trial profile. One that survives is in memory with
        autoconnect off: nothing joins with it, and it is gone at reboot."""
        try:
            rc, _, stderr = await self._run_nmcli("connection", "delete", "uuid", uuid)
        except asyncio.TimeoutError:
            rc, stderr = None, "timed out"
        if rc not in (0, NMCLI_NO_SUCH_CONNECTION):
            self.logger.warning("The WiFi profile just tried (%s) could not be removed: %s", uuid, stderr)

    async def _restore_after_failed_try(self, previous: Optional[Tuple[str, str]], hotspot_was_up: bool) -> None:
        """Give wlan0 back what it carried before a failed try: the profile that
        was up, asked for at once rather than left to an autoconnect that may
        not come, or the AP — at once too, and under `_retry_station`'s own
        conditions: no hold, and no other link."""
        if previous is not None:
            name, uuid = previous
            try:
                rc, _, stderr = await self._run_nmcli(
                    "connection", "up", "uuid", uuid, timeout=STATION_UP_TIMEOUT_S
                )
            except asyncio.TimeoutError:
                rc, stderr = None, "timed out"
            if rc == 0:
                self.logger.info("WiFi profile '%s' brought back after the failed try", name)
            else:
                self.logger.warning("WiFi profile '%s' could not be brought back: %s", name, stderr or f"rc={rc}")
            return
        if hotspot_was_up and not self._recovery_holds and await self._has_active_connection() is not True:
            self._cancel_recovery_arm()
            await self._start_hotspot()

    async def forget_network(self, ssid: str) -> None:
        """Forget (delete) all saved WiFi connection profiles for this SSID.

        Raises when NetworkManager could not be read or kept a profile: unlike
        a save or a connect, no `connection add` follows to report it, and a
        profile that survives still autoconnects.
        """
        await self._delete_ssid_profiles(ssid)
        self.logger.info("Forgot WiFi network: %s", ssid)

    async def get_saved_networks(self) -> List[SavedNetwork]:
        """List saved WiFi networks, one per SSID — the SSID `forget_network`
        takes back. Raises RuntimeError when the profiles cannot be listed."""
        ssids: dict[str, None] = {}
        for _, _, ssid in await self._wifi_profiles():
            ssids[ssid] = None
        return [SavedNetwork(ssid=ssid) for ssid in ssids]

    async def known_ssids(self) -> Set[str]:
        """The SSID of every WiFi profile NetworkManager holds, the setup
        hotspot's excepted — for the diagnostic report, which must not name them.
        Read as `_wifi_profiles` reads them, each SSID asked for within 2 s."""
        ssids = {ssid for _, _, ssid in await self._wifi_profiles(timeout=2.0)}
        ssids.discard(HOTSPOT_NAME)
        return ssids

    async def _wifi_profiles(self, timeout: float = 10.0) -> List[Tuple[str, str, str]]:
        """(name, UUID, SSID) of every WiFi profile but the unit's own (OWN_PROFILES).

        A `milo-<ssid>` profile is Milō's, named for its SSID. Any other is
        asked for the SSID it carries, by UUID — only the profiles Milō wrote
        follow the naming, a card prepared by Raspberry Pi Imager holds one
        called `preconfigured`, and a name can be shared or be one of nmcli's
        own keywords. One whose SSID cannot be read, or not in time, counts by
        its name, and costs the others nothing. Raises RuntimeError when the
        listing fails.
        """
        rc, stdout, stderr = await self._run_nmcli(
            "-t", "-f", "NAME,UUID,TYPE", "connection", "show", timeout=timeout
        )
        if rc != 0:
            raise RuntimeError(f"Failed to list WiFi profiles: {stderr or f'nmcli exited with code {rc}'}")

        profiles = []
        for line in stdout.split("\n"):
            fields = _parse_nmcli_line(line) if line else []
            if len(fields) >= 3 and "wireless" in fields[2] and fields[0] not in OWN_PROFILES:
                profiles.append((fields[0], fields[1]))

        foreign = [(name, uuid) for name, uuid in profiles if not name.startswith("milo-")]
        answers = await asyncio.gather(*(
            self._run_nmcli(
                "-t", "-f", "802-11-wireless.ssid", "connection", "show", "uuid", uuid,
                timeout=timeout,
            )
            for _, uuid in foreign
        ), return_exceptions=True)
        carried = {}
        for (_, uuid), answer in zip(foreign, answers):
            if not isinstance(answer, BaseException) and answer[0] == 0:
                fields = _parse_nmcli_line(answer[1].split("\n", 1)[0])
                if len(fields) >= 2 and fields[1]:
                    carried[uuid] = fields[1]
        return [(name, uuid, carried.get(uuid) or _ssid_from_profile(name)) for name, uuid in profiles]

    async def _active_station(
        self, timeout: float = 10.0, log_timeout: bool = True
    ) -> Optional[Tuple[str, str]]:
        """(name, UUID) of the station profile wlan0 carries now, or None — the
        unit's own AP or a speaker's hotspot is none. Raises RuntimeError when
        nmcli fails."""
        rc, stdout, stderr = await self._run_nmcli(
            "-t", "-f", "NAME,UUID,DEVICE", "connection", "show", "--active",
            timeout=timeout, log_timeout=log_timeout,
        )
        if rc != 0:
            raise RuntimeError(f"Failed to list the active connections: {stderr or f'rc={rc}'}")
        for line in stdout.split("\n"):
            fields = _parse_nmcli_line(line) if line else []
            if len(fields) >= 3 and fields[2] == self.WIFI_INTERFACE and fields[0] not in OWN_PROFILES:
                return fields[0], fields[1]
        return None

    async def shareable_wifi(
        self, *, tries: int = 1, retry_delay_s: float = SHARE_READ_RETRY_S, timeout_s: float = 10.0,
    ) -> "ServerWifi":
        """The network this server is on, as a peer handed its SSID and key
        would rebuild it — or why there is none to hand over.

        Only the station profile wlan0 carries **now** is read, never a saved
        one: on the recovery AP or a speaker's hotspot there is none, so a
        unit off its network has no key to give. A read that fails (nmcli
        failed or timed out, a ``wpa-psk`` key read back empty) is tried again
        up to `tries` reads in all, `retry_delay_s` apart; each nmcli call is
        bounded by `timeout_s`. Logs nothing above debug: whether a refusal is
        worth a line, and which, is the caller's — a push that reads three
        times would otherwise put three error banners up for one outage.
        """
        answer = ServerWifi(ssid=None, password=None, refusal=WifiShareRefusal.UNREADABLE)
        for attempt in range(1, tries + 1):
            if attempt > 1:
                await asyncio.sleep(retry_delay_s)
            try:
                read = await self._read_active_profile(timeout=timeout_s)
            except WifiKeyUnreadable as unreadable:
                answer = ServerWifi(
                    ssid=unreadable.ssid, password=None, refusal=WifiShareRefusal.UNREADABLE,
                    profile=unreadable.profile_name, key_mgmt="wpa-psk",
                )
                continue
            except (RuntimeError, asyncio.TimeoutError) as failed:
                self.logger.debug("WiFi profile read %d/%d failed: %r", attempt, tries, failed)
                continue
            if read is None:
                return ServerWifi(ssid=None, password=None, refusal=WifiShareRefusal.NO_NETWORK)
            name, profile = read
            if not is_rebuildable_wifi_profile(profile):
                refusal = WifiShareRefusal.HIDDEN if profile["hidden"] else WifiShareRefusal.UNSUPPORTED_SECURITY
                return ServerWifi(
                    ssid=profile["ssid"], password=None, refusal=refusal,
                    profile=name, key_mgmt=profile["key_mgmt"],
                )
            return ServerWifi(
                ssid=profile["ssid"], password=profile["password"], refusal=None,
                profile=name, key_mgmt=profile["key_mgmt"],
            )
        return answer

    async def _read_active_profile(self, timeout: float = 10.0) -> Optional[Tuple[str, dict]]:
        """(name, profile) of the station profile wlan0 carries now, or None
        when there is none. The profile is ``{"ssid", "password", "hidden":
        bool, "key_mgmt": str}``: ``key_mgmt`` is empty for an open network (the
        profile then has no security setting at all), ``wpa-psk`` for WPA2 —
        anything else (SAE alone, 802.1X, WEP) is a network an SSID and a PSK
        cannot rebuild. Read by UUID: a connect being tried holds a second
        profile of the same name.

        Raises RuntimeError (or asyncio.TimeoutError) when nmcli fails, and
        `WifiKeyUnreadable` when the key of a visible ``wpa-psk`` profile reads
        back empty: that is a read that failed, not an open network, and
        handed over it would rebuild the network as an open one. Logs nothing:
        `shareable_wifi`, its one caller, leaves the report to its own.
        """
        active = await self._active_station(timeout=timeout, log_timeout=False)
        if active is None:
            return None
        active_name, active_uuid = active

        rc, stdout, stderr = await self._run_nmcli(
            "-s", "-t",
            "-f", "802-11-wireless.ssid,802-11-wireless.hidden,"
                  "802-11-wireless-security.key-mgmt,802-11-wireless-security.psk",
            "connection", "show", "uuid", active_uuid,
            timeout=timeout, log_timeout=False,
        )
        if rc != 0:
            raise RuntimeError(f"Failed to read WiFi profile '{active_name}': {stderr}")

        ssid: Optional[str] = None
        password: str = ""
        hidden = False
        key_mgmt = ""
        for line in stdout.split("\n"):
            if not line:
                continue
            fields = _parse_nmcli_line(line)
            if len(fields) < 2:
                continue
            key, value = fields[0], fields[1]
            if key == "802-11-wireless.ssid":
                ssid = value
            elif key == "802-11-wireless-security.psk":
                password = value
            elif key == "802-11-wireless.hidden":
                hidden = value == "yes"
            elif key == "802-11-wireless-security.key-mgmt":
                key_mgmt = value

        if not ssid:
            return None
        # A hidden one is still returned: its callers refuse it for being hidden.
        if key_mgmt == "wpa-psk" and not password and not hidden:
            raise WifiKeyUnreadable(active_name, ssid)

        return active_name, {"ssid": ssid, "password": password, "hidden": hidden, "key_mgmt": key_mgmt}

    async def get_country(self) -> str:
        """Return the stored WiFi country code, or empty string if not set."""
        return await self.settings_service.get_setting("wifi.country")

    async def set_country(self, country_code: str) -> None:
        """Apply WiFi regulatory domain and persist the country code.

        Calls the privileged milo-set-wifi-country script (via sudo) to:
          - Run 'iw reg set <CC>' for immediate effect in the current session.
          - Update cfg80211.ieee80211_regdom=<CC> in /boot/firmware/cmdline.txt.

        The caller is responsible for triggering a reboot if needed.

        Serialized, helper and setting together: two calls at once (the setup
        wizard's network step fires one on mount while its picker sends
        another) would run the helper twice concurrently and could persist
        the earlier country last.
        """
        async with self._country_lock:
            self.logger.info("Setting WiFi country code to %s", country_code)

            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "-n", SET_WIFI_COUNTRY_CMD, country_code,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            except asyncio.TimeoutError:
                if proc:
                    proc.kill()
                self.logger.error("milo-set-wifi-country timed out for country %s", country_code)
                raise RuntimeError("WiFi country script timed out")

            if proc.returncode != 0:
                error_msg = stderr.decode().strip() or "Unknown error"
                self.logger.error("milo-set-wifi-country failed (rc=%d): %s", proc.returncode, error_msg)
                raise RuntimeError(f"Failed to set WiFi country: {error_msg}")

            self.logger.info("WiFi country set to %s: %s", country_code, stdout.decode().strip())
            await self.settings_service.set_setting("wifi.country", country_code)

    # =========================================================================
    # Hotspot management
    # =========================================================================

    async def evaluate_reachability(self, status: Optional[NetworkStatus] = None) -> None:
        """Raise or drop the recovery access point. One rule, one owner.

        **The appliance must always be reachable; when it has no way to be, it
        makes one.** That is the whole condition — *no active non-AP
        connection* — and it replaces the first-boot special case this used to
        be (`maybe_start_hotspot(settings_service)`, run once at startup and
        gated on `setup_completed`).

        The special case was measured to fail exactly where it mattered. On the
        hotspot path the wizard *saves* a WiFi profile rather than connecting to
        it — it cannot connect, the AP is the only thing carrying the user's
        browser — so nothing validates the password. A wrong one completed the
        wizard, set `setup_completed`, rebooted, and left a unit NetworkManager
        could not associate: no LAN, no AP (setup was "complete"), and on a
        headless unit no screen either. Unreachable, permanently, with the only
        trace in a journal nobody could read.

        Driven by the same NM D-Bus property changes that already feed
        `network/status_changed`, plus once at startup — so it also covers a
        cable pulled or an access point that dies years later.

        Two things this deliberately does not do. It does not consult
        `setup_completed`: the network layer has no business knowing about the
        wizard, and that coupling *is* the bug above. And it does not treat "no
        internet" as unreachable — `_has_active_connection` asks for a link,
        not for connectivity, so a LAN without a WAN raises nothing.

        The AP is open on purpose: a key would have to be read from a UI that,
        in the one situation this exists for, cannot be reached, and brcmfmac
        offers neither OWE nor SAE in AP mode anyway. What its clients may do
        is the request guard's (`api/middleware.py::RecoveryAccessPointGuard`):
        before setup, the wizard's writes; once the unit is set up, only read,
        and save a WiFi network — any other repair goes through the touch
        screen or Ethernet.
        """
        # A status the caller already computed is a free first look, and it is
        # used in one direction only: `connected` there is sound (ethernet has
        # an IPv4 lease, or wlan0 is associated to something that is not our own
        # AP — `_get_wifi_info` excludes it), so it needs no confirmation.
        # Anything else falls through to the authoritative read below. Drift in
        # this pre-filter can therefore only cost one extra `nmcli`, never a
        # wrong decision — and it keeps the common case, a healthy unit, at the
        # four forks per NM event this path was trimmed down to.
        if status is not None and (status.ethernet.connected or status.wifi.connected):
            connected = True
        else:
            connected = await self._has_active_connection()

        if connected is None:
            return  # nmcli could not say; act on no evidence at all

        if connected:
            self._cancel_recovery_arm()
            self._link_lost = False
            if self._hotspot_active:
                await self._stop_hotspot("a network connection came back")
            await self._restore_radio_preference(status)
            return

        if self._hotspot_active or self._recovery_arm is not None or self._recovery_holds:
            return

        self._recovery_arm = asyncio.create_task(self._raise_recovery_ap_after_delay())

    def _cancel_recovery_arm(self) -> None:
        """Disarm the pending timer, if any."""
        if self._recovery_arm is not None:
            self._recovery_arm.cancel()
            self._recovery_arm = None

    async def _raise_recovery_ap_after_delay(self) -> None:
        """Wait out the settle window, then make the unit reachable.

        The delay is the difference between a recovery channel and a flashing
        AP: a normal boot has no connection for as long as NM takes to
        autoconnect and DHCP to answer, and NM retries a failing profile
        several times before giving up. Raising an AP inside that window would
        take the radio away from the association that was about to succeed.
        """
        try:
            await asyncio.sleep(RECOVERY_AP_DELAY_S)

            # A radio switched off cannot carry an access point, and NM answers
            # the attempt with "No suitable device found" naming the *ethernet*
            # device — measured on the appliance 2026-09-04, with the cable out
            # and the WiFi toggle off. So the radio comes back on first: it is a
            # preference the appliance cannot afford while it is unreachable.
            # A saved network then gets its chance whether or not the radio had
            # to come back: a WiFi-only unit whose box reboots had it on all along.
            if await self._ensure_radio_on() and await self._station_profiles():
                await self._wait_for_autoconnect()

            # Re-checked rather than trusted: the whole point of the wait is
            # that the answer is expected to change during it — and the radio
            # may just have restored the link by itself. `is not False` so an
            # unknown answer holds the AP down too.
            if await self._has_active_connection() is not False:
                return
            await self._start_hotspot()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.error("Recovery access point could not be evaluated: %s", e)
        finally:
            self._recovery_arm = None

    async def _ensure_radio_on(self) -> bool:
        """Turn the WiFi radio back on if it is off, and wait for wlan0.
        True when wlan0 is usable on return — the radio was on, or this call
        turned it on and the device came back.

        The wait is not politeness: `nmcli radio wifi on` returns before NM has
        re-enumerated the device, and an access point activated in that window
        is refused with "No suitable device found for this connection" — which
        names eth0 and reads like a profile bug rather than a timing one.
        """
        try:
            if await self.get_wifi_enabled():
                return await self._wifi_device_available()
        except Exception as e:
            self.logger.warning("Could not read the WiFi radio state: %s", e)
            return False

        self.logger.info(
            "WiFi radio is off and the unit has no link — turning it back on until ethernet returns"
        )
        # The marker before the radio: a process stopped between the two then
        # leaves a marker the restore merely answers with a redundant "off",
        # rather than a radio on with nothing to say whose "on" it is.
        await self._set_radio_override(True)
        try:
            await self._set_radio(True)
        except Exception as e:
            self.logger.error("Could not turn the WiFi radio back on: %s", e)
            return False

        for _ in range(RADIO_SETTLE_POLLS):
            await asyncio.sleep(RADIO_SETTLE_INTERVAL_S)
            if await self._wifi_device_available():
                return True
        self.logger.warning("wlan0 did not come back within the settle window")
        return False

    async def _station_profiles(self) -> List[Tuple[str, str, str]]:
        """(name, UUID, SSID) of the WiFi client profiles that stand for a home
        network, read as `_wifi_profiles` reads them — `milo-` ones first, as
        the wizard and the WiFi panel write them, then any other, Imager's
        `preconfigured` included; never one of the unit's own (OWN_PROFILES).
        By UUID, since two can share a name: a save that asked for a try keeps
        the SSID's other profiles until one joins (`save_network`)."""
        try:
            profiles = await self._wifi_profiles()
        except RuntimeError:
            return []
        return sorted(profiles, key=lambda profile: not profile[0].startswith("milo-"))

    async def _wait_for_autoconnect(self) -> None:
        """Give NM's autoconnect of a saved network its chance on the radio
        that just came back, before the access point claims it."""
        for _ in range(AUTOCONNECT_POLLS):
            await asyncio.sleep(AUTOCONNECT_INTERVAL_S)
            if await self._has_active_connection():
                return

    async def _wifi_device_available(self) -> bool:
        """Whether NM can use wlan0 at all (not `unavailable`/`unmanaged`)."""
        try:
            rc, stdout, _ = await self._run_nmcli("-t", "-f", "DEVICE,STATE", "device", "status")
        except asyncio.TimeoutError:
            return False
        if rc != 0:
            return False
        for line in stdout.split("\n"):
            fields = _parse_nmcli_line(line)
            if len(fields) >= 2 and fields[0] == self.WIFI_INTERFACE:
                return fields[1] not in ("unavailable", "unmanaged")
        return False

    async def _restore_radio_preference(self, status: Optional[NetworkStatus] = None) -> None:
        """Give the user their WiFi setting back, once it costs nothing.

        Only an ethernet lease makes it free. The link in hand may be the WiFi
        station itself, and it may not have its IPv4 yet: NM reads wlan0
        `connected` before DHCP answers, so a test built on the WiFi's lease
        switched the radio off under the association the recovery had just
        made (measured 2026-10-03). The override therefore stands for as long
        as ethernet holds no IPv4, and ends when it does — or when the user
        sets the radio themselves (set_wifi_enabled).
        """
        if not self._radio_forced_on:
            return

        ethernet = status.ethernet if status is not None else await self._get_ethernet_info()
        # Re-read after the await: concurrent evaluations (one per NM event)
        # all pass the first check, and only one may restore.
        if not ethernet.connected or not self._radio_forced_on:
            return
        self._radio_forced_on = False

        try:
            await self._set_radio(False)
        except Exception as e:
            # Still owed: the next evaluation tries again.
            self._radio_forced_on = True
            self.logger.warning("Could not restore the WiFi radio setting: %s", e)
            return
        await self._set_radio_override(False)
        self.logger.info("WiFi radio switched back off — the setting is restored")

    async def _start_hotspot(self) -> None:
        """Bring the AP up. Failing open — a unit with no wlan0 stays silent.

        The AP is owned from before `connection up`, not from its return: NM
        finishes an activation whose nmcli was cancelled or timed out, and a flag
        set afterwards left that AP with nothing to take it down. A failure takes
        the profile with it for the same reason. A cancellation leaves the flag
        up on purpose: it comes from a caller that found the unit reachable and
        runs the teardown next, or from shutdown, after which the next process
        adopts the AP (`_adopt_recovery_state`). A removal NM did not carry out
        keeps the AP owned, with the retry loop armed to take it down again.
        """
        self._hotspot_active = True
        self._hotspot_unread = False
        self._link_lost = True
        self._hotspot_changed()
        try:
            await self._activate_hotspot()
        except Exception as e:
            self.logger.error("Failed to activate the recovery access point: %s", e)
            if not await self._stop_hotspot("it did not come up"):
                self._arm_station_retry()
            return
        self.logger.info(
            "No network connection — access point '%s' raised so the unit stays reachable",
            HOTSPOT_NAME,
        )
        self._arm_station_retry()

    async def _stop_hotspot(self, reason: str) -> bool:
        """Take the AP down; True once it is down. The single teardown —
        `_connect_impl` and the station retry use it too, where dropping the AP
        before switching to station mode is sequencing (one radio), not
        cleanup. It ends the retry loop, unless the loop is the caller.

        The AP stays owned until NM has removed it, the mirror of
        `_start_hotspot` owning it before `connection up`: until then NM still
        carries the AP and its clients, and the recovery guard
        (`api/middleware.py::RecoveryAccessPointGuard`) refuses them only
        while `recovery_ap_possible` holds: `hotspot_active`, the half this
        teardown clears, or an AP not yet read back after a restart. A delete
        NM did not carry out — nmcli timed out, or NM refused — keeps the flag
        and the retry loop, so the next evaluation or the loop's next turn
        takes the AP down again."""
        async with self._hotspot_teardown:
            if not self._hotspot_active:
                return True
            try:
                failure = await self._delete_hotspot_profile()
            except (asyncio.TimeoutError, OSError) as e:
                failure = repr(e)
            if failure is not None:
                self.logger.error(
                    "Access point '%s' kept: NetworkManager did not confirm its removal (%s)"
                    " — taken down again at the next evaluation or retry",
                    HOTSPOT_NAME, failure,
                )
                return False
            self._hotspot_active = False
            self._cancel_station_retry()
        self._hotspot_changed()
        self.logger.info("Access point '%s' removed (%s)", HOTSPOT_NAME, reason)
        return True

    def _arm_station_retry(self) -> None:
        """Start the home-network retry for an AP just raised or adopted. One
        loop at most: one still running goes on with the AP."""
        if self._station_retry is None or self._station_retry.done():
            self._station_retry = asyncio.create_task(self._retry_station_while_hotspot())

    def _cancel_station_retry(self) -> None:
        """End the retry loop — from anywhere but the loop itself, whose own
        teardown of the AP is the start of a try, not the end of the loop."""
        task = self._station_retry
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            self._station_retry = None

    async def _retry_station_while_hotspot(self) -> None:
        """Give the radio back to the home network every STATION_RETRY_S while
        the AP holds it.

        Once the AP was up, only a link coming back took it down, and on a
        WiFi-only unit no link can: the AP holds the one radio, and NM's
        autoconnect "never replaces or competes with an already active profile"
        (`man nm-settings-nmcli`). A box that rebooted for longer than the settle
        window left the unit on its own hotspot until a reboot. A try is held
        off while a client of the AP is served, STATION_RETRY_MAX_HOLDS times
        in a row at most.
        """
        try:
            holds = 0
            while self._hotspot_active:
                waited_from = time.monotonic()
                await asyncio.sleep(STATION_RETRY_S)
                left = self._recovery_client_left
                in_use = self._recovery_clients > 0 or (left is not None and left >= waited_from)
                if in_use and holds < STATION_RETRY_MAX_HOLDS:
                    holds += 1
                    self.logger.info("Home network not tried: a client is using the access point")
                    continue
                holds = 0
                try:
                    await self._retry_station()
                except Exception as e:
                    self.logger.error("The home network could not be tried again: %s", e)
        finally:
            if self._station_retry is asyncio.current_task():
                self._station_retry = None

    async def _retry_station(self) -> None:
        """One try: the AP down, one saved profile asked for by UUID — each in
        turn, as nmcli lists them by name and the first may be a box long gone,
        the one `save_network` wrote last before the others — and the AP back at
        once if it does not join, not after the settle window: the home network
        just had its chance, and the AP is how the unit is reached. A join
        settles the network's profiles (`_settle_station`)."""
        async with self._connect_lock:
            if not self._hotspot_active:
                return
            # A link back by another path owes a teardown, not a try: the
            # evaluation's own could not be carried out, and a unit with no
            # saved network has no try to make anyway.
            if await self._has_active_connection() is True:
                await self._stop_hotspot("a network connection came back")
                return
            profiles = await self._station_profiles()
            if not profiles:
                return
            # The network just saved goes first, wherever the turn stands: saved
            # over the AP, it is the repair of a home network that changed, and
            # the old one's profile, still there, would otherwise take every
            # other turn — each one up to (STATION_RETRY_MAX_HOLDS + 1) ×
            # STATION_RETRY_S apart while a client of the AP holds them off.
            uuids = [uuid for _, uuid, _ in profiles]
            if self._station_saved in uuids:
                index = uuids.index(self._station_saved)
            else:
                index = self._station_tries % len(profiles)
            name, uuid, ssid = profiles[index]

            # An AP NM still carries holds the radio: no station under it.
            if not await self._stop_hotspot(f"trying the home network '{ssid}' again"):
                return
            self._station_saved = None
            self._station_tries = index + 1
            try:
                rc, _, stderr = await self._run_nmcli(
                    "connection", "up", "uuid", uuid, timeout=STATION_UP_TIMEOUT_S
                )
            except asyncio.TimeoutError:
                rc, stderr = None, "timed out"
            if rc == 0:
                self.logger.info("Home network '%s' joined again", ssid)
                await self._settle_station(ssid, uuid)
                return
            self.logger.info("Home network '%s' still out of reach (%s)", ssid, stderr or f"rc={rc}")

            # `is not True`: the AP is down because this try took it down, and
            # an unknown answer must not leave the unit with no way in at all.
            # Under a hold the radio is taken: its release looks again.
            if not self._recovery_holds and await self._has_active_connection() is not True:
                self._cancel_recovery_arm()
                await self._start_hotspot()

    async def _settle_station(self, ssid: str, uuid: str) -> None:
        """`ssid` was joined with profile `uuid`, a key now proven: it becomes
        the SSID's one profile, as a connect's does (`_keep_station_profile`) —
        whoever wrote it and whichever joined, the profile a save over the AP
        left untried or the one it had (`save_network`), and in whatever
        process: nothing about the save is kept in memory, so a restart between
        the save and the join settles the same. By the SSID each profile
        carries (`_delete_ssid_profiles`), so Imager's `preconfigured` counts
        as the network's. Only from a profile on disk: one NM holds in /run
        (a trial a discard missed) is gone at the next reboot, and the ones it
        would replace with it. Best effort: a profile left over is one more NM
        tries."""
        if not await self._profile_on_disk(uuid):
            return
        with contextlib.suppress(RuntimeError):
            await self._delete_ssid_profiles(ssid, keep=uuid)

    async def _settle_active_station(self) -> None:
        """WiFi came up, by whatever path, and the profile wlan0 carries settles
        its network's profiles (`_settle_station`). A try of the retry is one
        way back to the box; NetworkManager's own autoconnect is the other — at
        boot after a power cut, or in the gap a failed try leaves before the AP
        is back — and, settled by the retry alone, the profile a save over the
        AP left untried stayed on disk with its mistyped key, autoconnecting,
        and took a turn in every later outage's rotation. Under the connect
        lock, so a connect or a try in flight has settled first; two reads when
        the network has one profile, which is every join but those."""
        async with self._connect_lock:
            try:
                active = await self._active_station()
                profiles = await self._wifi_profiles() if active is not None else []
            except (RuntimeError, asyncio.TimeoutError) as e:
                self.logger.warning(
                    "Could not read the WiFi profile that came up, its network's other profiles kept: %s",
                    e or "timed out",
                )
                return
            if active is None:
                return
            uuid = active[1]
            ssid = next((carried for _, other, carried in profiles if other == uuid), None)
            if ssid is None or not any(
                carried == ssid and other != uuid for _, other, carried in profiles
            ):
                return
            await self._settle_station(ssid, uuid)

    async def _set_radio_override(self, forced: bool) -> None:
        """Set the radio override, on disk as well: NM keeps the radio on across
        a backend restart, so the next process has to know whose "on" it is."""
        self._radio_forced_on = forced
        try:
            if forced:
                await asyncio.to_thread(WIFI_RADIO_FORCED_FILE.write_bytes, b"")
            else:
                await asyncio.to_thread(WIFI_RADIO_FORCED_FILE.unlink, missing_ok=True)
        except OSError as e:
            self.logger.warning("Could not record the WiFi radio override: %s", e)

    async def _adopt_recovery_state(self) -> None:
        """Take back what a previous process left NetworkManager holding.

        Both recovery effects outlive the process that made them — NM keeps the
        AP up and the radio on across a backend restart — while the flags that
        own them start False, and the teardown and the restore read nothing
        else. An override that cannot be read is not adopted; an AP that cannot
        be read is neither adopted nor ruled out (`_hotspot_unread`), and read
        again once `initialize()` is past the bus.
        """
        try:
            self._radio_forced_on = await asyncio.to_thread(WIFI_RADIO_FORCED_FILE.exists)
        except OSError as e:
            self.logger.warning("Could not read the WiFi radio override back: %s", e)
        if self._radio_forced_on:
            self.logger.info("WiFi radio is on only for the recovery — switched back off once ethernet returns")

        left_up = await self._hotspot_left_up()
        if left_up is None:
            self._hotspot_unread = True
        elif left_up:
            self._adopt_hotspot()

    def _adopt_hotspot(self) -> None:
        self._hotspot_active = True
        self._link_lost = True
        self._hotspot_changed()
        self.logger.info("Access point '%s' is up from a previous run — adopted", HOTSPOT_NAME)

    def _hotspot_changed(self) -> None:
        """Broadcast the status again: `hotspot_active` is part of it, and the AP
        rising or ending moves no link, so the NetworkManager signals that drive
        the broadcast may not follow (deduplicated, a status naming no link
        twice is not sent again). The "WiFi lost" banner reads `link_lost`
        from it on every client, the kiosk's socket included, which outlives
        every network change."""
        self._bg.spawn(self._refresh_and_broadcast(evaluate=False), label="hotspot_status")

    async def _retry_hotspot_adoption(self) -> None:
        """Read again whether NM kept an AP up, after a restart that could not
        tell. An AP adopted here is taken down at once if the link is back: no
        evaluation would otherwise come, the link having nothing left to change.
        An AP raised meanwhile settles the question (`_start_hotspot`).

        It reads until it is answered, more slowly after `ADOPTION_READ_TRIES`.
        Giving up left the question open for the run: an AP really left up
        stayed on air, open and unowned, and one that was never there kept the
        guard shut on the AP's subnet (`RECOVERY_AP_SUBNET`), which a home LAN
        may be."""
        tries = 0
        while True:
            tries += 1
            await asyncio.sleep(
                ADOPTION_READ_RETRY_S if tries <= ADOPTION_READ_TRIES else ADOPTION_READ_SLOW_RETRY_S
            )
            if not self._hotspot_unread:
                return
            left_up = await self._hotspot_left_up(quiet=tries > ADOPTION_READ_TRIES)
            if left_up is not None:
                break
            if tries == ADOPTION_READ_TRIES:
                self.logger.error(
                    "Could not read whether access point '%s' is up after %d tries"
                    " — its clients stay refused by the recovery gate; read again every %d s",
                    HOTSPOT_NAME, tries, ADOPTION_READ_SLOW_RETRY_S,
                )
        if tries > ADOPTION_READ_TRIES:
            self.logger.info("Access point '%s' read at last, after %d tries", HOTSPOT_NAME, tries)
        self._hotspot_unread = False
        if left_up:
            self._adopt_hotspot()
            self._arm_station_retry()
            await self.evaluate_reachability()

    async def _hotspot_left_up(self, quiet: bool = False) -> Optional[bool]:
        """Whether wlan0 carries this unit's own AP, up or still activating —
        NM finishes an activation whether or not its caller is still there.
        None when nmcli could not say: unknown is not "no AP". `quiet` logs no
        failed read, for the slow re-reads an error already announced once: a
        line per tick put a banner on every UI each minute.

        The name alone is not proof: a station profile can carry it too (one
        made by hand to join a speaker's `Milō`), and adopting that would read
        `hotspot_active` on a unit that raised nothing. Only `ap` mode is ours.
        """
        try:
            rc, stdout, _ = await self._run_nmcli(
                "-t", "-f", "TYPE,STATE,CONNECTION", "device", "status", log_timeout=not quiet
            )
            if rc != 0:
                if not quiet:
                    self.logger.warning("nmcli device status failed (rc=%s) — an access point left up is unknown", rc)
                return None
            if not any(
                fields[2] == HOTSPOT_NAME and (fields[1] == "connected" or fields[1].startswith("connecting"))
                for fields in map(_parse_nmcli_line, stdout.split("\n"))
                if len(fields) >= 3
            ):
                return False
            rc, stdout, _ = await self._run_nmcli(
                "-t", "-f", "802-11-wireless.mode", "connection", "show", HOTSPOT_NAME,
                log_timeout=not quiet,
            )
        except (asyncio.TimeoutError, OSError) as e:
            if not quiet:
                self.logger.warning("Could not read back an access point left up: %r", e)
            return None
        if rc != 0:
            if not quiet:
                self.logger.warning("nmcli connection show failed (rc=%s) — an access point left up is unknown", rc)
            return None
        return _parse_nmcli_line(stdout)[-1] == "ap"

    async def _has_active_connection(self) -> Optional[bool]:
        """True/False if any Ethernet or WiFi client (non-AP) connection is
        active — **None when nmcli could not say**.

        The three-valued answer is load-bearing now that this decides whether
        to raise an access point. Folding a failed `nmcli` into False, as it
        used to, means one timed-out call reads as "this unit is unreachable"
        and puts an open AP on a perfectly connected box. Unknown is not
        no-network, and the callers act on neither.
        """
        try:
            rc, stdout, _ = await self._run_nmcli(
                "-t", "-f", "TYPE,STATE,CONNECTION",
                "device", "status"
            )
        except asyncio.TimeoutError:
            return None
        if rc != 0:
            self.logger.warning("nmcli device status failed (rc=%s) — reachability unknown", rc)
            return None
        for line in stdout.split("\n"):
            fields = _parse_nmcli_line(line)
            if len(fields) < 3:
                continue
            device_type, state, connection = fields[0], fields[1], fields[2]
            if state != "connected":
                continue
            # Neither the unit's own AP nor a speaker's hotspot is a link
            if connection in OWN_PROFILES:
                continue
            if device_type in ("ethernet", "wifi"):
                return True
        return False

    async def _activate_hotspot(self) -> None:
        """Create and activate an open (no password) NetworkManager hotspot.
        Raises on failure; `_start_hotspot` removes what it leaves behind."""
        # A profile left by a previous process: NM keeps an in-memory one
        # across a backend restart, and across its own (in /run).
        stale = await self._delete_hotspot_profile()
        if stale is not None:
            self.logger.debug("Hotspot profile cleanup: %s", stale)

        # An open AP profile (nmcli device wifi hotspot always adds WPA, and
        # brcmfmac offers neither OWE nor SAE in AP mode), kept in memory: no
        # boot ever finds it. autoconnect=no is still load-bearing, nmcli
        # defaulting it to yes: the profile outlives a restart of NM, which
        # would raise the AP on its own — behind the backend's back, so
        # `_hotspot_active` stays False and nothing ever tears it down. The AP
        # must only ever exist because this method ran. The address is pinned,
        # not left to `shared`'s pick, because the request guard and the
        # captive DNS answer both name it; and IPv6 is off, so no client
        # reaches a service by its link-local address.
        rc, _, stderr = await self._run_nmcli(
            "connection", "add",
            "save", "no",
            "type", "wifi",
            "ifname", self.WIFI_INTERFACE,
            "con-name", HOTSPOT_NAME,
            "ssid", HOTSPOT_NAME,
            "wifi.mode", "ap",
            "wifi.band", "bg",
            "wifi.channel", "6",
            "ipv4.method", "shared",
            "ipv4.addresses", f"{RECOVERY_AP_ADDRESS}/{RECOVERY_AP_PREFIX}",
            "ipv6.method", "disabled",
            "connection.autoconnect", "no",
            timeout=HOTSPOT_NMCLI_TIMEOUT_S,
        )
        if rc != 0:
            raise RuntimeError(f"Hotspot profile creation failed: {stderr}")

        rc, _, stderr = await self._run_nmcli(
            "connection", "up", HOTSPOT_NAME,
            timeout=HOTSPOT_NMCLI_TIMEOUT_S,
        )
        if rc != 0:
            raise RuntimeError(f"Hotspot activation failed: {stderr}")

    # =========================================================================
    # Internal helpers
    # =========================================================================

    async def _get_ethernet_info(self) -> EthernetStatus:
        """Return ethernet connection status.

        connected=True requires both an active NM profile AND an IPv4 address,
        so the badge matches what the Avahi dispatcher uses to advertise on
        eth0 (`has_ip eth0`). This prevents reporting "connected" during the
        DHCP activation window when milo.local is not yet resolvable on eth0.
        """
        rc, stdout, _ = await self._run_nmcli(
            "-t", "-f", "GENERAL.CONNECTION,IP4.ADDRESS",
            "device", "show", "eth0"
        )
        if rc != 0:
            return EthernetStatus(connected=False)

        connection = None
        ip_address = None
        for line in stdout.split("\n"):
            key, _, value = line.partition(":")
            value = value.strip()
            if key == "GENERAL.CONNECTION":
                connection = value if value and value != "--" else None
            elif key.startswith("IP4.ADDRESS"):
                ip_address = value.split("/")[0] if value and value != "--" else None

        connected = connection is not None and ip_address is not None
        return EthernetStatus(
            connected=connected,
            ip_address=ip_address if connected else None,
        )

    async def _get_wifi_info(self) -> WifiConnectionStatus:
        """Get WiFi connection status."""
        rc, stdout, _ = await self._run_nmcli(
            "-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS",
            "device", "show", self.WIFI_INTERFACE
        )

        if rc != 0:
            return WifiConnectionStatus(connected=False)

        state = None
        connection = None
        ip_address = None

        for line in stdout.split("\n"):
            key, _, value = line.partition(":")
            value = value.strip()
            if key == "GENERAL.STATE":
                # "70 (connecting (getting IP configuration))"
                code = value.split(" ", 1)[0]
                state = int(code) if code.isdigit() else None
            elif key == "GENERAL.CONNECTION":
                connection = value if value and value != "--" else None
            elif key.startswith("IP4.ADDRESS"):
                ip_address = value.split("/")[0] if value and value != "--" else None

        saved = await self._get_saved_ssid()

        if state != NM_DEVICE_STATE_ACTIVATED or ip_address:
            self._lease_wait_since = None

        # Neither the unit's own AP nor a speaker's hotspot is a network it joined.
        if not connection or connection in OWN_PROFILES:
            return WifiConnectionStatus(connected=False, saved_ssid=saved)
        if not ip_address:
            # Not connected until the IPv4 lease: the Avahi dispatcher won't
            # advertise on wlan0 before `has_ip wlan0`, and the badge follows
            # reachability. NM states 40 (prepare) to 90 (secondaries) are a
            # join in progress, and so is 100 while DHCP has time left.
            if state == NM_DEVICE_STATE_ACTIVATED:
                connecting = self._awaiting_lease()
            else:
                connecting = state is not None and NM_DEVICE_STATE_PREPARE <= state < NM_DEVICE_STATE_ACTIVATED
            ssid = None
            if connecting:
                ssid, _ = await self._read_active_ap_info()
                ssid = ssid or _ssid_from_profile(connection)
            return WifiConnectionStatus(
                connected=False, connecting=connecting, ssid=ssid, saved_ssid=saved,
            )

        # Read live SSID + Strength directly from the cached AP D-Bus proxy.
        # `nmcli dev wifi` was the old source here, but its default --rescan auto
        # could stall the refresh for several seconds during an implicit scan,
        # which serialised behind every NM PropertiesChanged event.
        ssid, signal = await self._read_active_ap_info()
        if not ssid:
            # Fail-open: D-Bus init didn't take, or AP not yet anchored.
            # Derive a usable SSID from the milo-prefixed connection name.
            ssid = _ssid_from_profile(connection)

        return WifiConnectionStatus(
            connected=True,
            ssid=ssid,
            ip_address=ip_address,
            signal=signal,
            saved_ssid=saved,
        )

    def _awaiting_lease(self) -> bool:
        """Whether an activated wlan0 is still inside NM's DHCP window. The
        first sighting schedules one refresh at its end, so the badge leaves
        "Connecting…" by itself if no lease ever comes."""
        now = asyncio.get_running_loop().time()
        if self._lease_wait_since is None:
            self._lease_wait_since = now
            self._bg.spawn(self._refresh_after(DHCP_LEASE_WAIT_S), label="lease_wait_expiry")
        return now - self._lease_wait_since < DHCP_LEASE_WAIT_S

    async def _refresh_after(self, delay: float) -> None:
        await asyncio.sleep(delay)
        await self._refresh_and_broadcast()

    async def _get_saved_ssid(self) -> Optional[str]:
        """Return the SSID of the first milo-managed WiFi profile, or None."""
        rc, stdout, _ = await self._run_nmcli(
            "-t", "-f", "NAME,TYPE", "connection", "show"
        )
        if rc != 0:
            return None
        for line in stdout.split("\n"):
            if not line:
                continue
            fields = _parse_nmcli_line(line)
            if len(fields) >= 2 and "wireless" in fields[1]:
                name = fields[0]
                if name.startswith("milo-"):
                    return _ssid_from_profile(name)
        return None

    async def _delete_ssid_profiles(self, ssid: str, keep: Optional[str] = None) -> None:
        """Delete every station profile of an SSID but the one whose UUID is `keep`.

        A profile counts by the SSID it stands for (`_wifi_profiles`), so one
        written by hand, by Imager or by `nmcli device wifi connect` goes with
        Milō's own: left behind, NM may pick its stale key. Deleted by UUID. A
        refused delete does not stop the others; RuntimeError once they are
        all tried if the listing failed or a profile survived.
        """
        try:
            profiles = await self._wifi_profiles()
        except (RuntimeError, asyncio.TimeoutError) as e:
            # A warning: a forget's caller logs the raise at error, and the
            # network a save or a connect wrote is already in place.
            self.logger.warning("Could not list WiFi profiles before deleting '%s': %s", ssid, e or "timed out")
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError("Failed to list WiFi profiles: timed out") from e

        survivors = []
        for name, uuid, carried in profiles:
            if carried != ssid or uuid == keep:
                continue
            self.logger.info("Deleting WiFi profile: %s (ssid=%s)", name, ssid)
            try:
                rc, _, stderr = await self._run_nmcli("connection", "delete", "uuid", uuid)
            except asyncio.TimeoutError:
                rc, stderr = None, "timed out"
            if rc == 0:
                self.logger.info("Deleted WiFi profile: %s", name)
            else:
                self.logger.error("Failed to delete WiFi profile %s: %s", name, stderr)
                survivors.append(name)
        if survivors:
            raise RuntimeError(f"Failed to delete WiFi profile(s) {', '.join(survivors)} for '{ssid}'")

    async def _delete_hotspot_profile(self) -> Optional[str]:
        """Remove the hotspot NM connection profile. None once NM holds no such
        profile — deleted, or missing, the normal answer to the stale-profile
        clear-out — else what NM answered. Raises asyncio.TimeoutError."""
        rc, _, stderr = await self._run_nmcli(
            "connection", "delete", HOTSPOT_NAME
        )
        if rc in (0, NMCLI_NO_SUCH_CONNECTION):
            return None
        return stderr or f"rc={rc}"

    # =========================================================================
    # NetworkManager D-Bus subscription (live status updates)
    # =========================================================================

    async def initialize(self) -> bool:
        """Adopt the recovery state NM still holds, then connect to NM via
        D-Bus, subscribe to the three tiers, prime cache.

        The adoption comes first: the priming spawns the first evaluation, which
        must already see an AP left up. It is an nmcli read, so it stands outside
        the bus's `try` and does not fail with it. An adopted AP's home-network
        retry, or the read again of one that could not be read, starts last,
        after the bus's fate: a bus that fails runs `cleanup()`, which would
        end it.

        Returns True if at least the system bus connection succeeded. Even
        when individual interfaces are missing (e.g. boards without wlan0),
        we still consider initialize() successful as long as the bus came up.
        Fails open: if NM is unavailable, returns False and live updates are
        disabled. The nmcli-based read path keeps working.
        """
        await self._adopt_recovery_state()
        try:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

            await self._subscribe_device_base("eth0")
            await self._subscribe_device_base(self.WIFI_INTERFACE)
            await self._subscribe_wireless()
            await self._reanchor_active_ap()
            await self._refresh_and_broadcast()

            self.logger.info(
                "Network service ready (devices=%s, wireless=%s, ap=%s)",
                list(self._device_listeners),
                "yes" if self._wireless_listener else "no",
                self._ap_path or "none",
            )
            return True
        except Exception as exc:
            self.logger.warning(
                "NetworkManager D-Bus unavailable, network status will lack live updates: %s",
                exc,
            )
            await self.cleanup()
            return False
        finally:
            if self._hotspot_active:
                self._arm_station_retry()
            elif self._hotspot_unread:
                self._bg.spawn(self._retry_hotspot_adoption(), label="hotspot_adoption_read")

    async def cleanup(self) -> None:
        """Detach every listener and disconnect the bus. Idempotent."""
        self._cancel_recovery_arm()
        self._cancel_station_retry()
        await self._bg.cancel_all()
        self._ap_proxy = None
        self._ap_path = None

        if self._wireless_listener is not None:
            properties_iface, handler = self._wireless_listener
            with contextlib.suppress(Exception):
                properties_iface.off_properties_changed(handler)
            self._wireless_listener = None

        for iface_name in list(self._ip4_listener):
            await self._detach_ip4(iface_name)
        self._ip4_path.clear()

        for _, (properties_iface, handler) in self._device_listeners.items():
            with contextlib.suppress(Exception):
                properties_iface.off_properties_changed(handler)
        self._device_listeners.clear()
        self._device_proxies.clear()

        self._wlan_proxy = None

        if self._bus is not None:
            with contextlib.suppress(Exception):
                self._bus.disconnect()
            self._bus = None

    async def _resolve_device_path(self, iface_name: str) -> Optional[str]:
        """Ask NM for the D-Bus object path of a given network interface."""
        try:
            introspect = await self._bus.introspect(NM_SERVICE, NM_PATH)
            nm_proxy = self._bus.get_proxy_object(NM_SERVICE, NM_PATH, introspect)
            nm_iface = nm_proxy.get_interface(NM_IFACE)
            return await nm_iface.call_get_device_by_ip_iface(iface_name)
        except Exception as exc:
            self.logger.debug("NM device lookup for %s failed: %s", iface_name, exc)
            return None

    # ---- Tier 1: base Device interface (eth0 + wlan0) ----

    async def _subscribe_device_base(self, iface_name: str) -> None:
        path = await self._resolve_device_path(iface_name)
        if not path:
            self.logger.info(
                "Skipping NM D-Bus subscription for %s (interface not present)",
                iface_name,
            )
            return

        introspect = await self._bus.introspect(NM_SERVICE, path)
        proxy = self._bus.get_proxy_object(NM_SERVICE, path, introspect)
        properties_iface = proxy.get_interface(DBUS_PROPERTIES_IFACE)
        handler = self._make_device_base_handler(iface_name)
        properties_iface.on_properties_changed(handler)
        self._device_listeners[iface_name] = (properties_iface, handler)
        self._device_proxies[iface_name] = proxy

        if iface_name == self.WIFI_INTERFACE:
            self._wlan_proxy = proxy

        # Anchor the IP4Config listener so DHCP-lease updates surface as events.
        await self._reanchor_ip4(iface_name)

    def _make_device_base_handler(self, iface_name: str):
        def _handler(iface: str, changed: dict, _invalidated: list) -> None:
            if iface != NM_DEVICE_IFACE:
                return
            if not (changed.keys() & _DEVICE_BASE_PROPS):
                return
            if "Ip4Config" in changed:
                self._bg.spawn(self._reanchor_ip4(iface_name), label="reanchor_ip4")
            self._bg.spawn(self._refresh_and_broadcast(), label="refresh_and_broadcast")
        return _handler

    # ---- Tier 1b: IP4Config object (re-anchored on Device.Ip4Config change) ----

    async def _reanchor_ip4(self, iface_name: str) -> None:
        """Re-attach the IP4Config listener if Device.Ip4Config path changed."""
        proxy = self._device_proxies.get(iface_name)
        if proxy is None:
            return
        async with self._listener_setup_lock:
            try:
                device_iface = proxy.get_interface(NM_DEVICE_IFACE)
                new_path = await device_iface.get_ip4_config()
            except Exception as exc:
                self.logger.debug("Failed to read %s Ip4Config path: %s", iface_name, exc)
                return

            if new_path == self._ip4_path.get(iface_name):
                return

            await self._detach_ip4(iface_name)
            self._ip4_path[iface_name] = new_path

            if new_path and new_path != "/":
                await self._attach_ip4(iface_name, new_path)

        # Path change is itself a status change — refresh now.
        self._bg.spawn(self._refresh_and_broadcast(), label="refresh_and_broadcast")

    async def _attach_ip4(self, iface_name: str, path: str) -> None:
        try:
            introspect = await self._bus.introspect(NM_SERVICE, path)
            proxy = self._bus.get_proxy_object(NM_SERVICE, path, introspect)
            properties_iface = proxy.get_interface(DBUS_PROPERTIES_IFACE)
            handler = self._make_ip4_handler()
            properties_iface.on_properties_changed(handler)
            self._ip4_listener[iface_name] = (properties_iface, handler)
        except Exception as exc:
            self.logger.debug("Failed to attach IP4 listener for %s at %s: %s", iface_name, path, exc)

    async def _detach_ip4(self, iface_name: str) -> None:
        listener = self._ip4_listener.pop(iface_name, None)
        if listener is None:
            return
        properties_iface, handler = listener
        with contextlib.suppress(Exception):
            properties_iface.off_properties_changed(handler)

    def _make_ip4_handler(self):
        def _handler(iface: str, changed: dict, _invalidated: list) -> None:
            if iface != NM_IP4_CONFIG_IFACE:
                return
            if not (changed.keys() & _IP4_CONFIG_PROPS):
                return
            self._bg.spawn(self._refresh_and_broadcast(), label="refresh_and_broadcast")
        return _handler

    # ---- Tier 2: Device.Wireless (wlan0 only) ----

    async def _subscribe_wireless(self) -> None:
        if self._wlan_proxy is None:
            return
        properties_iface = self._wlan_proxy.get_interface(DBUS_PROPERTIES_IFACE)
        properties_iface.on_properties_changed(self._on_wireless_props_changed)
        self._wireless_listener = (properties_iface, self._on_wireless_props_changed)

    def _on_wireless_props_changed(self, iface: str, changed: dict, _invalidated: list) -> None:
        if iface != NM_DEVICE_WIRELESS_IFACE:
            return
        if "ActiveAccessPoint" not in changed:
            return
        self._bg.spawn(self._reanchor_active_ap(), label="reanchor_active_ap")

    # ---- AccessPoint proxy (re-anchored on AP change, not subscribed) ----

    async def _read_active_ap_path(self) -> Optional[str]:
        if self._wlan_proxy is None:
            return None
        try:
            wireless_iface = self._wlan_proxy.get_interface(NM_DEVICE_WIRELESS_IFACE)
            return await wireless_iface.get_active_access_point()
        except Exception as exc:
            self.logger.debug("Failed to read ActiveAccessPoint: %s", exc)
            return None

    async def _reanchor_active_ap(self) -> None:
        """Drop the old AP proxy (if any), anchor the new ActiveAccessPoint."""
        async with self._listener_setup_lock:
            new_path = await self._read_active_ap_path()
            if new_path == self._ap_path:
                return

            self._ap_proxy = None
            self._ap_path = new_path

            if new_path and new_path != "/":
                await self._anchor_ap_proxy(new_path)

        # AP change is itself a status change — refresh now.
        self._bg.spawn(self._refresh_and_broadcast(), label="refresh_and_broadcast")

    async def _anchor_ap_proxy(self, path: str) -> None:
        """Cache a proxy on the associated AP so its props can be read on demand."""
        try:
            introspect = await self._bus.introspect(NM_SERVICE, path)
            self._ap_proxy = self._bus.get_proxy_object(NM_SERVICE, path, introspect)
        except Exception as exc:
            self.logger.debug("Failed to anchor AP proxy at %s: %s", path, exc)
            self._ap_proxy = None

    async def get_wifi_signal(self) -> Optional[int]:
        """Live RSSI (0-100) of the associated AP, or None when not associated.

        One D-Bus read on the anchored proxy, no nmcli — the point of splitting
        it from get_network_status(), which forks four. Called only by
        GET /api/network/wifi/signal, i.e. only while a client is showing the
        signal arc. Fails open (None) with no proxy, like every other D-Bus
        path here: a dev host without NM gets a greyed-out icon, not an error.
        """
        _, signal = await self._read_active_ap_info()
        return signal

    async def read_link(self, iface_name: str) -> Optional[InterfaceLink]:
        """The link `iface_name` negotiated, read over D-Bus; None when unknown.

        Wi-Fi reports Device.Wireless.Bitrate, the rate this link negotiated —
        not the AP's MaxBitrate, which is what `nmcli dev wifi` prints as RATE —
        and never triggers a scan. None with no bus (a dev host), for an
        interface NM does not manage, or for a type that is neither.
        """
        if self._bus is None:
            return None
        path = await self._resolve_device_path(iface_name)
        if not path:
            return None
        try:
            introspect = await self._bus.introspect(NM_SERVICE, path)
            proxy = self._bus.get_proxy_object(NM_SERVICE, path, introspect)
            device_type = await proxy.get_interface(NM_DEVICE_IFACE).get_device_type()
            if device_type == NM_DEVICE_TYPE_ETHERNET:
                wired = proxy.get_interface(NM_DEVICE_WIRED_IFACE)
                speed, carrier = await asyncio.gather(wired.get_speed(), wired.get_carrier())
                return InterfaceLink("ethernet", float(speed) if carrier and speed > 0 else None, None)
            if device_type == NM_DEVICE_TYPE_WIFI:
                wireless = proxy.get_interface(NM_DEVICE_WIRELESS_IFACE)
                bitrate_kbps, ap_path = await asyncio.gather(
                    wireless.get_bitrate(), wireless.get_active_access_point()
                )
                return InterfaceLink(
                    "wifi",
                    bitrate_kbps / 1000.0 if bitrate_kbps > 0 else None,
                    await self._read_strength(ap_path),
                )
            return None
        except Exception as exc:
            self.logger.warning("Could not read the link of %s over D-Bus: %s", iface_name, exc)
            return None

    async def _read_strength(self, ap_path: str) -> Optional[int]:
        if not ap_path or ap_path == "/":
            return None
        introspect = await self._bus.introspect(NM_SERVICE, ap_path)
        ap = self._bus.get_proxy_object(NM_SERVICE, ap_path, introspect)
        return int(await ap.get_interface(NM_ACCESS_POINT_IFACE).get_strength())

    async def _read_active_ap_info(self) -> Tuple[Optional[str], Optional[int]]:
        """Return (ssid, strength) from the cached AP proxy. (None, None) on miss."""
        if self._ap_proxy is None:
            return None, None
        try:
            ap_iface = self._ap_proxy.get_interface(NM_ACCESS_POINT_IFACE)
            ssid_bytes, strength = await asyncio.gather(
                ap_iface.get_ssid(),
                ap_iface.get_strength(),
            )
            ssid = bytes(ssid_bytes).decode("utf-8", errors="replace") or None
            return ssid, int(strength)
        except Exception as exc:
            self.logger.debug("Failed to read AP info: %s", exc)
            return None, None

    async def _refresh_and_broadcast(self, *, evaluate: bool = True) -> None:
        """Re-read full network status and broadcast it, dedup against last sent.

        The WS broadcast runs OUTSIDE the lock: a slow client would otherwise
        keep the lock held while D-Bus signal handlers pile up new refresh
        tasks behind it. The link listeners run inside it, once per gained
        link, so they hold the next refresh for as long as they write.
        `evaluate` False when the AP itself moved
        (`_hotspot_changed`): the evaluation is what raised or removed it, and
        one run again from there would raise a failed AP again at once, forever.
        """
        async with self._broadcast_lock:
            try:
                status = await self.get_network_status()
            except Exception as exc:
                self.logger.error("Failed to read network status for broadcast: %s", exc)
                return

            # Before the dedup, deliberately: an unchanged status is exactly
            # what a unit stuck with no link looks like, and returning early
            # there would mean the rule only ever sees transitions.
            if evaluate:
                self._bg.spawn(self.evaluate_reachability(status), label="reachability")

            if status == self._last_broadcast:
                return

            previous = self._last_broadcast
            self._last_broadcast = status

            # After the dedup, deliberately: this costs one IPv4 request and a
            # link that has not changed cannot have moved the uplink. NM only
            # emits connectivity when its own family-agnostic verdict moves, so
            # a link change is the appliance's only chance to notice an IPv4
            # path that died while IPv6 kept the verdict green.
            if self._connectivity_service is not None:
                self._bg.spawn(
                    self._connectivity_service.recheck("link change"),
                    label="connectivity_recheck",
                )

            # The server's WiFi came up or moved to another network: every
            # satellite keeps it as a standby link. A reconnection to the same
            # network counts too — it is how a changed password reaches them.
            joined = status.wifi.connected and not (
                previous is not None and previous.wifi.connected
                and previous.wifi.ssid == status.wifi.ssid
            )
            if joined:
                self._bg.spawn(self._settle_active_station(), label="station_settle")
            if joined and self._satellite_wifi_service is not None:
                self._bg.spawn(self._satellite_wifi_service.push_all(), label="satellite_wifi")

            # Under the lock, and before the broadcast (`add_link_listener`): a
            # newer refresh run meanwhile — NM signals a link in bursts — sent
            # its own linked status while the listener still wrote, and this
            # one, superseded, sent nothing after the write.
            if status.linked and not (previous is not None and previous.linked):
                for listener in self._link_listeners:
                    try:
                        await listener()
                    except Exception as e:
                        self.logger.error("A listener to the link that came up failed: %s", e)

        await self.state_machine.broadcast(
            NetworkStatusChanged(**status.model_dump())
        )

    # =========================================================================
    # Private helpers
    # =========================================================================

    async def _run_nmcli(
        self, *args: str, timeout: float = 10.0, log_timeout: bool = True
    ) -> Tuple[int, str, str]:
        """Run an nmcli command asynchronously with timeout.

        Returns (returncode, stdout, stderr).
        Raises asyncio.TimeoutError if the command exceeds the timeout, logged
        at error unless the caller reports the outage itself (`log_timeout`).
        """
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "nmcli", *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
            return proc.returncode, stdout.decode().strip(), stderr.decode().strip()
        except asyncio.TimeoutError:
            if proc:
                proc.kill()
            if log_timeout:
                self.logger.error("nmcli timed out: nmcli %s", _redact_nmcli_args(args))
            raise


def _redact_nmcli_args(args: Tuple[str, ...]) -> str:
    """The argv as a log line, with the value of every `*.psk` / `*password`
    key replaced by `***` (a `-f` field list, comma-joined, is not a key).

    A logged line lands in the world-readable errors.log, every UI's error
    banner and the diagnostic report, none of them a place for a secret —
    the rule `api/system.py::set_device_password` applies to argv itself.
    """
    return " ".join(
        "***" if prev.endswith((".psk", "password")) and "," not in prev else arg
        for prev, arg in zip(("", *args), args)
    )


def _parse_nmcli_line(line: str) -> List[str]:
    """Parse a line from nmcli -t output, handling escaped characters.

    nmcli terse mode uses ':' as field separator and escapes literal
    colons as '\\:' and backslashes as '\\\\'.
    """
    fields = []
    current: list[str] = []
    i = 0
    while i < len(line):
        if line[i] == "\\" and i + 1 < len(line):
            current.append(line[i + 1])
            i += 2
        elif line[i] == ":":
            fields.append("".join(current))
            current = []
            i += 1
        else:
            current.append(line[i])
            i += 1
    fields.append("".join(current))
    return fields


class WifiKeyUnreadable(Exception):
    """The active profile is a visible ``wpa-psk`` one whose key read back empty.

    NetworkManager answers rc 0 with an empty PSK when it cannot authorize the
    secret read — measured during a reboot, polkit already stopped — so the
    profile is sound and a later read may well return its key.
    """

    def __init__(self, profile_name: str, ssid: Optional[str] = None):
        super().__init__(profile_name)
        self.profile_name = profile_name
        self.ssid = ssid


class WifiShareRefusal(str, enum.Enum):
    """Why `NetworkService.shareable_wifi` has no network to hand over."""
    # wlan0 carries no station profile: Ethernet only, the radio off, the
    # recovery AP or a speaker's hotspot up.
    NO_NETWORK = "no_network"
    # The profile could not be read on any try: nmcli failed or timed out, or
    # a wpa-psk key read back empty.
    UNREADABLE = "unreadable"
    # A hidden network is never probed for by the peer that rebuilds it.
    HIDDEN = "hidden"
    # SAE alone, 802.1X, WEP: refused by a PSK (`key_mgmt` says which).
    UNSUPPORTED_SECURITY = "unsupported_security"


@dataclass(frozen=True)
class ServerWifi:
    """`NetworkService.shareable_wifi`'s answer. Shareable iff `refusal` is None,
    and `password` (``""`` for an open network) is set exactly then. `ssid` is
    None for NO_NETWORK and for an UNREADABLE whose profile never answered;
    `profile` names the NM profile read, for a log line."""
    ssid: Optional[str]
    password: Optional[str]
    refusal: Optional[WifiShareRefusal]
    profile: Optional[str] = None
    key_mgmt: str = ""


def is_rebuildable_wifi_profile(profile: dict) -> bool:
    """Whether a peer handed this network's SSID and PSK alone joins it.

    A satellite or an adopted speaker writes what it is given as a visible
    ``wpa-psk`` profile (or an open one, for an empty key): a hidden network is
    never probed for, and SAE alone, 802.1X or WEP refuses the PSK, so the peer
    would keep a profile that never associates.
    """
    return not profile["hidden"] and profile["key_mgmt"] in ("", "wpa-psk")


def _station_profile_name(ssid: str) -> str:
    """The name of the profile Milō writes for `ssid` (`_write_station_profile`)."""
    return f"milo-{ssid}"


def _ssid_from_profile(connection: str) -> str:
    """The SSID a `milo-<ssid>` profile names (save_network's naming contract),
    or the profile name itself for one Milō did not write."""
    return connection[5:] if connection.startswith("milo-") else connection
