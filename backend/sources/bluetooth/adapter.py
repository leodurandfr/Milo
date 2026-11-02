# backend/sources/bluetooth/adapter.py
"""
BlueZ adapter control over D-Bus: exposure, per-peer blocking, disconnection.

This replaces a `bluetoothctl` session fed on stdin. That session reported
success on `proc.returncode == 0`, which measures that bluetoothctl *ran* — not
that anything was applied. Measured on the unit 2026-08-15, with the source
started and reporting a configured adapter: `pairable on` had not taken
(`Pairable: no`, so a device that had never paired could not pair at all) and
`class 0x200404` is not even a command in bluetoothctl 5.82 (`Invalid command in
menu main: class`) — BlueZ 5 derives the class from the registered profiles, so
the command was dropped rather than made loud. Both failures printed a line
inside the session and exited 0.

Here every write is a D-Bus property set that raises on refusal, and the
adapter's own value is read back afterwards, so "configured" means the adapter
agrees.

Peer blocking is how "Milō must not be connectable" is enforced for an
*already-paired* sender: `discoverable off` only stops discovery, and a paired
device dials a known address. `Device1.Blocked` refuses the link itself. Only
peers advertising the A2DP **Source** UUID are touched — the Bluetooth HID
remote keeps `bluetooth.service` running and must never be blocked with them.
"""
import asyncio
import contextlib
import logging
from typing import Dict, List, Optional

from dbus_next import DBusError, Variant
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType

from backend.shared.decorators import handle_errors

ADAPTER_PATH = "/org/bluez/hci0"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
PROPS_IFACE = "org.freedesktop.DBus.Properties"

# A peer that can *send* audio to Milō. The HID remote does not carry it, which
# is what keeps it out of every block sweep below.
A2DP_SOURCE_UUID = "0000110a-0000-1000-8000-00805f9b34fb"

# Every call below is bounded by it, Introspect included (dbus-next's own
# default there is 30 s, and it bounds nothing else), and so are the pairing
# agent's AgentManager1 exchanges (agent.py). Unbounded, a bluetoothd
# that stops answering would park the HTTP request the Disconnect button made,
# stop the BlueALSA connect/disconnect feed for good (the monitor awaits its
# connected-callback inline), and hold the source's STOP inside the exposure
# write, which nothing preempts. The two `bluetoothctl` spawns this replaced
# each carried their own 10 s cap; losing it was the one thing the move to
# D-Bus must not cost. Read at call time, never bound as a default argument.
DBUS_CALL_TIMEOUT = 10.0


