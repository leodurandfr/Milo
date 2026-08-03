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
from typing import Optional

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


class ConnectivityService:
    """Tracks NetworkManager connectivity and broadcasts state changes."""

    def __init__(self):
        self._state_machine = None
        self._bus: Optional[MessageBus] = None
        self._properties_iface = None
        self._level: ConnectivityLevel = ConnectivityLevel.UNKNOWN  # Fail-open default
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

        new_level = NM_LEVELS.get(connectivity, ConnectivityLevel.UNKNOWN)
        if new_level == self._level:
            return

        previous = self._level
        self._level = new_level
        logger.info(
            "Connectivity (forced re-check): %s → %s (NM=%s)",
            previous.value,
            new_level.value,
            connectivity,
        )
        await self._broadcast()

    def _on_properties_changed(self, iface: str, changed: dict, _invalidated: list) -> None:
        """D-Bus PropertiesChanged callback. Filters NM Connectivity changes."""
        if iface != NM_IFACE or "Connectivity" not in changed:
            return

        value = changed["Connectivity"]
        connectivity = value.value if isinstance(value, Variant) else value
        new_level = NM_LEVELS.get(connectivity, ConnectivityLevel.UNKNOWN)

        if new_level == self._level:
            return

        previous = self._level
        self._level = new_level
        logger.info(
            "Connectivity changed: %s → %s (NM=%s)",
            previous.value,
            new_level.value,
            connectivity,
        )
        self._bg.spawn(self._broadcast(), label="nm_props_changed")

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
