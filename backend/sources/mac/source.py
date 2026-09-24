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
Naming a Mac (mdns.py) can take seconds — 6.8 s for the owner's — so it runs
beside the mailbox and posts its answer.
"""
import asyncio
import contextlib
import ipaddress
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
from backend.sources.mac.log_patterns import classify_line, normalize_ip
from backend.sources.mac.mdns import is_private_hostname, service_name_for_addresses


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
    # is no transport and no media field to project — only the sender's name,
    # which rides in `extras`. Everything else publishes the inert
    # {is_playing, is_buffering} pair even when stopped.
    MUTE_RECEIVER = True

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

        self.rtp_port = self._config.get("rtp_port", 10001)
        self.rs8m_port = self._config.get("rs8m_port", 10002)
        self.rtcp_port = self._config.get("rtcp_port", 10003)
        self.audio_output = self._config.get("audio_output", "hw:1,0")
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
            self._update_connection_state()
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
        """Beside the mailbox: the lookup can take seconds, and a departure or
        roc-recv's death must not wait behind it."""
        ip = address[0]
        try:
            name = await self._resolve_hostname(ip)
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

    async def _resolve_hostname(self, ip: str) -> str:
        """Resolve the connected Mac's display name from its ROC source IP.

        The Bonjour service instance name is what the user recognises — "Mac
        mini de Léo" — so it is what we ask for, matched by advertised address.
        A hostname is only the fallback, and a private one is worth less than
        the IP: see mdns.py for why neither the reverse nor the forward lookup
        can be trusted to answer with a name meant to be read.

        Two addresses are offered to the match because they can differ: a Mac
        streams ROC from one interface while advertising Bonjour on another (a
        private Wi-Fi address next to the wired one), and the forward lookup is
        what bridges the two.
        """
        if not ip:
            return "Mac"

        reverse = await self._avahi_reverse(ip)
        label = reverse.split('.', 1)[0] if reverse else None

        advertised_ip = None
        if label:
            canonical, advertised_ip = await self._avahi_forward(f"{label}.local")
            if canonical:
                label = canonical.split('.', 1)[0]

        service_name = await self._bonjour_name((ip, advertised_ip))
        if service_name:
            return service_name

        return label if label and not is_private_hostname(label) else ip

    async def _bonjour_name(self, addresses: Tuple[Optional[str], ...]) -> Optional[str]:
        """Look up the Bonjour instance name advertised at any of `addresses`.

        `-t` stops at the end of the cache dump (~1 s on a home LAN) instead of
        browsing forever; a Mac that has not been seen yet simply yields no
        match, and the caller falls back to a hostname.
        """
        out = await self._run_avahi(["avahi-browse", "-a", "-r", "-p", "-t"])
        return service_name_for_addresses(out, addresses) if out else None

    async def _avahi_reverse(self, ip: str) -> Optional[str]:
        """avahi-resolve -a <ip> → hostname (mDNS '.local' or a router '.home')."""
        try:
            ip_norm = normalize_ip(ip)
            scope = None

            if '%' in ip_norm:
                ip_only, scope = ip_norm.split('%', 1)
            else:
                ip_only = ip_norm

            addr = ipaddress.ip_address(ip_only)

            # Add scope for link-local IPv6
            if addr.version == 6 and addr.is_link_local and scope is None and self.network_interface:
                ip_norm = f"{ip_only}%{self.network_interface}"

            args = ["avahi-resolve", "-a", ip_norm]
            if addr.version == 6:
                args.insert(1, "-6")
        except Exception as e:
            self._logger.debug(f"Bad IP for mDNS reverse {ip}: {e}")
            return None

        out = await self._run_avahi(args)
        if out:
            parts = out.split()
            if len(parts) >= 2:
                return parts[1].rstrip('.')
        return None

    async def _avahi_forward(self, name: str) -> Tuple[Optional[str], Optional[str]]:
        """avahi-resolve -n <name> → (canonical hostname, address it resolves to).

        '.local' is mDNS-only, so this can only be answered by the Mac itself —
        never by the router's '.home' unicast zone — which is what makes the
        address it answers with the Mac's own, whichever interface it came from.
        """
        out = await self._run_avahi(["avahi-resolve", "-n", name])
        if out:
            parts = out.split()
            if len(parts) >= 2:
                return parts[0].rstrip('.'), parts[1]
            if parts:
                return parts[0].rstrip('.'), None
        return None, None

    async def _run_avahi(self, args: list) -> Optional[str]:
        """Run an avahi query; return stripped stdout or None on failure."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self._logger.error("mDNS resolution skipped: %s not installed", args[0])
            return None
        except OSError as e:
            # Spawn can fail transiently (EMFILE/ENOMEM/…) — fall back so the
            # caller still registers the client under its bare IP.
            self._logger.warning("avahi-resolve spawn failed: %s", e)
            return None

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), 5.0)
        except asyncio.TimeoutError:
            proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()  # reap the killed child so its transport closes
            self._logger.debug("Timeout running %s", " ".join(args))
            return None

        return stdout.decode().strip() if proc.returncode == 0 else None

    def _update_connection_state(self) -> None:
        """The one publish site: the Macs attached, by name."""
        self.emit_connection_state(*self._connection_state())

    def _connection_state(self):
        session = self._session
        names = (
            list(dict.fromkeys(session.senders.values())) if isinstance(session, MacSession) else []
        )
        return session is not None, None, {"client_names": names}