class BluetoothAdapter:
    """org.bluez.Adapter1 + org.bluez.Device1 writes, with the result read back."""

    def __init__(self):
        self._logger = logging.getLogger("source.bluetooth.adapter")
        self._bus: Optional[MessageBus] = None

    async def _connect(self) -> Optional[MessageBus]:
        """Connect the system bus lazily, keeping it for later calls."""
        if self._bus is None:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        return self._bus

    async def close(self) -> None:
        """Drop the system bus connection."""
        if self._bus:
            with contextlib.suppress(Exception):
                self._bus.disconnect()
            self._bus = None

    async def _interface(self, path: str, name: str):
        """Get one interface of a BlueZ object."""
        bus = await self._connect()
        introspection = await bus.introspect("org.bluez", path, timeout=DBUS_CALL_TIMEOUT)
        obj = bus.get_proxy_object("org.bluez", path, introspection)
        return obj.get_interface(name)

    async def _properties(self, path: str):
        """Get the Properties interface of a BlueZ object."""
        return await self._interface(path, PROPS_IFACE)

    async def _set_verified(self, path: str, iface: str, name: str,
                            variant: Variant, expected) -> bool:
        """Write one property and read it back.

        The read-back is the point: a BlueZ that accepts the call and then
        settles on another value (an adapter still powering up, a policy
        overriding the request) is exactly what the bluetoothctl script could
        not tell from success.
        """
        try:
            props = await self._properties(path)
            await asyncio.wait_for(props.call_set(iface, name, variant), DBUS_CALL_TIMEOUT)
            actual = await asyncio.wait_for(props.call_get(iface, name), DBUS_CALL_TIMEOUT)
        except asyncio.TimeoutError:
            self._logger.warning(
                f"Bluetooth adapter did not answer {name}={expected} on {path} "
                f"within {DBUS_CALL_TIMEOUT} s"
            )
            return False
        if actual.value != expected:
            self._logger.error(
                f"Bluetooth adapter refused {name}={expected} on {path} "
                f"(reports {actual.value})"
            )
            return False
        return True

    @handle_errors(default=False)
    async def power_on(self) -> bool:
        """Power the adapter on."""
        return await self._set_verified(
            ADAPTER_PATH, ADAPTER_IFACE, "Powered", Variant("b", True), True
        )

    @handle_errors(default=False)
    async def set_discoverable_timeout(self, seconds: int) -> bool:
        """Set DiscoverableTimeout (0 = never expires)."""
        return await self._set_verified(
            ADAPTER_PATH, ADAPTER_IFACE, "DiscoverableTimeout",
            Variant("u", seconds), seconds
        )

    @handle_errors(default=False)
    async def set_exposure(self, discoverable: bool, pairable: bool) -> bool:
        """Set Discoverable and Pairable, reporting whether the adapter agreed.

        Both are attempted even if the first fails — a half-applied exposure is
        worth logging in full rather than one line at a time.
        """
        results = [
            await self._set_verified(ADAPTER_PATH, ADAPTER_IFACE, "Discoverable",
                                     Variant("b", discoverable), discoverable),
            await self._set_verified(ADAPTER_PATH, ADAPTER_IFACE, "Pairable",
                                     Variant("b", pairable), pairable),
        ]
        return all(results)

    @handle_errors(default=False)
    async def disconnect_device(self, address: str) -> bool:
        """Drop the link with one peer, and read back that it is really gone.

        `bluetoothctl disconnect <address>` was what did this, in two copies,
        and it carried the same blindness as the session this module replaced:
        it exits 0 on having *issued* the request. Measured on the unit against
        a streaming iPhone — `Device1.Disconnect` returned in 3 s with
        `Connected` already false and the peer stayed away, where bluetoothctl
        took about 6 s and answered success either way, including for an address
        the adapter does not know.

        The path is built from the address rather than looked up: BlueZ names a
        device object after it, and a path that does not exist raises here
        instead of being reported as a disconnection that happened.
        """
        path = f"{ADAPTER_PATH}/dev_{address.upper().replace(':', '_')}"
        device = await self._interface(path, DEVICE_IFACE)

        try:
            await asyncio.wait_for(device.call_disconnect(), DBUS_CALL_TIMEOUT)
        except DBusError as e:
            # `NotConnected` on a peer that already left is the answer we want,
            # not a failure — and a refusal for any other reason is still not
            # the verdict. The read-back below is, in both cases: it is the
            # whole reason this left `bluetoothctl`. Reported as a failure here
            # instead, a phone that dropped on its own while the BlueALSA feed
            # was down would answer the button with "did not release the link"
            # and raise the UI's error banner for a link that is gone.
            self._logger.debug(f"Disconnect on {address} answered {e}")

        props = await self._properties(path)
        connected = await asyncio.wait_for(
            props.call_get(DEVICE_IFACE, "Connected"), DBUS_CALL_TIMEOUT
        )
        if connected.value:
            self._logger.error(
                f"Bluetooth peer {address} is still connected after Disconnect"
            )
            return False
        return True

    async def audio_peers(self) -> Optional[Dict[str, str]]:
        """Known devices that can send audio, as {object path: address}.

        Filtered on the A2DP Source UUID rather than on "is it paired": the
        blocking below must reach a sender and must never reach the HID remote.

        None when BlueZ would not list them — never `{}`, which is the answer
        "no sender is known" and would let a sweep that reached nobody report
        itself applied.
        """
        try:
            manager = await self._interface("/", "org.freedesktop.DBus.ObjectManager")
            objects = await asyncio.wait_for(
                manager.call_get_managed_objects(), DBUS_CALL_TIMEOUT
            )
        except Exception as e:
            # Broad on purpose: a bus that cannot connect (a dev host) raises
            # whatever dbus-next's transport raises, and every failure here
            # means the same thing — nobody was listed.
            self._logger.warning(f"Bluetooth peers could not be listed: {e!r}")
            return None

        peers = {}
        for path, interfaces in objects.items():
            device = interfaces.get(DEVICE_IFACE)
            if not device:
                continue
            uuids = device.get("UUIDs")
            address = device.get("Address")
            if not uuids or not address:
                continue
            if A2DP_SOURCE_UUID in [u.lower() for u in uuids.value]:
                peers[path] = address.value
        return peers

    @handle_errors(default=False)
    async def set_audio_peers_blocked(self, blocked: bool,
                                      keep_unblocked: Optional[str] = None) -> bool:
        """Block or unblock every known A2DP sender.

        Args:
            blocked: Target state for Device1.Blocked
            keep_unblocked: Address left untouched — the sender currently holding
                the link. Blocking it would drop the audio it is playing, which
                is the opposite of what hiding the appliance is for.

        Returns:
            True if every peer reached the target state.
        """
        peers = await self.audio_peers()
        if peers is None:
            verb = "blocked" if blocked else "unblocked"
            self._logger.error(
                f"Bluetooth peers not {verb}: they could not be listed — "
                f"none of them was reached"
            )
            return False
        if not peers:
            return True

        failed: List[str] = []
        for path, address in peers.items():
            if keep_unblocked and address.upper() == keep_unblocked.upper():
                continue
            try:
                if not await self._set_verified(path, DEVICE_IFACE, "Blocked",
                                                Variant("b", blocked), blocked):
                    failed.append(address)
            except Exception as e:
                self._logger.error(f"Failed to set Blocked={blocked} on {address}: {e!r}")
                failed.append(address)

        if failed:
            consequence = (
                "the appliance still accepts them in a state where it must not"
                if blocked else "they cannot connect to the appliance"
            )
            verb = "blocked" if blocked else "unblocked"
            self._logger.error(f"Bluetooth peers not {verb}: {failed} — {consequence}")
            return False
        return True
