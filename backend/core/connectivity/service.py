# backend/core/connectivity/service.py
"""
Internet connectivity monitoring via NetworkManager (D-Bus).

Subscribes to org.freedesktop.NetworkManager's PropertiesChanged signal on
the Connectivity property. NetworkManager performs its own probe of a known
endpoint (configured in /etc/NetworkManager/conf.d/99-milo-connectivity.conf),
periodically and on every interface state change.

That signal is necessary and not sufficient, so this service also probes on its
own — see `_probe_loop`. NM's probe is address-family agnostic and this
appliance streams from IPv4-only hosts, and NM signals only when its own value
*changes*. A gateway that goes silent while IPv6 still answers therefore
produces no event at all, at any duration: measured on the unit, 7 minutes of a
dead IPv4 path with not one line from NM. A path that stops working notifies
nobody — which is why every system that answers this question polls, NM and the
bonding driver's arp_interval included.

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

# What one probe can say. A blackholed path and a merely slow one produce the
# *same* signal — silence — so a single inconclusive result is never evidence.
# Measured 2026-09-20: with the gateway's ARP entry poisoned, the probe raises
# TimeoutError; with the cable out it raises ClientConnectorError ("Network is
# unreachable"), because the kernel can answer immediately for a dead interface.
# Only the second is a verdict on its own.
PROBE_OK = "ok"
PROBE_FAILED = "failed"            # connect refused/unreachable — a verdict
PROBE_INCONCLUSIVE = "inconclusive"  # timeout, DNS, no aiohttp — not a verdict

# Consecutive inconclusive probes before silence is treated as a black hole. A
# slow link eventually answers; a path that drops packets never does. Three at
# PROBE_INTERVAL_S is ~3 minutes, which is the price of not calling a struggling
# link an outage.
INCONCLUSIVE_STRIKES = 3

# Steady-state confirmation while NM says FULL. This is the only thing that can
# notice a grey failure, which emits no event of any kind — see _probe_loop.
PROBE_INTERVAL_S = 60.0

# While we contradict NM, its own value stays FULL and it has no reason to emit,
# so our re-probe is also the only thing that can lift the downgrade. Backs off
# because what it waits for takes minutes.
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
        self._hold_backoff: float = HOLD_RECHECK_START_S
        # Consecutive probes that said nothing. Silence is only evidence when
        # it repeats — see _periodic_check.
        self._inconclusive: int = 0
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
            self._bg.spawn(self._probe_loop(), label="ipv4_probe_loop")
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

    async def _confirm_ipv4(self) -> str:
        """PROBE_OK, PROBE_FAILED or PROBE_INCONCLUSIVE.

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
            return PROBE_OK
        except aiohttp.ClientConnectorDNSError as exc:
            # Name resolution, not an IPv4 reachability verdict.
            logger.debug("IPv4 probe could not resolve %s: %s", IPV4_PROBE_URI, exc)
            return PROBE_INCONCLUSIVE
        except aiohttp.ClientConnectorError as exc:
            logger.info("IPv4 probe could not connect: %s", exc)
            return PROBE_FAILED
        except Exception as exc:
            logger.debug("IPv4 probe inconclusive (%s): %s", type(exc).__name__, exc)
            return PROBE_INCONCLUSIVE

    async def _adopt(self, connectivity: int, reason: str) -> None:
        """Publish the level NM reports, downgrading an unconfirmed FULL.

        Only FULL is second-guessed. NM reporting a degraded link is an
        observation we have no reason to doubt, and confirming bad news would
        spend a request to learn nothing.
        """
        self._nm_level = NM_LEVELS.get(connectivity, ConnectivityLevel.UNKNOWN)
        new_level = self._nm_level

        if new_level == ConnectivityLevel.FULL:
            result = await self._confirm_ipv4()
            if result == PROBE_FAILED:
                new_level = ConnectivityLevel.LIMITED
            elif result == PROBE_OK:
                self._inconclusive = 0

        await self._publish(new_level, reason, connectivity)

    async def _publish(self, new_level: ConnectivityLevel, reason: str, nm_value) -> None:
        if new_level == self._level:
            return
        previous = self._level
        self._level = new_level
        logger.info(
            "Connectivity (%s): %s → %s (NM=%s)",
            reason,
            previous.value,
            new_level.value,
            nm_value,
        )
        await self._broadcast()

    def _contradicting(self) -> bool:
        """True while we publish something worse than NM reports."""
        return (
            self._nm_level == ConnectivityLevel.FULL
            and self._level != ConnectivityLevel.FULL
        )

    async def _probe_loop(self) -> None:
        """Ask, because a grey failure never tells.

        Measured on the unit 2026-09-20: with the gateway silent but the link
        and the lease both intact, NM emits nothing at all — not in 100 seconds,
        not in 7 minutes. Its periodic check still succeeds over IPv6, so its
        value never changes, and NM only signals on change. No event means the
        event-driven paths (`_on_properties_changed`, `recheck`) can never fire,
        so the failure is invisible for ever rather than for one probe interval.

        Polling is the appliance's default *last* resort, not its first. Here it
        is the only instrument there is: a silent failure emits nothing by
        definition. One ~200-byte request a minute buys the only detection
        available.
        """
        while True:
            await asyncio.sleep(
                self._hold_backoff if self._contradicting() else PROBE_INTERVAL_S
            )
            try:
                await self._periodic_check()
            except Exception as exc:
                logger.error("Connectivity probe loop failed: %s", exc, exc_info=exc)
                self._hold_backoff = HOLD_RECHECK_START_S

    async def _periodic_check(self) -> None:
        """One pass of the loop. Split out so a test can drive it without a clock."""
        if self._nm_level != ConnectivityLevel.FULL:
            # NM already reports a problem and will signal when it clears. We
            # never upgrade on our own evidence, and confirming bad news spends
            # a request to learn nothing.
            self._hold_backoff = HOLD_RECHECK_START_S
            return

        result = await self._confirm_ipv4()

        if result == PROBE_OK:
            self._inconclusive = 0
            self._hold_backoff = HOLD_RECHECK_START_S
            await self._publish(ConnectivityLevel.FULL, "IPv4 recovered", 4)
            return

        if result == PROBE_INCONCLUSIVE:
            # Silence. A blackholed path and a slow one are indistinguishable
            # from one probe — measured on the unit, a poisoned gateway ARP
            # entry raises TimeoutError, exactly like a link under load. Only
            # repetition separates them, so count before concluding.
            self._inconclusive += 1
            if self._inconclusive < INCONCLUSIVE_STRIKES:
                return
            reason = f"IPv4 silent x{self._inconclusive}"
        else:
            self._inconclusive = 0
            reason = "IPv4 probe"

        if self._level == ConnectivityLevel.FULL:
            self._hold_backoff = HOLD_RECHECK_START_S
            await self._publish(ConnectivityLevel.LIMITED, reason, 4)
        else:
            # Still contradicting NM: back off, because what we wait for — IPv4
            # returning on a link NM already calls healthy — takes minutes.
            self._hold_backoff = min(self._hold_backoff * 2, HOLD_RECHECK_MAX_S)

    async def _broadcast(self) -> None:
        """The level rides its own event, and the state is republished: a level
        change can flip a source's `availability` without anything about the
        source itself changing."""
        if self._state_machine is None:
            return
        await self._state_machine.broadcast(
            SystemConnectivityChanged(connectivity=self._level.value)
        )
        await self._state_machine.publish_state()

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
