"""
Boot-time registration with main Milo.

On startup, the client registers itself with the main Milo server
so it appears as a pending speaker available for configuration.
Sends a heartbeat every HEARTBEAT_INTERVAL seconds so the server
can detect when this client goes offline, and so it hears when this
client, just plugged back in, would follow a new server.
"""
import asyncio
import ipaddress
import json
import logging
import os
import re
import socket

import aiohttp

from server_secret import accepts_new_server

logger = logging.getLogger(__name__)

HARDWARE_FILE = "/var/lib/milo-client/hardware.json"
IDENTITY_FILE = "/var/lib/milo-client/identity.json"
ASOUND_CARDS_FILE = "/proc/asound/cards"
# Where the kernel lists the network interfaces; tests point it at a fixture tree.
SYSFS_NET = "/sys/class/net"
MILO_PRINCIPAL_HOST = "milo.local"
MILO_PRINCIPAL_PORT = 8000
REGISTER_ENDPOINT = "/api/multiroom/register-client"
RETRY_INTERVAL = 30  # seconds (before first successful registration)
HEARTBEAT_INTERVAL = 15  # seconds (after successful registration)

# The main Milo's own IPv4 addresses, wired first, as its last heartbeat answer
# listed them. The address watcher dials the next one when the stream's own stops
# answering: mDNS keeps answering a server's wired address for seconds after its
# cable has gone.
_server_addresses: tuple[str, ...] = ()


def remember_server_addresses(answer: dict) -> None:
    """Keep the IPv4 addresses a heartbeat answer lists, in their order."""
    global _server_addresses
    kept = []
    for address in answer["server_addresses"]:
        try:
            kept.append(str(ipaddress.IPv4Address(address)))
        except ipaddress.AddressValueError:
            logger.debug(f"Ignoring a server address that is not IPv4: {address!r}")
    _server_addresses = tuple(kept)


def known_server_addresses() -> tuple[str, ...]:
    """The main Milo's addresses, wired first; empty before the first heartbeat answer."""
    return _server_addresses


UNSET_MAC = "00:00:00:00:00:00"


def _usable_mac(name: str) -> str | None:
    try:
        with open(os.path.join(SYSFS_NET, name, "address")) as f:
            mac = f.read().strip()
    except OSError:
        return None
    return mac if mac and mac != UNSET_MAC else None


def _get_mac_address() -> str:
    """This unit's identity, by the rule `milo-host-id` prints for the snapclient's --hostID.

    eth0, then wlan0, then every other physical interface (one with a `device`
    link) in name order; an address that cannot be read, is empty or is all
    zeros is skipped. The registration must announce the id the snapclient
    passes, or the server keys one speaker twice. Held equal to the script by
    milo-client/app/tests/test_host_id.py.
    """
    primary = ("eth0", "wlan0")
    for name in primary:
        mac = _usable_mac(name)
        if mac:
            return mac
    try:
        others = sorted(
            name for name in os.listdir(SYSFS_NET)
            if name not in primary and os.path.exists(os.path.join(SYSFS_NET, name, "device"))
        )
    except OSError:
        others = []
    for name in others:
        mac = _usable_mac(name)
        if mac:
            return mac
    raise RuntimeError("No usable MAC address on any physical interface")


