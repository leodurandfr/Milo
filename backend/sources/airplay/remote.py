# backend/sources/airplay/remote.py
"""shairport-sync's D-Bus control: the one request Milō makes of it.

`DropSession` ends the current AirPlay session and replaces it with nothing.
Measured on shairport-sync 5.5.1 (2026-09-23), on a paused iPhone Music
session and on a paused Spotify one: the daemon sends `disc` at once and the
phone falls back to its own speaker. It is how the idle timeout ends a paused
session (REQUEST_END) without restarting the daemon under the sender.

The interface is compiled in on every unit (`--with-dbus-interface`, in both
build paths) and the policy lets `milo` call it
(rootfs/etc/dbus-1/system.d/shairport-sync-dbus.conf).
"""
import asyncio
import logging

from dbus_next import Message, MessageType
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType

logger = logging.getLogger("source.airplay.remote")

BUS_NAME = "org.gnome.ShairportSync"
OBJECT_PATH = "/org/gnome/ShairportSync"

# A daemon that no longer answers is the one case this can fail with a session
# still open (a dead one has already ended it): don't wait on it for long.
CALL_TIMEOUT_S = 5.0


async def drop_session() -> bool:
    """Ask shairport-sync to end its session. True when it took the request."""
    bus = None
    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        reply = await asyncio.wait_for(bus.call(Message(
            destination=BUS_NAME, path=OBJECT_PATH, interface=BUS_NAME, member="DropSession",
        )), CALL_TIMEOUT_S)
    except (OSError, asyncio.TimeoutError) as e:
        logger.warning(f"DropSession did not reach shairport-sync: {e!r}")
        return False
    finally:
        if bus is not None:
            bus.disconnect()
    if reply.message_type is MessageType.ERROR:
        logger.warning(f"shairport-sync refused DropSession: {reply.error_name} {reply.body}")
        return False
    return True
