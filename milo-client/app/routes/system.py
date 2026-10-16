"""
System routes for Milo Client: SSH, the `milo` account password, the links the
unit is on, and the server's WiFi kept as a standby link.

The satellite has no UI of its own, so these are driven from the server's
client panel, exactly like the server's own `/api/system/ssh` and
`/api/system/password` are driven from its settings. The image ships with SSH
off (pi-gen `ENABLE_SSH=0`); without this surface a flashed satellite could only
be opened by writing a `ssh` file onto its boot partition. The WiFi is pushed by
the server on its own, never from a panel: a satellite adopted over Ethernet
then falls back to the WiFi when its cable goes.
"""
import asyncio
import logging
import os
import pwd

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

SSH_UNIT = "ssh.service"
SET_PASSWORD_CMD = "/usr/local/bin/milo-set-password"
JOIN_WIFI_CMD = "/usr/local/bin/milo-client-join-wifi"
ETHERNET_INTERFACE = "eth0"
WIFI_INTERFACE = "wlan0"
# The image's first user, whose password the helper sets. A satellite provisioned
# before the pi-gen image may not have it (Canapé's only account is milo-client).
LOGIN_USER = "milo"
# Written by milo-set-password. Absent means the `milo` account still carries
# the factory password, which is the same on every unit and published.
PASSWORD_CHANGED_MARKER = "/var/lib/milo/password-changed"
# Bounds the server's relay is sized on: a read is two of these in parallel.
SYSTEMCTL_READ_TIMEOUT_S = 5
SYSTEMCTL_WRITE_TIMEOUT_S = 15
NMCLI_READ_TIMEOUT_S = 5
# A handful of nmcli calls, plus the country on its first push only; the
# helper never waits for the link to come up. The server's push is sized on it.
JOIN_WIFI_TIMEOUT_S = 20


class SshRequest(BaseModel):
    """Request body for PUT /api/system/ssh."""
    enabled: bool


class PasswordRequest(BaseModel):
    """Request body for POST /api/system/password — the bounds milo-set-password enforces."""
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def validate_printable(cls, v):
        if not v.isprintable():
            raise ValueError("password contains a non-printable character")
        return v


class WifiRequest(BaseModel):
    """Request body for PUT /api/system/wifi — the bounds milo-client-join-wifi enforces.

    `password` is empty for an open network; `country` is empty when the server
    has none set, and the regulatory domain is then left alone.
    """
    ssid: str = Field(..., min_length=1, max_length=32)
    password: str = Field("", max_length=64)
    country: str = Field("", pattern=r"^([A-Z0-9]{2})?$")

    @field_validator("ssid", "password")
    @classmethod
    def validate_single_line(cls, v):
        # The helper reads one line per field: a newline would shift the next one.
        if "\n" in v or "\r" in v:
            raise ValueError("contains a line break")
        return v


def _login_user():
    """The account SSH logs into, or None on a unit that has none to offer."""
    try:
        pwd.getpwnam(LOGIN_USER)
    except KeyError:
        return None
    return LOGIN_USER


async def _run(argv, timeout, stdin=None):
    """(returncode, stderr) of a child, killed and reaped if it outlives `timeout`."""
    returncode, _, stderr = await _run_capturing(argv, timeout, stdin)
    return returncode, stderr


