"""Probe route — what this satellite knows about its own link and timing.

The server sizes one Snapcast buffer for the whole house, so it has to know the
worst link in it. It cannot work that out from outside: a satellite with both
interfaces up sits on one subnet, and a probe addressed to its Wi-Fi IP is
answered over whichever route wins the metric contest. Measured on a unit whose
`eth0` carries metric 100 against `wlan0`'s 600: probing the Wi-Fi address
reported 0.34 ms and no loss, while the same link forced from this side reported
10.4% loss and 5 ms. An outside-in probe measures the *best* path a satellite
has, never the one its audio takes.

So the interface reported here is resolved by asking the kernel which route
reaches the caller -- the server itself, whose address FastAPI hands us. Not
"which interfaces are up", which would answer two.

Everything is bounded and nothing fails the route: what cannot be read is named
in `unavailable` and the rest still arrives, the same contract `/diagnostic`
already follows. A missing field costs the server a conservative default; a 500
would cost it the whole analysis.

Scheduling is sampled in a worker thread rather than on the event loop, because
the loop's own latency is not what audio pays. This is still ordinary userspace
timing, not the SCHED_FIFO thread snapclient runs -- it overstates the tail
snapclient sees, which is the safe direction for a value that widens a buffer.
"""
import asyncio
import contextlib
import logging
import platform
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

PROBE_TIMEOUT = 2.0

# 400 intervals of 2 ms is under a second of wall clock and still resolves a
# tail: the server calls this once per analysis, never in a loop.
SCHED_SAMPLES = 400
SCHED_INTERVAL_S = 0.002


async def _run(args, timeout: float = PROBE_TIMEOUT) -> Optional[str]:
    """stdout of `args`, or None. exec, never a shell."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        return None
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        with contextlib.suppress(ProcessLookupError):
            await proc.wait()
        return None
    if proc.returncode != 0:
        return None
    return stdout.decode("utf-8", errors="ignore")


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None


async def _interface_towards(peer: Optional[str]) -> Optional[str]:
    """The interface the kernel would use to answer `peer`.

    This is the whole point of the route: on a dual-homed satellite it is the
    only way to learn which link the audio actually crosses.
    """
    if not peer:
        return None
    out = await _run(["ip", "-o", "route", "get", peer])
    if not out:
        return None
    match = re.search(r"\bdev\s+(\S+)", out)
    return match.group(1) if match else None


async def _wireless_link(interface: str) -> Dict[str, Optional[float]]:
    """Negotiated rate (Mbit/s) and signal quality (%) of the associated AP.

    `iw` is not on the satellite image, so this comes from NetworkManager. The
    row is picked on IN-USE (`*`), never on ACTIVE: ACTIVE is translated, so a
    unit running a French locale answers `oui` and a parser keyed on `yes`
    silently finds no active network -- reporting an unknown rate on a perfectly
    healthy radio.

    Without the rate the server assumes a plain 802.11n link for every Wi-Fi
    satellite, and every analysis on a wireless fleet ends by warning that
    values had to be guessed.

    The signal is reported as NetworkManager's percentage, not the dBm in
    /proc/net/wireless, because a percentage is what the interface's existing
    WifiSignal bars take. Converting dBm to bars on the frontend would invent a
    curve here that nothing else in the app uses.
    """
    out = await _run(["nmcli", "-t", "-f", "IN-USE,RATE,SIGNAL", "dev", "wifi"])
    link: Dict[str, Optional[float]] = {"rate_mbps": None, "signal_percent": None}
    if not out:
        return link
    for line in out.splitlines():
        fields = line.split(":")
        if len(fields) < 3 or fields[0].strip() != "*":
            continue
        match = re.match(r"\s*([\d.]+)", fields[1])
        if match:
            with contextlib.suppress(ValueError):
                link["rate_mbps"] = float(match.group(1))
        with contextlib.suppress(ValueError):
            link["signal_percent"] = float(fields[2].strip())
        break
    return link


def _sample_scheduling() -> Dict[str, float]:
    """Overshoot of a fixed interval, in milliseconds.

    Stands in for how late a thread on this machine can be woken. The caller
    turns the tail into the client's ALSA buffer size.
    """
    overshoot: List[float] = []
    for _ in range(SCHED_SAMPLES):
        start = time.perf_counter()
        time.sleep(SCHED_INTERVAL_S)
        overshoot.append((time.perf_counter() - start - SCHED_INTERVAL_S) * 1000.0)
    overshoot.sort()
    last = len(overshoot) - 1
    return {
        "p50_ms": round(overshoot[last // 2], 3),
        "p99_ms": round(overshoot[int(last * 0.99)], 3),
        "max_ms": round(overshoot[last], 3),
        "samples": len(overshoot),
    }


def create_probe_router() -> APIRouter:
    """Create the probe router."""
    router = APIRouter(tags=["probe"])

    @router.get("/probe")
    async def get_probe(request: Request):
        """This satellite's link and timing, for the server's calibration."""
        unavailable: List[str] = []
        peer = request.client.host if request.client else None
        interface = await _interface_towards(peer)

        link: Dict[str, object] = {
            "interface": interface,
            "kind": None,
            "speed_mbps": None,
            "signal_percent": None,
        }

        if interface is None:
            unavailable.append("link.interface")
        else:
            wireless = Path(f"/sys/class/net/{interface}/wireless").exists()
            link["kind"] = "wifi" if wireless else "ethernet"
            if wireless:
                wifi = await _wireless_link(interface)
                link["speed_mbps"] = wifi["rate_mbps"]
                link["signal_percent"] = wifi["signal_percent"]
                if link["signal_percent"] is None:
                    unavailable.append("link.signal_percent")
            else:
                speed = _read(Path(f"/sys/class/net/{interface}/speed"))
                with contextlib.suppress(TypeError, ValueError):
                    link["speed_mbps"] = int(speed)
            if link["speed_mbps"] is None:
                unavailable.append("link.speed_mbps")

        try:
            scheduling = await asyncio.to_thread(_sample_scheduling)
        except Exception:
            logger.warning("scheduling sample failed", exc_info=True)
            scheduling = None
            unavailable.append("scheduling")

        return {
            "hostname": platform.node(),
            "link": link,
            "scheduling": scheduling,
            "unavailable": unavailable,
        }

    return router
