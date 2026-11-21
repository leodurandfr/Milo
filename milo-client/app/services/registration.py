"""
Boot-time registration with main Milo.

On startup, the client registers itself with the main Milo server
so it appears as a pending speaker available for configuration.
Sends a heartbeat every HEARTBEAT_INTERVAL seconds so the server
can detect when this client goes offline, and so it hears when this
client, just plugged back in, would follow a new server.
"""
import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import re
import socket
import threading
from pathlib import Path
from typing import Optional

import aiohttp

from atomic_file import write_atomic
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

# How long the main Milo has to accept a connect at one of its addresses: a LAN
# round trip is a millisecond, and a dead port answers nothing at all.
PROBE_TIMEOUT = 1

# The main Milo's own IPv4 addresses, wired first, as its last heartbeat answer
# listed them, one per line. The address watcher dials the next one when the
# stream's own stops answering: mDNS keeps answering a server's wired address for
# seconds after its cable has gone. And they are kept on the card, because they
# are the only way back to the server where mDNS never answers: a mesh router
# that filters multicast leaves a restarted speaker looking for milo.local in
# vain, and the heartbeat that would teach it the addresses again needs the
# server first. Read by `milo-client-snapclient-launcher` too, after its own
# lookup fails.
SERVER_ADDRESSES_FILE = Path("/var/lib/milo-client/server-addresses")
# None until read from the card, at the first call that needs them; and what the
# card holds as far as this process knows, None when a write to it failed. Read
# and written from worker threads — the heartbeat's and the watcher's — so only
# under the lock.
_server_addresses: Optional[tuple[str, ...]] = None
_stored: Optional[tuple[str, ...]] = None
_addresses_lock = threading.Lock()
# Whether the last write to the card failed: a card that stays full or read-only
# is reported once, not at every heartbeat answer that tries again.
_store_failing = False


def _ipv4(addresses) -> tuple[str, ...]:
    kept = []
    for address in addresses:
        try:
            kept.append(str(ipaddress.IPv4Address(address)))
        except ipaddress.AddressValueError:
            logger.debug(f"Ignoring a server address that is not IPv4: {address!r}")
    return tuple(kept)


def server_addresses_in(answer: dict) -> list:
    """What a heartbeat answer lists as the server's addresses, wired first."""
    return answer["server_addresses"]


def remember_server_addresses(addresses) -> None:
    """Keep the IPv4 ones of the server's `addresses`, in their order, on the card too. Blocking.

    Written only when they changed: a heartbeat answers every 15 s, and each
    write is a write to the SD card. A write that fails keeps them in memory,
    which is all this run needs, and is tried again at the next answer.
    """
    global _server_addresses
    with _addresses_lock:
        _load()  # what the card holds, read once
        _server_addresses = _ipv4(addresses)
        if _server_addresses != _stored:
            _store(_server_addresses)


def _store(addresses: tuple[str, ...]) -> None:
    global _stored, _store_failing
    try:
        write_atomic(SERVER_ADDRESSES_FILE, "".join(f"{address}\n" for address in addresses))
    except OSError as e:
        if not _store_failing:
            logger.warning(f"Cannot keep the server's addresses on the card ({e}): a restart "
                           "on a LAN without mDNS will not find the server")
        else:
            logger.debug(f"Still cannot keep the server's addresses on the card: {e}")
        _stored, _store_failing = None, True
        return
    if _store_failing:
        logger.info("The server's addresses are kept on the card again")
    _stored, _store_failing = addresses, False


def _load() -> None:
    """Read the card's addresses into memory, at the first call only; under the lock."""
    global _server_addresses, _stored
    if _server_addresses is not None:
        return
    try:
        lines = SERVER_ADDRESSES_FILE.read_text(encoding="ascii").split()
    except FileNotFoundError:
        lines = []
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"Cannot read the server's addresses kept on the card: {e}")
        _server_addresses, _stored = (), None  # rewritten at the next answer
        return
    _server_addresses = _stored = _ipv4(lines)


def known_server_addresses() -> tuple[str, ...]:
    """The main Milo's addresses, wired first, as its last heartbeat answer listed them. Blocking.

    Read from the card at the first call, so a restarted speaker knows them
    before any heartbeat; empty when none was ever answered.
    """
    with _addresses_lock:
        _load()
        return _server_addresses


async def answers(address: str) -> bool:
    """Whether the main Milo accepts a TCP connection at `address` within the probe timeout.

    The backend's port is dialled, not snapserver's: a probe there would log a
    session error on the server for every pass of a loss window.
    """
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(address, MILO_PRINCIPAL_PORT), PROBE_TIMEOUT)
    except (OSError, TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return True


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


def _resolve_target(target: str) -> Optional[str]:
    """`target` as an IPv4 address: itself when it is one, else what the resolver answers. Blocking."""
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
    return None


async def resolve_milo_principal() -> str:
    """Find the main Milo's IPv4 address, in this order:

    1. MILO_PRINCIPAL_IP, from the unit's EnvironmentFile, when it is a literal
       address — an operator pinned it, and nothing else is asked;
    2. that name resolved, `milo.local` when the file names none — what
       `milo-first-boot` writes when it converts a flashed card into a satellite;
    3. the first of the server's last known addresses that accepts a connect,
       in the order its heartbeat answer listed them (wired first) — the way
       back where mDNS never answers.

    Raises RuntimeError, naming the target, when all three fail — never a
    guess: the address watcher reads an address found here as the server's,
    and restarts a room to follow it.
    """
    target = os.environ.get("MILO_PRINCIPAL_IP") or MILO_PRINCIPAL_HOST
    resolved = await asyncio.to_thread(_resolve_target, target)
    if resolved is not None:
        return resolved
    known = await asyncio.to_thread(known_server_addresses)
    if known:
        answered = await asyncio.gather(*(answers(address) for address in known))
        for address, ok in zip(known, answered):
            if ok:
                logger.debug(f"Cannot resolve {target}: the server answers at {address}")
                return address
    raise RuntimeError(f"Cannot resolve {target}, and no address the server last listed answers")


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
            milo_ip = await resolve_milo_principal()

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
                        addresses = server_addresses_in(await resp.json())
                        await loop.run_in_executor(None, remember_server_addresses, addresses)
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
