# backend/sources/mac/source.py
"""
Mac audio source using ROC Streaming toolkit.

roc-recv receives what Macs send (roc-vad: the Mac picks Milō as its sound
output) and says nothing about it anywhere but in its journal — no API, no
D-Bus. So the journal is the feed, and what was measured on the unit
(2026-09-24, roc-recv 0.4.0, the owner's Mac) decides how it is read:

  - A Mac arriving is `session group: creating session: src_addr=<ip>:<port>`;
    one leaving (another output picked, the Mac asleep or off the network) is
    roc-recv's watchdog ending the session ~0.3 s after the last packet,
    `removing route: … address=<ip>:<port>` (log_patterns.py).
  - roc-recv stopped or killed writes nothing about its sessions: they end
    with the process. Its journal therefore describes the Macs attached to it
    *since its process started*, and nothing older — a replay across an
    earlier run is how a Mac that had left came back as "Audio reçu de …"
    forever (E28). The feed is one `journalctl -f` bounded by that start and
    following on from it, across the restarts `Restart=always` makes (a Mac
    still streaming reattaches to the new process by itself, measured). The
    time bound is journald's *reception* time, so each line also carries the
    pid of the roc-recv that wrote it: a line from a process that no longer
    runs is ignored, and a session is watched against the process that
    announced its Macs.
  - roc-vad streams silence as well as music, unbroken, for as long as the
    Mac has Milō as its output: nothing tells playing from paused, so the
    session is CONNECTED from start to end and no idle timeout applies.

The session is the set of Macs attached (several at once), opened when the
first one is named and ended when the last one leaves or roc-recv exits.
Naming a Mac (mdns.py) asks the Mac itself and gives up after a second when
nothing answers — too long for the mailbox to wait, so it runs beside it and
posts its answer.
"""
import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Set, Tuple

from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import NetworkRequirement
from backend.core.models.session import (
    DaemonSnapshot, EndReason, IdlePolicy, Phase, ResumePolicy, ReroutePolicy, Session,
)
from backend.shared.background import BackgroundTaskSet
from backend.shared.journalctl import follow_unit
from backend.sources.mac.log_patterns import classify_line
from backend.sources.mac.mdns import resolve_sender_name


@dataclass(eq=False)
class MacSession(Session):
    """The Macs streaming to roc-recv: (ip, port) of each stream -> the name
    the card shows. A Mac on two streams (a new one opened before the old one
    ended) is one name on screen."""
    senders: Dict[Tuple[str, Optional[int]], str] = field(default_factory=dict)


