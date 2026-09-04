# backend/core/system/diagnostic/service.py
"""Assembling one pasteable file out of everything the appliance already knows.

The collection is not the hard part — errors.log, the journal, /proc/asound and
the registry all existed before this. What did not exist is a way for someone
who will never open a terminal to hand a maintainer a single artefact, and that
is what this composes.

Two properties are load-bearing, and both are about failure. A section that
cannot be collected is written into the file as NOT COLLECTED with its reason:
never dropped, because a missing heading reads as "nothing to report", which is
the opposite of what a failed probe means. And every section is bounded, because
the export is run precisely when something is hung — a NAS that stopped
answering, a satellite that is unplugged, a daemon that will not talk.
"""
import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.config.constants import ERROR_LOG_FILE, MUSIC_LIBRARY_DATA_FILE
from backend.core.system.diagnostic import collectors, probes, render, satellite
from backend.core.system.diagnostic.whitelist import (
    JOURNAL_EXTRA_UNITS,
    JOURNAL_INCLUDE_KERNEL,
    JOURNAL_UNIT_GLOB,
    Redactor,
)

logger = logging.getLogger(__name__)

REPORT_FORMAT = 1


class DiagnosticContext:
    """The services a collector may read, every one of them optional.

    Optional is the requirement, not a convenience: the report has to be
    produced by a backend that is partly dead, and a collector that assumed its
    service was wired would take the whole export down with it.
    """

    def __init__(self, **services):
        for name, value in services.items():
            setattr(self, name, value)
        self.labels: Dict[str, str] = {}
        self.zone_labels: Dict[str, str] = {}
        self.share_labels: Dict[str, str] = {}
        self.usb_labels: Dict[str, str] = {}
        # Applied to every line of the three free-text sections. Never optional:
        # the default still strips URL query strings.
        self.sanitize = Redactor()


