# backend/core/multiroom/calibration_probe.py
"""Collect the readings `calibration.compute_configuration` turns into a config.

Two probes per remote client, run together across the fleet:

* ICMP, from here, for the pure network term. It is answered by the client's
  kernel, so it measures the wire and nothing above it.
* ``GET /probe`` on the satellite, for what only the satellite knows: which
  interface its traffic to this server actually leaves by, and how late a thread
  on it can be woken. Both were measured to be unknowable from outside -- a
  dual-homed satellite answers a probe addressed to its Wi-Fi IP over whichever
  route wins the metric contest, and ICMP never touches the scheduler at all.

A client that is online but cannot be probed **fails the whole analysis**. The
buffer is one server-wide value sized by the worst link, so a client left out of
the reading is a link that was never weighed -- and it is exactly as likely to
be the weak one. Skipping it and reporting the rest is the shape of a false
green, which is the one outcome this feature cannot afford.
"""
import asyncio
import logging
import re
import sys
from typing import Dict, List, Tuple

import aiohttp

from backend.config.constants import CLIENT_API_PORT
from backend.core.multiroom.calibration import ClientReading

logger = logging.getLogger(__name__)

# 150 probes at 5 Hz is 30 s of wall clock per client, run in parallel across
# the fleet. Enough samples to place a 99th percentile; short enough that the
# whole analysis stays inside the couple of minutes a person will wait.
PING_COUNT = 150
PING_INTERVAL_S = 0.2
PING_TIMEOUT_S = 1
PING_BUDGET_S = PING_COUNT * PING_INTERVAL_S + 20

# What the UI tells the user to expect. Derived from the probe that dominates
# the run — everything else happens alongside it — and never guessed on the
# frontend, which would restate a duration this module decides. Measured end to
# end on a three-speaker fleet: 31 s.
EXPECTED_DURATION_S = PING_COUNT * PING_INTERVAL_S + 2

PROBE_TIMEOUT_S = 10

# The satellite samples its own scheduling; this is the same measurement taken
# here for the local speaker. The two trees are independent deployments, so the
# satellite's copy cannot be imported -- duplicating the arithmetic is the cost
# of that separation, not an oversight. The satellite runs its copy in-process
# because its API is idle by construction; this one cannot, see
# `_sample_local_scheduling`.
LOCAL_SCHED_SAMPLES = 400
LOCAL_SCHED_INTERVAL_S = 0.002
LOCAL_SCHED_BUDGET_S = LOCAL_SCHED_SAMPLES * LOCAL_SCHED_INTERVAL_S + 15

# What a satellite that could not name its link is assumed to be. Wi-Fi is the
# pessimistic reading, and 72 Mbit/s is a plain 802.11n rate: a default that
# guessed Ethernet would hand the model the optimistic answer on exactly the
# unit it knows least about.
ASSUMED_LINK = "wifi"
ASSUMED_LINK_SPEED_MBPS = 72.0

_PING_TIME = re.compile(r"time=([\d.]+)\s*ms")


class NoRemoteClientError(RuntimeError):
    """There is no remote speaker to measure.

    Separate from CalibrationProbeError because the two mean opposite things to
    the user: this one is "there is nothing here to tune", the other is "a
    speaker that should have answered did not". Folded together, the second
    message was shown for the first case and the localized string for it was
    unreachable.
    """


class CalibrationProbeError(RuntimeError):
    """A client that is online could not be measured.

    Carries the client's display name so the UI can say which speaker stopped
    the analysis, rather than reporting a generic failure the user cannot act
    on.
    """


def _percentiles(values: List[float]) -> Tuple[float, float]:
    ordered = sorted(values)
    last = len(ordered) - 1
    return ordered[last // 2], ordered[last]


async def _sample_local_scheduling() -> float:
    """Worst overshoot of a fixed interval on this machine, in milliseconds.

    Runs in a separate interpreter, not in a thread here, and that is the whole
    point. Measured both ways on the same idle fleet: a thread inside this
    process reported 5.0 and 6.6 ms while the two satellites -- sampling inside
    their own, idle API processes -- reported 0.47 and 0.11 ms. What the thread
    was measuring is contention for this process's GIL, which the local speaker
    pays nothing for: snapclient is a separate C++ process at SCHED_FIFO 12.

    Left as it was, that artefact alone pushed the client ALSA buffer from 60 ms
    to 120 and the proposal from 180 ms to 300 -- on a fleet where 180 had been
    confirmed by listening. Worse than wrong, it was irreproducible: the same
    house got a different answer depending on what the backend happened to be
    doing that second.
    """
    code = (
        "import time\n"
        f"w=0.0\n"
        f"for _ in range({LOCAL_SCHED_SAMPLES}):\n"
        f"    s=time.perf_counter(); time.sleep({LOCAL_SCHED_INTERVAL_S})\n"
        f"    w=max(w,(time.perf_counter()-s-{LOCAL_SCHED_INTERVAL_S})*1000.0)\n"
        "print(w)\n"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", code,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), LOCAL_SCHED_BUDGET_S)
    except asyncio.TimeoutError:
        proc.kill()
        raise CalibrationProbeError("the server's scheduling sample did not finish") from None

    try:
        return float(stdout.decode().strip())
    except ValueError as exc:
        raise CalibrationProbeError("the server's scheduling sample was unreadable") from exc


async def _ping(ip: str) -> Tuple[float, float, float]:
    """(p50, max, loss_pct) of `PING_COUNT` probes to `ip`.

    Raises CalibrationProbeError when nothing came back: a host that answers no
    probe at all has not been measured, and a 100% loss reading fed to the model
    would be treated as a very bad link rather than as an absent one.
    """
    proc = await asyncio.create_subprocess_exec(
        "ping", "-n", "-c", str(PING_COUNT),
        "-i", str(PING_INTERVAL_S), "-W", str(PING_TIMEOUT_S), ip,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), PING_BUDGET_S)
    except asyncio.TimeoutError:
        proc.kill()
        raise CalibrationProbeError(f"ping to {ip} did not finish") from None

    times = [float(m) for m in _PING_TIME.findall(stdout.decode("utf-8", errors="ignore"))]
    if not times:
        raise CalibrationProbeError(f"no ICMP reply from {ip}")

    p50, worst = _percentiles(times)
    loss = 100.0 * (PING_COUNT - len(times)) / PING_COUNT
    return p50, worst, loss


async def _fetch_probe(session: aiohttp.ClientSession, ip: str) -> Dict:
    url = f"http://{ip}:{CLIENT_API_PORT}/probe"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=PROBE_TIMEOUT_S)) as response:
        if response.status != 200:
            raise CalibrationProbeError(f"{url} answered {response.status}")
        return await response.json()


