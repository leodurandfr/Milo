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
import re
from typing import Any, Dict, List

import aiohttp

from backend.config.constants import CLIENT_API_PORT
from backend.core.system.diagnostic.render import keep_newest, round_robin

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

        body = fit_block(result.get("text", ""), per_satellite)
        missing = result.get("unavailable") or []
        if missing:
            body += "\nnot collected on this satellite: " + ", ".join(
                f"{item.get('section')} ({item.get('reason')})" for item in missing
            )
        blocks.append(f"{header}\nhostname: {result.get('hostname') or '-'}\n{body}")
    return "\n\n".join(blocks)


# The satellite's own layout (milo-client/app/routes/diagnostic.py): sections
# headed `[name]`, the journal last, one `--- unit ---` block per unit in it.
# Read from the text that route renders by
# tests/test_diagnostic_service.py::test_a_satellite_block_cut_to_its_share_keeps_its_newest_journal_lines,
# so a heading that moves on one side alone goes red rather than falling back.
JOURNAL_HEADING = "[journal]"
_UNIT_HEADING = re.compile(r"^--- (?P<unit>.+?) ---(?: \((?P<cut>\d+) older lines cut\))?$")
# Room for a heading's own cut count, which grows as lines are taken out.
_HEADING_SLACK = len(" (99999 older lines cut)") + 2


def fit_block(text: str, budget: int) -> str:
    """A satellite's block within `budget` bytes, its newest lines kept.

    The block ends with the satellite's journal, each unit's newest lines
    last, and those are the lines a report is generated for — a cut from the
    end drops them first. So the journal gives up its oldest lines, one unit
    at a time by `render.round_robin`, the rule the server's own journal is
    cut by, and the snapshot above it (identity, units, ALSA, CamillaDSP)
    stays whole. A block not in that layout — a journal line under no heading
    read here is one — or whose snapshot alone overruns, keeps its newest
    whole lines (`render.keep_newest`), and says how many it cut: a line is
    never dropped without a count.
    """
    if len(text.encode("utf-8")) <= budget:
        return text
    lines = text.splitlines()
    if JOURNAL_HEADING in lines:
        start = len(lines) - 1 - lines[::-1].index(JOURNAL_HEADING)
        head = "\n".join(lines[:start + 1])
        units: Dict[str, List[str]] = {}
        earlier: Dict[str, int] = {}
        current = None
        unread = 0
        for line in lines[start + 1:]:
            heading = _UNIT_HEADING.match(line)
            if heading:
                current = heading.group("unit")
                units[current] = []
                earlier[current] = int(heading.group("cut") or 0)
            elif line and current is not None:
                units[current].append(line)
            elif line:
                unread += 1
        room = budget - len(head.encode("utf-8")) - 1 - sum(
            len(f"--- {unit} ---".encode("utf-8")) + _HEADING_SLACK for unit in units
        )
        if units and not unread and room > 0:
            kept = round_robin(units, room)
            blocks = []
            for unit, unit_lines in units.items():
                lines_kept = kept.get(unit, [])
                cut = earlier[unit] + len(unit_lines) - len(lines_kept)
                blocks.append("\n".join([
                    f"--- {unit} ---" + (f" ({cut} older lines cut)" if cut else ""),
                    *lines_kept,
                ]))
            fitted = f"{head}\n" + "\n".join(blocks)
            if len(fitted.encode("utf-8")) <= budget:
                return fitted
    note = "({cut} older lines cut to fit the report)"
    kept, cut = keep_newest(lines, budget - len(note.format(cut=len(lines))) - 1)
    return "\n".join([note.format(cut=cut), *kept])


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
