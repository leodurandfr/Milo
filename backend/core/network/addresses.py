"""The server's own IPv4 addresses, in the order a satellite should try them.

A satellite finds the server through `milo.local`, which avahi publishes on one
interface at a time (eth0 whenever it has an address). When the server's cable
goes, NetworkManager withdraws the wired address only after its carrier wait
(6 s, measured 2026-11-06), and mDNS keeps answering it until then: a satellite
that knows the server's other address can dial it at once instead. The heartbeat
answer carries this list for that.
"""
import fcntl
import socket
import struct
from typing import List, Optional

# Wired first: Ethernet is preferred whenever it answers (the owner, 2026-11-06).
INTERFACES = ("eth0", "wlan0")
SIOCGIFADDR = 0x8915


def interface_ipv4(name: str) -> Optional[str]:
    """The interface's IPv4 address, or None when it has none or does not exist."""
    ifreq = struct.pack("256s", name.encode())
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            return socket.inet_ntoa(fcntl.ioctl(s.fileno(), SIOCGIFADDR, ifreq)[20:24])
    except OSError:
        return None


def server_addresses() -> List[str]:
    """Every IPv4 address the server answers on, wired first."""
    return [address for address in map(interface_ipv4, INTERFACES) if address]
