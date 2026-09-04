# backend/core/system/diagnostic/satellite.py
"""Asking each satellite for its own diagnostic block.

A satellite is where the maintainer is blindest: it has no log surface in the UI
at all, so nothing it knows leaves it except over this HTTP call. It is also the
part most likely to be the reason a report is being generated, and the part most
likely not to answer — so an absent satellite has to produce a *paragraph*, not
a gap.

Three verdicts, not one, because they point at three different actions:

  * no answer at all — powered off, off the network, or its API is down;
  * an HTTP status — it is alive and refused, which is a fault on the satellite;
  * 404 — it is alive and serving, but the code it runs predates this route. A
    satellite is always updated BY the version it is already running, so a push
    lands one release behind; this is what that looks like from here, and it is
    a fleet update, not a repair.

The transport is aiohttp directly rather than EqualizerClientProxyService: that
service is the DSP command path, with its own fixed 10 s timeout and its
multiroom-enabled gate, and a diagnostic must answer while multiroom is off and
must bound its own wait. `SatelliteUpdateService` reaches satellites the same
way for the same kind of reason.
"""
import asyncio
import logging
from typing import Any, Dict, List

import aiohttp

from backend.config.constants import CLIENT_API_PORT

logger = logging.getLogger(__name__)

# A satellite that answers at all answers in well under a second on a LAN; the
# rest of this budget is its own subprocess work (journalctl, systemctl).
SATELLITE_TOTAL_TIMEOUT = 6.0
# A powered-off host sends no RST, so nothing but this bounds the connect: it is
# what keeps an unplugged speaker from costing the full total.
SATELLITE_CONNECT_TIMEOUT = 2.0


async def collect_all(clients, labels: Dict[str, str]) -> List[Dict[str, Any]]:
    """One result per remote client, all probed in parallel.

    Parallel so the wall clock is one satellite's timeout however many there
    are — a household with four speakers must not wait four times over.
    """
    remote = [c for c in clients if not getattr(c, "is_local", False) and c.ip]
    if not remote:
        return []

    timeout = aiohttp.ClientTimeout(
        total=SATELLITE_TOTAL_TIMEOUT, sock_connect=SATELLITE_CONNECT_TIMEOUT
    )
    async with aiohttp.ClientSession(timeout=timeout) as session:
        results = await asyncio.gather(
            *(_probe(session, client) for client in remote), return_exceptions=True
        )

    out = []
    for client, result in zip(remote, results):
        if isinstance(result, Exception):
            result = {"reachable": False, "reason": f"probe failed: {result}"}
        result["label"] = labels.get(client.mac_id, client.mac_id)
        result["mac_id"] = client.mac_id
        result["ip"] = client.ip
        result["registry_online"] = client.online
        out.append(result)
    return out


async def _probe(session: aiohttp.ClientSession, client) -> Dict[str, Any]:
    # The path is spelled inline, not through a constant: the milo-client
    # contract test resolves satellite calls out of the f-string itself, and a
    # name it cannot follow makes this call invisible to the only check that
    # the satellite still serves it.
    url = f"http://{client.ip}:{CLIENT_API_PORT}/diagnostic"
    try:
        async with session.get(url) as response:
            if response.status == 404:
                return {
                    "reachable": False,
                    "reason": (
                        "this satellite answers, but the code it runs predates the "
                        "diagnostic route — push the client app to the fleet"
                    ),
                }
            if response.status != 200:
                return {
                    "reachable": False,
                    "reason": f"answered HTTP {response.status}",
                }
            payload = await response.json()
            return {
                "reachable": True,
                "hostname": payload.get("hostname"),
                "text": payload.get("text") or "",
                "unavailable": payload.get("unavailable") or [],
            }
    except asyncio.TimeoutError:
        return {
            "reachable": False,
            "reason": (
                f"no answer within {SATELLITE_TOTAL_TIMEOUT:g} s — powered off, "
                "off the network, or its API is down"
            ),
        }
    except aiohttp.ClientError as e:
        return {"reachable": False, "reason": f"cannot be reached ({e})"}


def render(results: List[Dict[str, Any]], budget: int) -> str:
    """One block per satellite, sharing `budget` bytes equally.

    Equally rather than first-come: with two satellites and one of them noisy,
    a shared pool hands the whole budget to whichever the loop reached first,
    and the other — possibly the broken one — arrives empty.
    """
    if not results:
        return "(no remote client is registered)"

    per_satellite = max(budget // len(results), 1_000)
    blocks = []
    for result in results:
        header = (
            f"--- {result['label']} ({result['mac_id']} at {result['ip']}, "
            f"registry says {'online' if result['registry_online'] else 'offline'}) ---"
        )
        if not result.get("reachable"):
            blocks.append(f"{header}\nNOT COLLECTED — {result['reason']}")
            continue

        body = result.get("text", "")
        raw = body.encode("utf-8")
        if len(raw) > per_satellite:
            cut = raw[:per_satellite].rsplit(b"\n", 1)[0].decode("utf-8", errors="ignore")
            body = f"{cut}\n(truncated: {len(raw) - len(cut.encode('utf-8'))} bytes cut)"
        missing = result.get("unavailable") or []
        if missing:
            body += "\nnot collected on this satellite: " + ", ".join(
                f"{item.get('section')} ({item.get('reason')})" for item in missing
            )
        blocks.append(f"{header}\nhostname: {result.get('hostname') or '-'}\n{body}")
    return "\n\n".join(blocks)


def unavailable_names(results: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """What the UI lists under the buttons, one entry per satellite that failed."""
    return [
        {"section": f"satellite {r['label']}", "reason": r["reason"]}
        for r in results if not r.get("reachable")
    ]


def labels_for(clients) -> Dict[str, str]:
    """mac_id → `client-N`, assigned in MAC order.

    Positional, so the report never carries the room name the user typed, and
    deterministic, so two exports of the same unit name the same speaker the
    same way and the labels can be quoted back in a conversation.
    """
    ordered = sorted(clients, key=lambda c: c.mac_id)
    return {
        client.mac_id: f"client-{index}" + (" (local)" if client.is_local else "")
        for index, client in enumerate(ordered, start=1)
    }


def zone_labels_for(zones) -> Dict[str, str]:
    """zone id → `zone-N`, assigned in id order, same reasoning."""
    return {zone_id: f"zone-{index}" for index, zone_id in enumerate(sorted(zones), start=1)}
