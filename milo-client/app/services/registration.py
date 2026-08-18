"""
Boot-time registration with main Milo.

On startup, the client registers itself with the main Milo server
so it appears as a pending speaker available for configuration.
Sends a heartbeat every HEARTBEAT_INTERVAL seconds so the server
can detect when this client goes offline.
"""
import asyncio
import ipaddress
import json
import logging
import os
import socket

import aiohttp

logger = logging.getLogger(__name__)

HARDWARE_FILE = "/var/lib/milo-client/hardware.json"
IDENTITY_FILE = "/var/lib/milo-client/identity.json"
MILO_PRINCIPAL_HOST = "milo.local"
MILO_PRINCIPAL_PORT = 8000
REGISTER_ENDPOINT = "/api/multiroom/register-client"
RETRY_INTERVAL = 30  # seconds (before first successful registration)
HEARTBEAT_INTERVAL = 15  # seconds (after successful registration)


def _get_mac_address() -> str:
    """Read MAC address from eth0, fallback to wlan0."""
    for iface in ("eth0", "wlan0"):
        try:
            with open(f"/sys/class/net/{iface}/address") as f:
                mac = f.read().strip()
                if mac and mac != "00:00:00:00:00:00":
                    return mac
        except FileNotFoundError:
            continue
    raise RuntimeError("No MAC address found on eth0 or wlan0")


def _get_local_ip(remote_ip: str, remote_port: int) -> str:
    """Get the local IP used to reach the main Milo (most reliable routable IP)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((remote_ip, remote_port))
        return s.getsockname()[0]


def _resolve_milo_principal() -> str:
    """Resolve the main Milo to an IPv4 address.

    MILO_PRINCIPAL_IP comes from the unit's EnvironmentFile and carries either a
    literal IP (`install-client.sh --server`, for a LAN where mDNS does not work)
    or the string "milo.local" (discovery succeeded) — both forms are accepted.
    A flashed unit has no env entry and falls back to mDNS.
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
            milo_ip = await loop.run_in_executor(None, _resolve_milo_principal)

            # Get our own info (blocking file I/O — run in executor)
            mac_id = await loop.run_in_executor(None, _get_mac_address)
            local_ip = await loop.run_in_executor(None, _get_local_ip, milo_ip, MILO_PRINCIPAL_PORT)
            hw_config = await loop.run_in_executor(None, _read_hardware_config)
            identity = await loop.run_in_executor(None, _read_identity)

            payload = {
                "mac_id": mac_id,
                "ip": local_ip,
                "hardware_configured": hw_config["hardware_configured"],
                "audio_id": hw_config["audio_id"],
                "volume_control": hw_config["volume_control"],
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
