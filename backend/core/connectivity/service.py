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

Only FULL is treated as online. Fails open: if D-Bus or NetworkManager is
unavailable (e.g. dev environment, NM down), the service stays at
online=True so the UI never shows a false offline banner.
"""
import logging
from typing import Optional

from dbus_next.aio import MessageBus
from dbus_next.constants import BusType
from dbus_next.signature import Variant

from backend.shared.background import BackgroundTaskSet

logger = logging.getLogger(__name__)

NM_SERVICE = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
NM_IFACE = "org.freedesktop.NetworkManager"
DBUS_PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

NM_CONNECTIVITY_FULL = 4


class ConnectivityService:
    """Tracks NetworkManager connectivity and broadcasts state changes."""

    def __init__(self):
        self._state_machine = None
        self._bus: Optional[MessageBus] = None
        self._properties_iface = None
        self._online: bool = True  # Fail-open default
        self._listener_attached: bool = False
        self._bg = BackgroundTaskSet(logger, "connectivity")

    def set_state_machine(self, state_machine) -> None:
        self._state_machine = state_machine

    def get_state(self) -> dict:
        return {"online": self._online}

    @property
    def online(self) -> bool:
        return self._online

    async def initialize(self) -> bool:
        """Connect to system bus, read initial state, subscribe to changes."""
        try:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            introspect = await self._bus.introspect(NM_SERVICE, NM_PATH)
            proxy = self._bus.get_proxy_object(NM_SERVICE, NM_PATH, introspect)
            nm_iface = proxy.get_interface(NM_IFACE)
            self._properties_iface = proxy.get_interface(DBUS_PROPERTIES_IFACE)

            connectivity = await nm_iface.get_connectivity()
            self._online = connectivity == NM_CONNECTIVITY_FULL

            self._properties_iface.on_properties_changed(self._on_properties_changed)
            self._listener_attached = True

            logger.info(
                "Connectivity service ready (initial=%s, NM=%s)",
                "online" if self._online else "offline",
                connectivity,
            )
            return True
        except Exception as exc:
            logger.warning(
                "NetworkManager D-Bus unavailable, connectivity defaults to online: %s",
                exc,
            )
            self._online = True
            return False

    def _on_properties_changed(self, iface: str, changed: dict, _invalidated: list) -> None:
        """D-Bus PropertiesChanged callback. Filters NM Connectivity changes."""
        if iface != NM_IFACE or "Connectivity" not in changed:
            return

        value = changed["Connectivity"]
        connectivity = value.value if isinstance(value, Variant) else value
        new_online = connectivity == NM_CONNECTIVITY_FULL

        if new_online == self._online:
            return

        previous = self._online
        self._online = new_online
        logger.info(
            "Connectivity changed: %s → %s (NM=%s)",
            "online" if previous else "offline",
            "online" if new_online else "offline",
            connectivity,
        )
        self._bg.spawn(self._broadcast(), label="nm_props_changed")

    async def _broadcast(self) -> None:
        if self._state_machine is None:
            return
        await self._state_machine.broadcast_event(
            category="system",
            event_type="connectivity_changed",
            data={"source": "system", "online": self._online},
            include_full_state=False,
        )

    async def cleanup(self) -> None:
        await self._bg.cancel_all()
        if self._properties_iface is not None and self._listener_attached:
            try:
                self._properties_iface.off_properties_changed(self._on_properties_changed)
            except Exception:
                pass
            self._listener_attached = False
        if self._bus is not None:
            try:
                self._bus.disconnect()
            except Exception:
                pass
            self._bus = None