class MacSource(BaseAudioSource):
    """
    Mac audio source using ROC toolkit.

    Family A (mute receiver): playback control flows from the Mac sender, so
    this source registers no command at all.
    Extends BaseAudioSource — implements `_do_start / _do_stop`.
    """

    NETWORK_REQUIREMENT = NetworkRequirement.LAN

    # No pause is visible (see the module docstring): the global auto-stop
    # delay does not apply, and a CONNECTED session has no idle timeout.
    AUTO_STOP_SUPPORTED = False
    IDLE_POLICY = IdlePolicy.NONE
    # roc-recv writes to the output it was started on: a multiroom toggle
    # restarts it, and each Mac still streaming reattaches by itself.
    REROUTE = ReroutePolicy.END_SESSION
    # Milō cannot start a Mac's stream: nothing is kept.
    RESUME_POLICY = ResumePolicy(capture_on=frozenset(), forget_on=frozenset(EndReason))
    SESSION_DAEMON = True

    # The one family-A source: ROC hands over an IP and nothing else, so there
    # is no transport and no media field to project — only the senders' names.
    COMMANDS = {}
    COMMAND_SCOPES = {}

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        state_machine=None,
        settings_service=None,
        systemd_manager=None
    ):
        super().__init__(
            source_id="mac",
            service_name="milo-mac.service",
            state_machine=state_machine,
            systemd_manager=systemd_manager,
            settings_service=settings_service,
            config=config
        )

        self.network_interface = self._config.get("network_interface")

        self._journal_task: Optional[asyncio.Task] = None
        # Streams heard arriving whose Mac is being named; one that ends
        # meanwhile is dropped from it, and its answer with it.
        self._naming: Set[Tuple[str, Optional[int]]] = set()
        # The roc-recv whose lines count (see _from_running_roc_recv).
        self._roc_pid: Optional[int] = None
        self._namers = BackgroundTaskSet(self._logger, "source.mac.naming")

    async def _do_start(self) -> bool:
        """Start roc-recv and follow its journal from its process's start."""
        try:
            if not await self._start_service_and_wait(settle=1):
                return False

            if not await self._is_service_active():
                self._logger.error("Service not active after start")
                return False

            started_usec = await self._service_main_start_usec()
            self._publish()
            self._journal_task = asyncio.create_task(self._follow_journal(started_usec))
            return True

        except Exception as e:
            self._logger.error("Start failed: %s", e)
            return False

    async def _do_stop(self) -> bool:
        await self.end_session(EndReason.SOURCE_SWITCH)
        await self._cleanup()
        return await self._stop_service()

    async def _do_release(self) -> bool:
        await self.end_session(EndReason.REROUTE)
        await self._cleanup()
        return await self._stop_service()

    async def shutdown(self) -> None:
        # The mailbox first: nothing may run in it while the follow is torn down.
        await super().shutdown()
        await self._stop_following()
        await self._namers.cancel_all()

    async def _cleanup(self) -> None:
        """Stop following the journal and forget what it said (unit stop is the caller's)."""
        await self._stop_following()
        await self._namers.cancel_all()

    async def _stop_following(self) -> None:
        if self._journal_task:
            self._journal_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._journal_task
            self._journal_task = None
        self._naming.clear()
        self._roc_pid = None
        # What the follow posted and nobody handled belongs to this roc-recv run.
        self._discard_feed()

    async def _service_main_start_usec(self) -> Optional[int]:
        """When roc-recv's process started (µs), or None when systemd cannot say."""
        if self._service_manager is None:
            return None
        try:
            return await self._service_manager.main_start_usec(self.service_name)
        except Exception as e:
            self._logger.warning(f"Could not read when {self.service_name} started: {e}")
            return None

    async def _service_main_pid(self) -> Optional[int]:
        """The roc-recv a session is watched against: the one whose journal
        announced its Macs, named by its own lines — never left unwatched
        because systemd was slow to answer when the session opened."""
        return self._roc_pid

    # === The journal ===

    async def _follow_journal(self, started_usec: Optional[int]) -> None:
        """Post every arrival and departure roc-recv logs, with the pid of the
        process that logged it, from its process's start on. Without that
        start (systemd unreadable) the follow begins now, and a Mac that
        attached before it is not seen until it reattaches."""
        if started_usec is None:
            self._logger.warning(
                "No start time for %s: following its journal from now", self.service_name
            )
            bound = {}
        else:
            bound = {"tail": "all", "since": f"@{started_usec / 1_000_000:.6f}"}
        try:
            async for entry in follow_unit(
                self.service_name,
                consequence="Mac connection detection is down",
                output="json",
                logger=self._logger,
                **bound,
            ):
                # Per background-loop doctrine: one line must not kill the follow.
                try:
                    fields = json.loads(entry)
                    message, pid = fields.get("MESSAGE"), fields.get("_PID")
                    if not isinstance(message, str) or not pid:
                        continue
                    event, ip, port = classify_line(message)
                    if event and ip:
                        self._post_feed((event, (ip, port), int(pid)))
                except Exception as e:
                    self._logger.error(f"Log line handling error: {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._logger.error(f"Monitoring error: {e}")

    async def _handle_feed(self, events) -> None:
        """Arrivals and departures, in the order roc-recv logged them, then one publish."""
        if self._journal_task is None:
            return
        for event, address, pid in events:
            if not await self._from_running_roc_recv(pid):
                continue
            if event == "connect":
                self._sender_arrived(address)
            else:
                await self._sender_left(address)
        self._publish_changes()

    async def _from_running_roc_recv(self, pid: int) -> bool:
        """Whether a line comes from the roc-recv that holds sessions now.

        journald stamps a stdout line when it receives it, so a line the
        previous process wrote can arrive after the next one started: the pid,
        not the time, says whose it is. A new pid that systemd names as the
        main process is a roc-recv that replaced the previous one. Systemd
        unreadable: the line's own process is taken — the watch on its pid
        ends any session it no longer holds.
        """
        if pid == self._roc_pid:
            return True
        running = await super()._service_main_pid()
        if running is not None and running != pid:
            self._logger.debug(f"A line from roc-recv {pid}, which no longer runs — ignored")
            return False
        previous, self._roc_pid = self._roc_pid, pid
        if previous is not None:
            # What the previous process announced went with it.
            self._naming.clear()
            if self._session is not None:
                await self._daemon_gone(self._session)
        return True

    def _sender_arrived(self, address: Tuple[str, Optional[int]]) -> None:
        session = self._session
        if isinstance(session, MacSession):
            if address in session.senders:
                return
            # The same Mac on a new stream (measured: a new port per stream).
            known = next((n for (ip, _), n in session.senders.items() if ip == address[0]), None)
            if known is not None:
                session.senders[address] = known
                return
        if address in self._naming:
            return
        self._naming.add(address)
        self._namers.spawn(self._name_sender(address), label="name")

    async def _name_sender(self, address: Tuple[str, Optional[int]]) -> None:
        """Beside the mailbox: the lookup can take a second, and a departure
        or roc-recv's death must not wait behind it."""
        ip = address[0]
        try:
            name = await resolve_sender_name(ip, self.network_interface)
        except Exception as e:
            self._logger.warning(f"Could not name {ip}: {e}")
            name = ip
        self._post_result(lambda: self._sender_named(address, name))

    async def _sender_named(self, address: Tuple[str, Optional[int]], name: str) -> None:
        if address not in self._naming:
            return  # left, or roc-recv went, while it was being named
        self._naming.discard(address)
        session = await self.reconcile(DaemonSnapshot(None, Phase.CONNECTED))
        if not isinstance(session, MacSession):
            return
        session.senders[address] = name
        self._logger.info(f"Connected: {name} ({address[0]})")
        # The state first, then the banner's end: a cleared event carrying the
        # state it clears would say READY under a Mac already back.
        self._publish_changes()
        # A Mac back on the restarted roc-recv: the death it followed is over.
        self.broadcast_error_cleared()

    async def _sender_left(self, address: Tuple[str, Optional[int]]) -> None:
        self._naming.discard(address)
        session = self._session
        if not isinstance(session, MacSession) or address not in session.senders:
            return
        name = session.senders.pop(address)
        self._logger.info(f"Disconnected: {name} ({address[0]})")
        if not session.senders:
            await self.reconcile(None)

    def _daemon_session(self, snapshot: DaemonSnapshot) -> Session:
        return MacSession(phase=snapshot.phase, sender=snapshot.sender)

    async def _session_ended(self, session: Session, reason: EndReason) -> None:
        """Macs still being named belonged to the roc-recv that ended with the
        session — unless the last named one simply left."""
        if reason is not EndReason.SENDER_LEFT:
            self._naming.clear()

    def _session_fields(self, session: MacSession) -> Dict[str, Any]:
        """The Macs attached, by name — one entry per Mac, however many
        streams it has opened (D3)."""
        return {"senders": list(dict.fromkeys(session.senders.values()))}
