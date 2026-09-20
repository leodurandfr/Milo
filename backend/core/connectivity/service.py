# backend/core/connectivity/service.py
"""
Internet connectivity monitoring via NetworkManager (D-Bus).

Subscribes to org.freedesktop.NetworkManager's PropertiesChanged signal on
the Connectivity property. NetworkManager performs its own probe of a known
endpoint (configured in /etc/NetworkManager/conf.d/99-milo-connectivity.conf),
periodically and on every interface state change, so the backend gets
event-driven updates without polling.

NM Connectivity enum (from nm-dbus-interface.h):
    0 = UNKNOWN
    1 = NONE       (no network)
    2 = PORTAL     (captive portal intercepting traffic)
    3 = LIMITED    (LAN reachable but no internet)
    4 = FULL       (internet reachable)

The level is published whole rather than flattened to a boolean: LIMITED is
literally "LAN up, no internet", which is the difference between a broken
AirPlay and a working one, and collapsing it is what made the old offline
banner fire while listening over Bluetooth. Fails open: if D-Bus or
NetworkManager is unavailable (e.g. dev environment, NM down), the service
stays at UNKNOWN, which every consumer treats as FULL — never report a
problem we have not observed.

The initial read is the cached property (kept fast and non-blocking, since
this runs inside the backend's startup gather), but the cached value can
still be UNKNOWN right after boot if NM's own periodic/interface-triggered
check hasn't run yet — reading it as-is produced a false "offline" banner on
every reboot. A background task forces one fresh NM probe (CheckConnectivity)
shortly after startup and broadcasts a correction if it disagrees, so the UI
self-corrects within seconds instead of waiting up to NM's 5-minute recheck
interval.
"""
import asyncio
import logging
import socket
from typing import Optional

import aiohttp
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType
from dbus_next.signature import Variant

from backend.core.models.audio_state import ConnectivityLevel
from backend.core.models.ws_events import SystemConnectivityChanged
from backend.shared.background import BackgroundTaskSet

logger = logging.getLogger(__name__)

NM_SERVICE = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
NM_IFACE = "org.freedesktop.NetworkManager"
DBUS_PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

# NM's integer property → our level. Anything outside the enum (a value NM
# gained after this was written) reads as UNKNOWN, i.e. fail open.
NM_LEVELS = {
    1: ConnectivityLevel.NONE,
    2: ConnectivityLevel.PORTAL,
    3: ConnectivityLevel.LIMITED,
    4: ConnectivityLevel.FULL,
}

NM_CHECK_CONNECTIVITY_TIMEOUT = 15  # seconds; bounds the background forced probe

# The endpoint NM itself probes, declared in
# /etc/NetworkManager/conf.d/99-milo-connectivity.conf (written by
# pi-gen/stage-milo/02-install-milo/01-run.sh, which is the authority). Restated
# here because that drop-in is not readable as configuration from this process.
#
# We re-fetch it forced to IPv4, because NM's own fetch is family-agnostic and a
# dual-stack host answers it over IPv6. Measured 2026-09-20: the wired gateway
# stopped answering ARP, IPv6 failed over to wifi on its own, NM's probe
# succeeded over IPv6 and reported FULL — while every IPv4-only stream
# (icecast.radiofrance.fr has no AAAA) and every podcast feed was timing out.
# Milo published "internet is back" for the remaining 18 minutes of the outage.
IPV4_PROBE_URI = "http://nmcheck.gnome.org/check_network_status.txt"
IPV4_PROBE_TIMEOUT = 3.0

# NM has no reason to emit anything while we hold a downgrade against it — its
# own value stays FULL — so our own re-probe is the only thing that can lift it.
# Without this loop a held downgrade would block every internet source for ever.
HOLD_RECHECK_START_S = 20.0
HOLD_RECHECK_MAX_S = 120.0


