# backend/core/multiroom/identity.py
"""
How Milō names a multiroom client: the MAC of its primary interface.

Stateless, and deliberately importable on its own — the registry, the snapcast
REST and WebSocket services and VolumeService all need this identity, and none
of them should have to reach through ClientRegistryService (or take a lazy
import to dodge a cycle) to get it.
"""
import logging
import os
from typing import Optional

from backend.config import constants

logger = logging.getLogger(__name__)

# The interfaces tried first, in this order, whatever else the unit has: every
# unit in the field is keyed on one of them, so no other rule may come before.
PRIMARY_INTERFACES = ("eth0", "wlan0")
UNSET_MAC = "00:00:00:00:00:00"


def _usable_mac(sysfs_net: str, name: str) -> Optional[str]:
    try:
        with open(os.path.join(sysfs_net, name, "address")) as f:
            mac = f.read().strip()
    except OSError:
        return None
    return mac if mac and mac != UNSET_MAC else None


def read_host_id(sysfs_net: str) -> Optional[str]:
    """The unit's multiroom identity as `milo-host-id` prints it, from one sysfs root.

    eth0, then wlan0, then every other physical interface (one with a `device`
    link, so not lo or a tunnel) in name order — a Pi whose wired port is named
    `end0` keeps an identity. An address that cannot be read, is empty, or is
    all zeros is skipped. Both snapclient launchers and the satellite's
    registration apply this same rule, held equal by
    tests/architecture/test_host_id_rule.py.
    """
    for name in PRIMARY_INTERFACES:
        mac = _usable_mac(sysfs_net, name)
        if mac:
            return mac
    try:
        others = sorted(
            name for name in os.listdir(sysfs_net)
            if name not in PRIMARY_INTERFACES
            and os.path.exists(os.path.join(sysfs_net, name, "device"))
        )
    except OSError:
        return None
    for name in others:
        mac = _usable_mac(sysfs_net, name)
        if mac:
            return mac
    return None


def get_local_mac() -> Optional[str]:
    """MAC of the snapclient's primary interface, matching its --hostID (see `read_host_id`).

    Read afresh on every call, never kept: the snapclient runs `milo-host-id`
    again each time it starts, so an identity cached here would outlive the one
    it passes once an interface comes up late. Only the local client asks (at
    most twice per sweep), and sysfs is kernel memory, not the card.
    """
    return read_host_id(constants.SYSFS_NET)


def is_stale_local_client(client_id: str, ip: str) -> bool:
    """True for a Snapcast client at 127.0.0.1 whose id doesn't match the current local MAC."""
    if ip != "127.0.0.1":
        return False
    local_mac = get_local_mac()
    return bool(local_mac) and client_id != local_mac


def compute_mac_id(hostname: str, ip: str, host_id: str = "") -> str:
    """
    Return the identity Milō keys a client by: the MAC of its primary
    interface, chosen by the rule `read_host_id` states.

    That identity has exactly one producer — the `--hostID` every snapclient
    launcher passes, printed by `milo-host-id` — and every other component
    derives it the same way: `milo-client`'s registration POST applies the same
    rule, and so does this function for the local client. Snapcast's `host.mac` is NOT that
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

    if host_id and host_id != UNSET_MAC:
        return host_id

    raise ValueError(f"No client id for {hostname} at {ip}")