def local_ip_towards(remote_ip: str, remote_port: int) -> str:
    """Get the local IP used to reach the main Milo (most reliable routable IP)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((remote_ip, remote_port))
        return s.getsockname()[0]


def resolve_milo_principal() -> str:
    """Resolve the main Milo to an IPv4 address.

    MILO_PRINCIPAL_IP comes from the unit's EnvironmentFile. `milo-first-boot`
    writes the string "milo.local" when it converts a flashed card into a
    satellite, and an operator can replace it with a literal IP on a LAN where
    mDNS does not work — both forms are accepted. A unit with no env entry at all
    falls back to mDNS.
    """
    target = os.environ.get("MILO_PRINCIPAL_IP") or MILO_PRINCIPAL_HOST
    try:
        return str(ipaddress.IPv4Address(target))
    except ipaddress.AddressValueError:
        pass  # a hostname — resolve it below
    try:
        results = socket.getaddrinfo(target, MILO_PRINCIPAL_PORT, socket.AF_INET)
        if results:
            return results[0][4][0]
    except socket.gaierror:
        pass
    raise RuntimeError(f"Cannot resolve {target}")


def _read_hardware_config() -> dict:
    """Read hardware.json for registration payload."""
    try:
        with open(HARDWARE_FILE, "r") as f:
            data = json.load(f)
        audio = data.get("audio", {})
        audio_id = audio.get("id", "none")
        volume_control = audio.get("volume_control", True)
        return {
            "audio_id": audio_id,
            "hardware_configured": audio_id != "none",
            "volume_control": volume_control,
        }
    except (FileNotFoundError, json.JSONDecodeError):
        return {"audio_id": "none", "hardware_configured": False, "volume_control": True}


_CARD_ID = re.compile(r"^\s*\d+\s+\[([^\]\s]+)\s*\]", re.MULTILINE)


def read_alsa_cards() -> list[str] | None:
    """The ALSA card ids /proc/asound/cards lists, None when it cannot be read.

    Facts only: the server knows which id the configured card registers, and
    judges from it whether the board is seen.
    """
    try:
        with open(ASOUND_CARDS_FILE, "r") as f:
            return _CARD_ID.findall(f.read())
    except OSError:
        return None


def _read_identity() -> dict:
    """Read identity.json (name + speaker_type) for registration payload.

    Written by milo-first-boot when applying a wifi-adoption marker, so the
    server can pre-fill the registry without waiting for a separate configure step.
    """
    try:
        with open(IDENTITY_FILE, "r") as f:
            data = json.load(f)
        return {
            "name": data.get("name"),
            "speaker_type": data.get("speaker_type"),
        }
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


async def register_with_main_milo() -> None:
    """
    Register this client with the main Milo server, then heartbeat.

    Phase 1: Retry every RETRY_INTERVAL until the first successful registration.
    Phase 2: Send the same POST every HEARTBEAT_INTERVAL to keep the
             server's pending-client entry alive. If the client is powered
             off, the server will notice the missing heartbeats and remove it.
    """
    # Small delay to let the network settle after boot
    await asyncio.sleep(5)

    loop = asyncio.get_running_loop()
    registered = False

    while True:
        try:
            # Resolve main Milo (blocking mDNS — run in executor to avoid blocking event loop)
            milo_ip = await loop.run_in_executor(None, resolve_milo_principal)

            # Get our own info (blocking file I/O — run in executor)
            mac_id = await loop.run_in_executor(None, _get_mac_address)
            local_ip = await loop.run_in_executor(None, local_ip_towards, milo_ip, MILO_PRINCIPAL_PORT)
            hw_config = await loop.run_in_executor(None, _read_hardware_config)
            identity = await loop.run_in_executor(None, _read_identity)
            alsa_cards = await loop.run_in_executor(None, read_alsa_cards)

            payload = {
                "mac_id": mac_id,
                "ip": local_ip,
                "hardware_configured": hw_config["hardware_configured"],
                "audio_id": hw_config["audio_id"],
                "volume_control": hw_config["volume_control"],
                "alsa_cards": alsa_cards,
                # Asked again at every heartbeat: the server hands its secret to
                # a speaker that says so, which is how one that answers another
                # server comes to follow it (server_secret.py).
                "accepts_new_server": accepts_new_server(),
            }
            if identity.get("name"):
                payload["name"] = identity["name"]
            if identity.get("speaker_type"):
                payload["speaker_type"] = identity["speaker_type"]

            url = f"http://{milo_ip}:{MILO_PRINCIPAL_PORT}{REGISTER_ENDPOINT}"

            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        remember_server_addresses(await resp.json())
                        if not registered:
                            logger.info(
                                f"Registered with main Milo at {milo_ip} "
                                f"(mac={mac_id}, ip={local_ip}, audio={hw_config['audio_id']})"
                            )
                            registered = True
                    else:
                        body = await resp.text()
                        logger.warning(
                            f"Registration failed (HTTP {resp.status}): {body}"
                        )

        except Exception as e:
            if registered:
                logger.debug(f"Heartbeat failed: {e}")
            else:
                logger.warning(f"Registration attempt failed: {e}")

        interval = HEARTBEAT_INTERVAL if registered else RETRY_INTERVAL
        await asyncio.sleep(interval)
