"""
Boot-time registration with main Milo.

On startup, the client registers itself with the main Milo server
so it appears as a pending speaker available for configuration.
Retries periodically until successful.
"""
import asyncio
import json
import logging
import socket

import aiohttp

logger = logging.getLogger(__name__)

HARDWARE_FILE = "/var/lib/milo-client/hardware.json"
MILO_PRINCIPAL_PORT = 8000
REGISTER_ENDPOINT = "/api/multiroom/register-client"
RETRY_INTERVAL = 30  # seconds


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
    """Resolve milo.local to an IP address."""
    try:
        results = socket.getaddrinfo("milo.local", MILO_PRINCIPAL_PORT, socket.AF_INET)
        if results:
            return results[0][4][0]
    except socket.gaierror:
        pass
    raise RuntimeError("Cannot resolve milo.local")


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


async def register_with_main_milo() -> None:
    """
    Register this client with the main Milo server.

    Retries every RETRY_INTERVAL seconds until successful.
    Runs as a background task during the application lifespan.
    """
    # Small delay to let the network settle after boot
    await asyncio.sleep(5)

    loop = asyncio.get_running_loop()

    while True:
        try:
            # Resolve main Milo (blocking mDNS — run in executor to avoid blocking event loop)
            milo_ip = await loop.run_in_executor(None, _resolve_milo_principal)

            # Get our own info (blocking file I/O — run in executor)
            mac_id = await loop.run_in_executor(None, _get_mac_address)
            local_ip = await loop.run_in_executor(None, _get_local_ip, milo_ip, MILO_PRINCIPAL_PORT)
            hw_config = await loop.run_in_executor(None, _read_hardware_config)

            payload = {
                "mac_id": mac_id,
                "ip": local_ip,
                "hardware_configured": hw_config["hardware_configured"],
                "audio_id": hw_config["audio_id"],
                "volume_control": hw_config["volume_control"],
            }

            url = f"http://{milo_ip}:{MILO_PRINCIPAL_PORT}{REGISTER_ENDPOINT}"

            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status == 200:
                        logger.info(
                            f"Registered with main Milo at {milo_ip} "
                            f"(mac={mac_id}, ip={local_ip}, audio={hw_config['audio_id']})"
                        )
                        return
                    else:
                        body = await resp.text()
                        logger.warning(
                            f"Registration failed (HTTP {resp.status}): {body}"
                        )

        except Exception as e:
            logger.warning(f"Registration attempt failed: {e}")

        logger.info(f"Retrying registration in {RETRY_INTERVAL}s...")
        await asyncio.sleep(RETRY_INTERVAL)