class DiagnosticService:
    """Builds the report. Holds no state and spawns no background task."""

    # A collector is a few subprocesses or one loopback call; past this it is
    # wedged, and its section is worth less than the rest of the report.
    SECTION_TIMEOUT = 5.0
    # The backstop. Sections run in parallel, so the natural wall clock is one
    # slow section — this only fires if several wedge at once.
    TOTAL_TIMEOUT = 20.0

    def __init__(
        self,
        settings_service=None,
        hardware_service=None,
        state_machine=None,
        routing_service=None,
        camilladsp_service=None,
        snapcast_service=None,
        registry_service=None,
        version_service=None,
        connectivity_service=None,
        hostname_conflict_service=None,
    ):
        self._services = {
            "settings_service": settings_service,
            "hardware_service": hardware_service,
            "state_machine": state_machine,
            "routing_service": routing_service,
            "camilladsp_service": camilladsp_service,
            "snapcast_service": snapcast_service,
            "registry_service": registry_service,
            "version_service": version_service,
            "connectivity_service": connectivity_service,
            "hostname_conflict_service": hostname_conflict_service,
        }

    # ----------------------------------------------------------------- public

    async def generate(self) -> Dict[str, Any]:
        """The report text, and the list of sections that could not be collected.

        The list is returned as well as written into the file so the UI can name
        them under the buttons — a satellite that was asleep is something the
        person sending the report should see before they send it, not a silence.
        """
        ctx = DiagnosticContext(**self._services)
        clients = list(self._clients())
        ctx.labels = satellite.labels_for(clients)
        ctx.zone_labels = satellite.zone_labels_for(self._zone_ids())
        await self._build_redactor(ctx, clients)

        sections = self._sections()
        tasks = {
            title: asyncio.ensure_future(self._run_section(title, fn, ctx))
            for title, fn in sections
        }
        satellite_task = asyncio.ensure_future(
            satellite.collect_all(clients, ctx.labels)
        )

        await asyncio.wait(
            [*tasks.values(), satellite_task], timeout=self.TOTAL_TIMEOUT
        )

        blocks: List[str] = [self._header()]
        unavailable: List[Dict[str, str]] = []

        for title, _ in sections:
            task = tasks[title]
            if not task.done():
                task.cancel()
                reason = f"exceeded the {self.TOTAL_TIMEOUT:g} s export deadline"
                blocks.append(render.unavailable_section(title, reason))
                unavailable.append({"section": title, "reason": reason})
                continue
            body, reason = task.result()
            if reason:
                blocks.append(render.unavailable_section(title, reason))
                unavailable.append({"section": title, "reason": reason})
            else:
                blocks.append(render.section(title, body))

        blocks.append(self._satellites_block(satellite_task, unavailable))
        blocks.append(self._not_collected_block(unavailable))

        text, _ = render.fit_report(blocks)
        return {"report": text, "unavailable": unavailable}

    # ---------------------------------------------------------------- private

    def _sections(self) -> List[Tuple[str, Callable]]:
        """Declared order — it is the order the file reads in.

        Identity first because it is what a maintainer checks before anything
        else; the two free-text sections last because they are the long ones and
        everything above them should be readable without scrolling past a log.
        """
        return [
            ("IDENTITY", collectors.identity),
            ("VERSIONS", collectors.versions),
            ("HARDWARE", collectors.hardware),
            ("AUDIO PATH", collectors.audio_path),
            ("AUDIO SOURCES", collectors.sources),
            ("MULTIROOM", collectors.multiroom),
            ("STORAGE", collectors.storage),
            ("NETWORK", collectors.network),
            ("SETTINGS", collectors.settings_section),
            ("RECENT ERRORS (errors.log)", _errors_log),
            ("JOURNAL", _journal),
            ("PREVIOUS BOOT (errors)", _previous_boot),
        ]

    async def _run_section(self, title, fn, ctx) -> Tuple[str, Optional[str]]:
        """(body, None) or (\"\", reason) — a collector never takes the export down."""
        try:
            body = await asyncio.wait_for(fn(ctx), self.SECTION_TIMEOUT)
        except asyncio.TimeoutError:
            return "", f"did not answer within {self.SECTION_TIMEOUT:g} s"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("diagnostic section %s failed: %s", title, e)
            return "", f"{type(e).__name__}: {e}"
        return body, None

    async def _build_redactor(self, ctx, clients) -> None:
        """Label every excluded value this unit holds, then teach the text about it.

        Done before any section runs, because the labels the structured sections
        print and the substitutions the log sections apply have to be the same
        ones — a share called `share-1` in one half and `share-2` in the other
        would be worse than printing the name.
        """
        import json

        data = {}
        raw = await probes.read_file(MUSIC_LIBRARY_DATA_FILE)
        if raw:
            try:
                data = json.loads(raw)
            except ValueError as e:
                logger.warning("diagnostic could not parse the share list: %s", e)

        shares = data.get("shares", [])
        ctx.share_labels = {
            share["id"]: f"share-{index}"
            for index, share in enumerate(shares, start=1) if share.get("id")
        }
        known_usb = data.get("known_usb", {})
        ctx.usb_labels = {
            uuid: f"usb-{index}" for index, uuid in enumerate(sorted(known_usb), start=1)
        }

        usb_strings = {}
        for uuid, entry in known_usb.items():
            label = ctx.usb_labels[uuid]
            for key in ("name", "label", "mountpoint"):
                value = entry.get(key)
                if value:
                    usb_strings[value] = label

        zones = {}
        if self._services["registry_service"] is not None:
            zones = self._services["registry_service"].get_all_zones()

        ctx.sanitize = Redactor.build(
            client_labels=ctx.labels,
            client_names={c.mac_id: c.name for c in clients},
            zone_labels=ctx.zone_labels,
            zone_names={zone_id: zone.name for zone_id, zone in zones.items()},
            share_labels=ctx.share_labels,
            share_names={
                share["id"]: share.get("name") for share in shares if share.get("id")
            },
            usb_strings=usb_strings,
        )

    def _clients(self):
        registry = self._services["registry_service"]
        if registry is None:
            return []
        return registry.get_all_clients().values()

    def _zone_ids(self):
        registry = self._services["registry_service"]
        if registry is None:
            return []
        return list(registry.get_all_zones())

    def _header(self) -> str:
        return (
            "===== MILO DIAGNOSTIC REPORT =====\n"
            f"report format: {REPORT_FORMAT}\n"
            "no credential, no network name and no file name of yours is in this "
            "file; local IP and MAC addresses are, because a multiroom fault "
            "cannot be read without them\n"
        )

    def _satellites_block(self, task, unavailable: List[Dict[str, str]]) -> str:
        title = "SATELLITES"
        if not task.done():
            task.cancel()
            reason = f"exceeded the {self.TOTAL_TIMEOUT:g} s export deadline"
            unavailable.append({"section": title, "reason": reason})
            return render.unavailable_section(title, reason)
        try:
            results = task.result()
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"
            unavailable.append({"section": title, "reason": reason})
            return render.unavailable_section(title, reason)

        unavailable.extend(satellite.unavailable_names(results))
        return render.section(
            title, satellite.render(results, render.SATELLITE_BUDGET_BYTES)
        )

    def _not_collected_block(self, unavailable: List[Dict[str, str]]) -> str:
        if not unavailable:
            return render.section("NOT COLLECTED", "(nothing — every section answered)")
        body = "\n".join(f"{item['section']}: {item['reason']}" for item in unavailable)
        return render.section("NOT COLLECTED", body)