class ConnectivityService:
    """Tracks NetworkManager connectivity and broadcasts state changes."""

    def __init__(self):
        self._state_machine = None
        self._bus: Optional[MessageBus] = None
        self._properties_iface = None
        self._nm_iface = None
        self._level: ConnectivityLevel = ConnectivityLevel.UNKNOWN  # Fail-open default
        # What NM last told us, before our IPv4 confirmation. Kept apart from
        # _level so the hold loop knows it is still contradicting NM.
        self._nm_level: ConnectivityLevel = ConnectivityLevel.UNKNOWN
        self._holding: bool = False
        self._hold_running: bool = False
        self._listener_attached: bool = False
        self._bg = BackgroundTaskSet(logger, "connectivity")

    def set_state_machine(self, state_machine) -> None:
        self._state_machine = state_machine

    def get_state(self) -> dict:
        return {"connectivity": self._level.value}

    @property
    def level(self) -> ConnectivityLevel:
        return self._level

    async def initialize(self) -> bool:
        """Connect to system bus, read initial (cached) state, subscribe to
        changes, then schedule a background forced re-check — kept off this
        method's critical path since it runs inside the backend's startup
        gather and must not delay boot when the network is genuinely down."""
        try:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            introspect = await self._bus.introspect(NM_SERVICE, NM_PATH)
            proxy = self._bus.get_proxy_object(NM_SERVICE, NM_PATH, introspect)
            nm_iface = proxy.get_interface(NM_IFACE)
            self._properties_iface = proxy.get_interface(DBUS_PROPERTIES_IFACE)

            self._nm_iface = nm_iface

            connectivity = await nm_iface.get_connectivity()
            self._level = NM_LEVELS.get(connectivity, ConnectivityLevel.UNKNOWN)

            self._properties_iface.on_properties_changed(self._on_properties_changed)
            self._listener_attached = True

            logger.info(
                "Connectivity service ready (initial=%s, NM=%s)",
                self._level.value,
                connectivity,
            )
            self._bg.spawn(self._recheck_fresh(nm_iface), label="initial_recheck")
            return True
        except Exception as exc:
            logger.warning(
                "NetworkManager D-Bus unavailable, connectivity stays unknown "
                "(treated as reachable): %s",
                exc,
            )
            self._level = ConnectivityLevel.UNKNOWN
            return False

    async def _recheck_fresh(self, nm_iface) -> None:
        """Force NM to re-probe once, since the cached property read at
        startup can still be UNKNOWN right after boot, before NM's own
        periodic/interface-triggered check has had a chance to run. Runs as
        a background task so a slow or failed probe never delays boot."""
        try:
            connectivity = await asyncio.wait_for(
                nm_iface.call_check_connectivity(), timeout=NM_CHECK_CONNECTIVITY_TIMEOUT
            )
        except Exception as exc:
            logger.warning("NM forced connectivity re-check failed: %s", exc)
            return

        await self._adopt(connectivity, "forced re-check")

    def _on_properties_changed(self, iface: str, changed: dict, _invalidated: list) -> None:
        """D-Bus PropertiesChanged callback. Filters NM Connectivity changes."""
        if iface != NM_IFACE or "Connectivity" not in changed:
            return

        value = changed["Connectivity"]
        connectivity = value.value if isinstance(value, Variant) else value
        # Adoption may probe, so it cannot run in this synchronous callback.
        self._bg.spawn(self._adopt(connectivity, "changed"), label="nm_props_changed")

    async def recheck(self, reason: str = "link change") -> None:
        """Re-evaluate without waiting for NM to say something.

        NM only emits when *its own* verdict moves, and its verdict is
        family-agnostic: measured 2026-09-20, the ethernet cable was pulled and
        NM stayed FULL for the whole 43-second outage, because wlan0 carries its
        own IPv6 default route (proto ra, metric 600) and the probe succeeded
        over it. No event meant no adoption, so the IPv4 confirmation never ran
        and the appliance reported a working internet it did not have.

        Called by NetworkService on a link change — the cheapest trigger there
        is, and the one that actually correlates with an uplink moving.
        """
        if self._nm_iface is None:
            return
        try:
            connectivity = await self._nm_iface.get_connectivity()
        except Exception as exc:
            logger.debug("Connectivity re-read failed (%s): %s", reason, exc)
            return
        await self._adopt(connectivity, reason)

    async def _confirm_ipv4(self) -> bool:
        """True unless IPv4 is *observed* to be broken.

        Asymmetric on purpose, and the asymmetry is the whole doctrine. A TCP
        connect failure is a positive observation, so it downgrades. Anything
        else — a timeout, a DNS failure, aiohttp missing on a dev host — means
        we could not tell, and an untold story is not a problem: holding LIMITED
        on our own probe's failure would block Spotify, Tidal, Qobuz, Radio and
        Podcast in the UI on evidence we never gathered. Fail open stays fail
        open.

        Note asyncio.TimeoutError subclasses OSError on Python 3.11+, so a bare
        `except OSError` here would silently turn a slow link into a fake outage.
        """
        try:
            connector = aiohttp.TCPConnector(family=socket.AF_INET)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    IPV4_PROBE_URI,
                    timeout=aiohttp.ClientTimeout(total=IPV4_PROBE_TIMEOUT),
                ) as response:
                    response.release()
            return True
        except aiohttp.ClientConnectorDNSError as exc:
            # Name resolution, not an IPv4 reachability verdict.
            logger.debug("IPv4 probe could not resolve %s: %s", IPV4_PROBE_URI, exc)
            return True
        except aiohttp.ClientConnectorError as exc:
            logger.info("IPv4 probe could not connect, holding NM's FULL down: %s", exc)
            return False
        except Exception as exc:
            logger.debug("IPv4 probe inconclusive, trusting NM: %s", exc)
            return True

    async def _adopt(self, connectivity: int, reason: str) -> None:
        """Publish the level NM reports, downgrading an unconfirmed FULL.

        Only FULL is second-guessed. NM reporting a degraded link is an
        observation we have no reason to doubt, and confirming bad news would
        spend a request to learn nothing.
        """
        self._nm_level = NM_LEVELS.get(connectivity, ConnectivityLevel.UNKNOWN)
        new_level = self._nm_level

        if new_level == ConnectivityLevel.FULL and not await self._confirm_ipv4():
            new_level = ConnectivityLevel.LIMITED

        self._holding = (
            self._nm_level == ConnectivityLevel.FULL
            and new_level != ConnectivityLevel.FULL
        )
        if self._holding:
            self._start_hold_loop()

        if new_level == self._level:
            return

        previous = self._level
        self._level = new_level
        logger.info(
            "Connectivity (%s): %s → %s (NM=%s)",
            reason,
            previous.value,
            new_level.value,
            connectivity,
        )
        await self._broadcast()

    def _start_hold_loop(self) -> None:
        if self._hold_running:
            return
        self._hold_running = True
        self._bg.spawn(self._hold_loop(), label="ipv4_hold_recheck")

    async def _hold_loop(self) -> None:
        """Re-probe while we contradict NM, so the downgrade can lift itself.

        Backs off because the condition it waits for — IPv4 coming back on a
        link NM already considers healthy — is measured in minutes, not seconds.
        """
        delay = HOLD_RECHECK_START_S
        try:
            while self._holding:
                await asyncio.sleep(delay)
                if not self._holding:
                    break
                if await self._confirm_ipv4():
                    self._holding = False
                    previous = self._level
                    self._level = ConnectivityLevel.FULL
                    logger.info(
                        "Connectivity (IPv4 recovered): %s → full", previous.value
                    )
                    await self._broadcast()
                    break
                delay = min(delay * 2, HOLD_RECHECK_MAX_S)
        finally:
            self._hold_running = False

    async def _broadcast(self) -> None:
        """The level rides its own event *and* full_state, since a level change
        can flip the active source's `network_unavailable` without anything
        about the source itself changing."""
        if self._state_machine is None:
            return
        await self._state_machine.broadcast(
            SystemConnectivityChanged(connectivity=self._level.value)
        )

    async def cleanup(self) -> None:
        await self._bg.cancel_all()
        if self._properties_iface is not None and self._listener_attached:
            try:
                self._properties_iface.off_properties_changed(self._on_properties_changed)
            except Exception as e:
                logger.debug(f"NM properties listener detach failed: {e}")
            self._listener_attached = False
        if self._bus is not None:
            try:
                self._bus.disconnect()
            except Exception as e:
                logger.debug(f"NM D-Bus disconnect failed: {e}")
            self._bus = None
