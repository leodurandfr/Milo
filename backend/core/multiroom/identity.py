# backend/core/multiroom/identity.py
"""
How Milō names a multiroom client: the MAC of its primary interface.

Stateless, and deliberately importable on its own — the registry, the snapcast
REST and WebSocket services and VolumeService all need this identity, and none
of them should have to reach through ClientRegistryService (or take a lazy
import to dodge a cycle) to get it.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def get_local_mac() -> Optional[str]:
    """MAC of the snapclient's primary interface (eth0 → wlan0 fallback), matching the --hostID flag."""
    for iface in ('eth0', 'wlan0'):
        try:
            with open(f'/sys/class/net/{iface}/address') as f:
                return f.read().strip()
        except FileNotFoundError:
            continue
    return None


def is_stale_local_client(client_id: str, ip: str) -> bool:
    """True for a Snapcast client at 127.0.0.1 whose id doesn't match the current local MAC."""
    if ip != "127.0.0.1":
        return False
    local_mac = get_local_mac()
    return bool(local_mac) and client_id != local_mac


def compute_mac_id(hostname: str, ip: str, host_id: str = "") -> str:
    """
    Return the identity Milō keys a client by: the MAC of its primary
    interface, eth0 first and wlan0 as fallback.

    That identity has exactly one producer — the `--hostID` every snapclient
    launcher passes — and every other component derives it the same way:
    `milo-client`'s registration POST reads eth0-then-wlan0 from /sys, and so
    does this function for the local client. Snapcast's `host.mac` is NOT that
    identity: it reports the interface the client actually connected through,
    so a wifi-only client (eth0 present but unused) announces its wlan0 MAC
    while registering under its eth0 one. Keying on `host.mac` therefore split
    one device into two identities — the pending entry never matched, its
    name was lost, and it was never cleared. Read the id Milō assigned.

    Args:
        hostname: Hostname from Snapcast (for logging only)
        ip: IP address from Snapcast
        host_id: Snapcast client id, i.e. the `--hostID` we passed (remote clients)

    Returns:
        MAC address in format xx:xx:xx:xx:xx:xx

    Raises:
        RuntimeError: If local MAC cannot be determined
        ValueError: If a remote client announces no usable id
    """
    # Local client: read from /sys rather than trusting the loopback entry,
    # whose host.mac is not the primary interface either.
    if ip == "127.0.0.1":
        local_mac = get_local_mac()
        if not local_mac:
            raise RuntimeError("Cannot determine local MAC address")
        return local_mac

    if host_id and host_id != "00:00:00:00:00:00":
        return host_id

    raise ValueError(f"No client id for {hostname} at {ip}")