# --------------------------------------------------------------------------- #
# The three free-text sections
# --------------------------------------------------------------------------- #

async def _errors_log(ctx) -> str:
    """The tail of errors.log — WARNING and above, backend and frontend alike.

    The only durable trace the appliance keeps today, and the only place a Vue
    error handler's output lands.
    """
    out = await probes.run(["tail", "-n", "400", str(ERROR_LOG_FILE)], timeout=5.0)
    if out is None:
        raise RuntimeError(f"{ERROR_LOG_FILE} could not be read")
    lines = [
        render.cap_line(ctx.sanitize(line))
        for line in out.splitlines() if line.strip()
    ]
    kept, cut = render.keep_newest(lines, render.ERRORS_LOG_BUDGET_BYTES)
    if cut:
        kept.insert(0, f"({cut} older lines cut to fit the report)")
    return "\n".join(kept) or "(empty — nothing at WARNING or above)"


async def _journal(ctx) -> str:
    """Per-unit tails of Milō's own units, plus the kernel ring.

    The unit list is asked of the host, not restated from `system/`, so a unit
    added to the repo later is picked up without anyone editing this. What is
    NOT here is as deliberate: NetworkManager and wpa_supplicant log the SSID of
    every network they touch, so neither is admitted, and there is no
    unrestricted journal read anywhere in this module.
    """
    units = await probes.list_milo_units(JOURNAL_UNIT_GLOB, JOURNAL_EXTRA_UNITS)
    if not units:
        raise RuntimeError("systemd listed no milo unit")

    tails = await asyncio.gather(*(
        probes.journal_tail(units=[unit], lines=render.JOURNAL_LINES_PER_UNIT)
        for unit in units
    ))
    per_unit = {
        unit: [render.cap_line(ctx.sanitize(line)) for line in lines]
        for unit, lines in zip(units, tails) if lines
    }
    if JOURNAL_INCLUDE_KERNEL:
        kernel = await probes.journal_tail(
            kernel=True, lines=render.JOURNAL_LINES_PER_UNIT
        )
        if kernel:
            per_unit["kernel"] = [
                render.cap_line(ctx.sanitize(line)) for line in kernel
            ]

    kept = render.round_robin(per_unit, render.JOURNAL_BUDGET_BYTES)
    blocks = []
    for unit in sorted(per_unit):
        lines = kept.get(unit, [])
        cut = len(per_unit[unit]) - len(lines)
        head = f"--- {unit} ---" + (f"  ({cut} older lines cut)" if cut else "")
        blocks.append("\n".join([head, *lines]) if lines else f"{head}\n(cut entirely)")
    quiet = [u for u in units if u not in per_unit]
    if quiet:
        blocks.append("--- silent in the window ---\n" + ", ".join(sorted(quiet)))
    return "\n\n".join(blocks)


async def _previous_boot(ctx) -> str:
    """Errors from the boot before this one — restricted to the same units.

    A unit that restarted the box, or a boot that failed and was retried, leaves
    its evidence in the session that ended. The window everywhere else stops at
    the current boot on purpose, so without this that evidence is unreachable.
    """
    units = await probes.list_milo_units(JOURNAL_UNIT_GLOB, JOURNAL_EXTRA_UNITS)
    lines = await probes.journal_tail(
        units=units, lines=40, boot="-1", since=None, priority="err"
    )
    if not lines:
        return "(no error in the previous boot, or no previous boot on this disk)"
    capped = [render.cap_line(ctx.sanitize(line)) for line in lines]
    kept, cut = render.keep_newest(capped, render.PREVIOUS_BOOT_BUDGET_BYTES)
    if cut:
        kept.insert(0, f"({cut} older lines cut to fit the report)")
    return "\n".join(kept)