async def _first_failure_cancels(*coros):
    """Run `coros` together; on the first failure cancel the rest, then raise.

    `asyncio.gather` propagates the first exception but leaves its siblings
    running. Here that sibling is a 30-second ping: when the HTTP probe refuses
    fast, the analysis reported the failure and the abandoned ping finished
    half a minute later with nobody left to read its exception. asyncio logs
    that at ERROR, and the WS log handler turns backend errors into a banner --
    so a single unreachable speaker produced a second, unexplained alert long
    after the screen had moved on.
    """
    tasks = [asyncio.ensure_future(coro) for coro in coros]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        if task.exception() is not None:
            raise task.exception()
    return tuple(task.result() for task in tasks)


async def _measure_remote(session: aiohttp.ClientSession, client) -> Tuple[ClientReading, List[str]]:
    """One remote client's reading, plus the fields that had to be assumed."""
    assumed: List[str] = []
    try:
        (p50, worst, loss), probe = await _first_failure_cancels(
            _ping(client.ip), _fetch_probe(session, client.ip)
        )
    except CalibrationProbeError as exc:
        # Re-raised with the speaker's name attached: the probes below know an
        # address and a URL, and "10.0.0.3 refused the connection" is not
        # something the person standing in the room can act on.
        raise CalibrationProbeError(f"{client.name}: {exc}") from exc
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as exc:
        raise CalibrationProbeError(f"{client.name}: {exc}") from exc

    link = (probe.get("link") or {})
    kind = link.get("kind")
    if kind not in ("ethernet", "wifi"):
        kind = ASSUMED_LINK
        assumed.append(f"{client.name}.link")

    speed = link.get("speed_mbps")
    if not isinstance(speed, (int, float)) or speed <= 0:
        speed = ASSUMED_LINK_SPEED_MBPS
        assumed.append(f"{client.name}.link_speed_mbps")

    scheduling = probe.get("scheduling") or {}
    sched_max = scheduling.get("max_ms")
    if not isinstance(sched_max, (int, float)):
        raise CalibrationProbeError(
            f"{client.name} did not report its scheduling; it cannot be weighed"
        )

    signal = link.get("signal_percent")

    return ClientReading(
        mac_id=client.mac_id,
        name=client.name,
        link=kind,
        signal_percent=float(signal) if isinstance(signal, (int, float)) else None,
        link_speed_mbps=float(speed),
        rtt_p50_ms=p50,
        rtt_max_ms=worst,
        loss_pct=loss,
        sched_max_ms=float(sched_max),
        is_local=False,
    ), assumed


class CalibrationProbeService:
    """Measures the fleet for the calibration model."""

    def __init__(self, client_registry):
        self._registry = client_registry

    async def measure(self) -> Tuple[List[ClientReading], List[str]]:
        """Read every online client, or fail naming the one that could not be.

        Returns the readings and the list of fields that had to be assumed
        rather than measured, so the UI can mark a proposal as partly inferred.
        """
        online = self._registry.get_online_clients()
        remote = [c for c in online if not c.is_local]
        if not remote:
            raise NoRemoteClientError("no remote speaker is online")

        assumed: List[str] = []
        readings: List[ClientReading] = []

        async with aiohttp.ClientSession() as session:
            results = await asyncio.gather(
                _sample_local_scheduling(),
                *[_measure_remote(session, c) for c in remote],
                return_exceptions=True,
            )

        local_sched, remote_results = results[0], results[1:]
        for outcome in remote_results:
            if isinstance(outcome, BaseException):
                logger.error("Calibration probe failed: %s", outcome)
                raise CalibrationProbeError(str(outcome)) from outcome
            reading, reading_assumed = outcome
            readings.append(reading)
            assumed.extend(reading_assumed)

        if isinstance(local_sched, BaseException):
            logger.error("Local scheduling sample failed: %s", local_sched)
            raise CalibrationProbeError("the server could not measure itself") from local_sched

        local = next((c for c in online if c.is_local), None)
        if local is not None:
            readings.append(ClientReading(
                mac_id=local.mac_id, name=local.name, link="ethernet",
                link_speed_mbps=0.0, rtt_p50_ms=0.0, rtt_max_ms=0.0, signal_percent=None,
                loss_pct=0.0, sched_max_ms=float(local_sched), is_local=True,
            ))

        return readings, assumed