async def _run_capturing(argv, timeout, stdin=None):
    """(returncode, stdout, stderr) of a child, killed and reaped if it outlives `timeout`."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        # The command itself, past a leading sudo — never one of its flags.
        name = argv[1] if argv[0] == "sudo" else argv[0]
        logger.error("%s did not answer within %s s", name, timeout)
        raise HTTPException(status_code=500, detail=f"{name} did not answer within {timeout} s")
    return proc.returncode, stdout.decode().strip(), stderr.decode().strip()


async def _ssh_state() -> dict:
    # `systemctl is-*` needs no root, and the two reads are independent.
    (enabled, _), (active, _) = await asyncio.gather(
        _run(("systemctl", "is-enabled", SSH_UNIT), SYSTEMCTL_READ_TIMEOUT_S),
        _run(("systemctl", "is-active", SSH_UNIT), SYSTEMCTL_READ_TIMEOUT_S),
    )
    return {
        "enabled": enabled == 0,
        "active": active == 0,
        "login_user": _login_user(),
        "password_is_default": not os.path.exists(PASSWORD_CHANGED_MARKER),
    }


async def _device(interface: str):
    """(connection name, IPv4 address) of a connected interface, None when it is not.

    Read plain: NetworkManager shows device state to any user.
    """
    returncode, stdout, stderr = await _run_capturing(
        ("nmcli", "-t", "-e", "no", "-f", "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS",
         "device", "show", interface),
        NMCLI_READ_TIMEOUT_S,
    )
    if returncode != 0:
        logger.warning("Cannot read %s: %s", interface, stderr or f"exit code {returncode}")
        return None

    fields = {}
    for line in stdout.splitlines():
        key, _, value = line.partition(":")
        fields.setdefault(key, value)
    # "100 (connected)": any other state carries no link, whatever profile it names.
    if not fields.get("GENERAL.STATE", "").startswith("100 "):
        return None
    address = fields.get("IP4.ADDRESS[1]", "").split("/")[0]
    return fields.get("GENERAL.CONNECTION", ""), address or None


async def _network_state() -> dict:
    """The links this unit is on: Ethernet's address, the WiFi's network and address."""
    ethernet, wifi = await asyncio.gather(_device(ETHERNET_INTERFACE), _device(WIFI_INTERFACE))
    wifi_state = None
    if wifi is not None:
        connection, address = wifi
        # A profile's non-secret settings are readable plain too.
        returncode, ssid, stderr = await _run_capturing(
            ("nmcli", "-e", "no", "-g", "802-11-wireless.ssid", "connection", "show", "id", connection),
            NMCLI_READ_TIMEOUT_S,
        )
        if returncode != 0:
            logger.warning("Cannot read the SSID of %s: %s", WIFI_INTERFACE, stderr or f"exit code {returncode}")
        wifi_state = {"ssid": ssid if returncode == 0 and ssid else None, "ip": address}
    return {
        "ethernet": None if ethernet is None else {"ip": ethernet[1]},
        "wifi": wifi_state,
    }


def create_system_router() -> APIRouter:
    """Creates the router for SSH, the account password and the network links."""
    router = APIRouter(prefix="/api/system", tags=["system"])
    # An admission and the server's own WiFi coming up can push at once; two
    # helpers interleaving their delete and add would leave two profiles.
    wifi_lock = asyncio.Lock()

    @router.get("/ssh")
    async def get_ssh():
        """SSH state, the account it logs into, and whether that account still has the factory password."""
        return {"status": "success", "data": await _ssh_state()}

    @router.put("/ssh")
    async def set_ssh(request: SshRequest):
        """Open or close SSH (enable + start, or disable + stop).

        Not gated on the factory password, for the reason the server's own route
        gives: nothing on this API authenticates, so a refusal would stop nobody
        on the LAN while standing in the owner's way. The state carries
        `password_is_default` so the UI can say what an open door costs.
        """
        argv = (
            ("sudo", "systemctl", "enable", "--now", SSH_UNIT) if request.enabled
            else ("sudo", "systemctl", "disable", "--now", SSH_UNIT)
        )
        returncode, stderr = await _run(argv, SYSTEMCTL_WRITE_TIMEOUT_S)
        if returncode != 0:
            detail = stderr or f"exit code {returncode}"
            logger.error("systemctl %s ssh.service failed: %s", argv[2], detail)
            raise HTTPException(status_code=500, detail=f"Failed to {argv[2]} SSH: {detail}")

        logger.info("SSH %s", "enabled" if request.enabled else "disabled")
        return {"status": "success", "data": await _ssh_state()}

    @router.post("/password")
    async def set_password(request: PasswordRequest):
        """Set the `milo` account password (SSH login + sudo).

        The password goes to the helper on stdin: /proc/<pid>/cmdline is
        world-readable, so an argv would publish it to every process on the unit.
        """
        if _login_user() is None:
            logger.error("No %s account on this unit; its password cannot be set", LOGIN_USER)
            raise HTTPException(
                status_code=409,
                detail=f"This satellite has no '{LOGIN_USER}' account: it was provisioned "
                       "before the Milō image. Reflash it to get one.",
            )
        returncode, stderr = await _run(
            ("sudo", SET_PASSWORD_CMD), SYSTEMCTL_WRITE_TIMEOUT_S, stdin=request.password.encode()
        )
        if returncode != 0:
            detail = stderr or f"exit code {returncode}"
            logger.error("milo-set-password failed (rc=%s): %s", returncode, detail)
            raise HTTPException(status_code=500, detail=f"Failed to set password: {detail}")

        logger.info("Device password updated")
        return {"status": "success", "data": {"password_is_default": False}}

    @router.get("/network")
    async def get_network():
        """The links this satellite is on, Ethernet and WiFi, each None when down."""
        return {"status": "success", "data": await _network_state()}

    @router.put("/wifi")
    async def keep_wifi(request: WifiRequest):
        """Keep a WiFi network as an autoconnecting link, for every boot.

        Pushed by the server, with its own network, at every admission and every
        change of it — so the helper leaves a matching profile alone and never
        pulls wlan0 off a network it is already on. The credentials reach it on
        stdin: sudo journals every command line it runs, so an argv would keep
        the PSK in the log.
        """
        async with wifi_lock:
            returncode, stdout, stderr = await _run_capturing(
                ("sudo", JOIN_WIFI_CMD), JOIN_WIFI_TIMEOUT_S,
                stdin=f"{request.country}\n{request.ssid}\n{request.password}\n".encode(),
            )
        if returncode != 0:
            detail = stderr or f"exit code {returncode}"
            logger.error("milo-client-join-wifi failed for '%s' (rc=%s): %s", request.ssid, returncode, detail)
            raise HTTPException(status_code=500, detail=f"Failed to keep WiFi '{request.ssid}': {detail}")

        changed = stdout != "unchanged"
        if changed:
            logger.info("WiFi '%s' saved as a standby link", request.ssid)
        return {"status": "success", "data": {"changed": changed}}

    return router
