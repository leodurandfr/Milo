# backend/sources/music_library/discovery.py
"""mDNS/Bonjour discovery of SMB/NFS servers on the LAN.

The "Add a share" settings screen calls this to offer a pick-list of servers
found on the network, so the user taps their NAS instead of typing its address.
Discovery is a pure convenience — it only prefills the add-share form. It never
mounts anything and never touches credentials.

Implementation mirrors :mod:`backend.core.system.hostname_conflict`: browse
Avahi (already running on the box for milo.local) via ``avahi-browse``, parse its
parseable output, and fail open — no avahi-utils, a timeout, or an unparseable
line all yield an empty list, so the form still works with manual entry. No new
package: avahi-utils ships with the image.

Lines are read as they are printed rather than after the process exits, because
``avahi-browse -t`` does not terminate until *every* advertised record has
resolved, and one record that never resolves holds it open for the resolver's
full timeout. Measured on the owner's LAN: the two SMB servers were printed
20 ms in, and the run still took 5.02 s every time — an IPv6 record the NAS
advertises but never answers for. Collecting only at exit therefore returned
nothing at all on a network where both servers had already been found.
"""
import asyncio
import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger("source.music_library.discovery")

# How long to keep reading. Servers from the daemon's warm cache arrive in the
# first few milliseconds, so this only caps the wait for a straggler (or for an
# unresolvable record, which pins every run to the deadline) — kept short so the
# "Find on network" button stays snappy. Whatever arrived by then is kept.
BROWSE_TIMEOUT_S = 2.5

# Avahi service type → the ShareRequest `type` discriminator it maps to.
_SERVICE_TYPES = {
    "_smb._tcp": "cifs",
    "_nfs._tcp": "nfs",
}

# avahi's parseable output escapes bytes in the service name as `\DDD` (decimal),
# e.g. a space is `\032` — turn those back into characters for a readable label.
_ESCAPE_RE = re.compile(r"\\(\d{3})")


async def discover_servers() -> List[Dict[str, str]]:
    """Browse the LAN for SMB and NFS servers.

    Returns a de-duplicated, name-sorted list of ``{name, host, address, type}``.
    ``host`` is what the form should use and ``address`` is the raw IPv4 shown
    as a hint — they are deliberately equal: the advertised ``.local`` name is
    never used as the host, because mount.cifs and smbclient resolve through
    getaddrinfo, which does not answer ``.local`` on this stack (see
    _parse_resolved). Returns ``[]`` when discovery is unavailable.
    """
    # Browse each service type concurrently (halves the wall-clock vs sequential).
    # Result order matches _SERVICE_TYPES order, so dedup precedence is stable.
    browsed = await asyncio.gather(
        *(_browse(service, share_type) for service, share_type in _SERVICE_TYPES.items())
    )
    found: Dict[tuple, Dict[str, str]] = {}
    for servers in browsed:
        for server in servers:
            # One NAS often answers on several interfaces — dedup on (host, type).
            found.setdefault((server["host"], server["type"]), server)
    return sorted(found.values(), key=lambda s: s["name"].lower())


async def _browse(service_type: str, share_type: str) -> List[Dict[str, str]]:
    """Run one ``avahi-browse`` for a service type; [] on any failure.

    Line-buffered (``stdbuf -oL``) and consumed line by line: what has been
    printed when the deadline passes is kept, since the process itself may stay
    alive long after every reachable server has been reported.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "stdbuf", "-oL", "avahi-browse", "-rt", "-p", service_type,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.debug("stdbuf not available — share discovery disabled")
        return []

    # A missing avahi-browse is not a FileNotFoundError here (stdbuf is the one
    # exec'd); it exits 127 having printed nothing, which reads as "no servers".
    out: List[Dict[str, str]] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + BROWSE_TIMEOUT_S
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if not raw:  # EOF — the browse ended on its own
                break
            server = _parse_resolved(raw.decode("utf-8", errors="ignore"), share_type)
            if server is not None:
                out.append(server)
    except Exception as exc:  # never let discovery break the (resilient) route
        logger.debug("avahi-browse failed for %s: %s", service_type, exc)
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
    return out


def _parse_resolved(line: str, share_type: str) -> Optional[Dict[str, str]]:
    """Parse one resolved (``=``) line of avahi-browse parseable output.

    Format: ``=;<iface>;<proto>;<name>;<type>;<domain>;<fqdn>;<ip>;<port>;<txt>``.
    IPv6 rows are dropped (the mount host is IPv4); rows without an address are
    ignored.
    """
    if not line.startswith("="):
        return None
    fields = line.split(";")
    if len(fields) < 8:
        return None
    if fields[2] != "IPv4":
        return None
    name = _unescape(fields[3])
    fqdn = fields[6].rstrip(".")
    address = fields[7]
    if not address:
        return None
    return {
        "name": name or fqdn or address,
        # Use the IPv4 address as the host, NOT the mDNS .local name: smbclient
        # and mount.cifs both resolve via getaddrinfo, which does not answer
        # <name>.local on this stack (only getent/avahi-resolve do), so a .local
        # host fails to connect. The IP always works.
        "host": address,
        "address": address,
        "type": share_type,
    }


def _unescape(value: str) -> str:
    return _ESCAPE_RE.sub(lambda m: chr(int(m.group(1))), value)
