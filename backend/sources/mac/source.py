# backend/sources/mac/source.py
"""
Mac audio source using ROC Streaming toolkit.

This source handles streaming audio from Mac computers via ROC Streaming.
It supports multiple simultaneous Mac connections and provides real-time
connection monitoring via journalctl logs.

Features:
- Multi-client support: Track multiple Macs by IP and hostname
- mDNS resolution: Resolve Mac hostnames via avahi-resolve
- Connection detection: Monitor journalctl for connect/disconnect events
- Active detection: Check recent journalctl logs for existing connections on start
"""
import asyncio
import contextlib
import ipaddress
from typing import Dict, Any, Optional

from pydantic import BaseModel

from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import SourceState
from backend.shared.decorators import handle_errors
from backend.shared.journalctl import follow_unit, read_unit
from backend.sources.mac.log_patterns import classify_line, normalize_ip


class MacSource(BaseAudioSource):
    """
    Mac audio source using ROC toolkit.

    Family A (mute receiver): playback control flows from the Mac sender;
    commands routed through `/api/audio/control/mac` reach `_handle_command`.
    Extends BaseAudioSource — implements `_do_start / _do_stop / _handle_command`.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        state_machine=None,
        settings_service=None,
        systemd_manager=None,
        camilladsp_service=None
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

        self.connected_clients: Dict[str, str] = {}  # {ip: hostname}
        self._monitor_task: Optional[asyncio.Task] = None

        # No per-source auto-stop: ROC is a passive PCM stream with no
        # pause signaling, and roc-receiver already handles client absence
        # on its own. The 12h INACTIVITY_TIMEOUT in AudioStateMachine
        # remains as the final backstop. `camilladsp_service` is kept on
        # the constructor for DI compatibility with the other Family A
        # sources.
        self.auto_stop_enabled = False
        _ = camilladsp_service  # reserved for future use

    def _reset_playback_state(self) -> None:
        super()._reset_playback_state()
        self.connected_clients.clear()

    async def _do_start(self) -> bool:
        """Start ROC service and monitoring."""
        try:
            if not await self._start_service_and_wait(settle=1):
                return False

            if not await self._is_service_active():
                self._logger.error("Service not active after start")
                return False

            await self._check_initial_state()

            self._monitor_task = asyncio.create_task(self._monitor_events())

            self._update_connection_state()

            return True

        except Exception as e:
            self._logger.error("Start failed: %s", e)
            return False

    async def _do_stop(self) -> bool:
        """Stop monitoring and service."""
        if self._monitor_task:
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task
            self._monitor_task = None

        self._reset_playback_state()

        return await self._stop_service()

    COMMANDS = {"get_connections": None}

    async def _handle_command(self, cmd: str, params: Optional[BaseModel]) -> Dict[str, Any]:
        """Handle Mac-specific commands."""
        if cmd == "get_connections":
            return self.success_response(
                connections=dict(self.connected_clients),
                connection_count=len(self.connected_clients)
            )

        return self.error_response(f"Unhandled command: {cmd}")

    # === Connection Monitoring ===

    @handle_errors(default=None)
    async def _check_initial_state(self) -> None:
        """Check for existing connections on startup."""
        # Check recent logs: last 100 non-trace lines out of the last 5000
        # (bounds startup replay + the avahi lookups a connect line triggers).
        for line in await read_unit(
            self.service_name, tail=5000, drop_substrings=("[trc]",),
            keep_last=100, logger=self._logger
        ):
            await self._process_log_line(line)

        # If no connections found, scan recent logs for active sessions
        if not self.connected_clients:
            await self._detect_active_connections()

    async def _detect_active_connections(self) -> None:
        """Detect existing connections via recent journalctl logs."""
        self._logger.debug("Detecting active connections via recent logs...")
        for line in await read_unit(
            self.service_name, since="10 min ago", drop_substrings=("[trc]",),
            timeout=2.0, logger=self._logger
        ):
            await self._process_log_line(line)

    async def _monitor_events(self) -> None:
        """Monitor journalctl for connection events."""
        try:
            async for line in follow_unit(self.service_name, logger=self._logger):
                # Per background-loop doctrine: a transient parse/state error on
                # one line must not kill the whole monitor.
                try:
                    await self._process_log_line(line)
                except Exception as e:
                    self._logger.error(f"Log line handling error: {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._logger.error(f"Monitoring error: {e}")

    @handle_errors(default=None)
    async def _process_log_line(self, line: str) -> None:
        """Process a log line for connection events."""
        event, ip, _ = classify_line(line)
        if not ip:
            return

        if event == "disconnect":
            if ip in self.connected_clients:
                name = self.connected_clients.pop(ip)
                self._logger.info(f"Disconnected: {name} ({ip})")
                self._update_connection_state()
        elif event == "connect":
            if ip not in self.connected_clients:
                await self._add_client(ip)

    async def _add_client(self, ip: str) -> None:
        """Add a client and resolve hostname."""
        if ip in self.connected_clients:
            return

        hostname = await self._resolve_hostname(ip)
        self.connected_clients[ip] = hostname
        self._logger.info(f"Connected: {hostname} ({ip})")
        self._update_connection_state()

    async def _resolve_hostname(self, ip: str) -> str:
        """Resolve the connected Mac's display name from its ROC source IP.

        ROC only hands us the sender's IP, so we reverse-resolve it — but the
        reverse (PTR) answer belongs to whoever owns that address's zone. A
        router that publishes its own DHCP domain (e.g. Freebox '.home') answers
        the reverse with a lowercased unicast name like 'mac-mini-de-leo.home'
        instead of the Mac's mDNS name, and the Mac no longer answers a reverse
        mDNS query for that IP at all. So we keep only the hostname label and
        recover the Mac's canonical, correctly-cased name via a *forward* mDNS
        lookup of '<label>.local' → 'Mac-mini-de-Leo', which the Mac answers
        regardless of query case. Falls back to the reverse label, then the IP.
        """
        if not ip:
            return "Mac"

        reverse = await self._avahi_reverse(ip)
        if not reverse:
            return ip

        label = reverse.split('.', 1)[0]
        canonical = await self._avahi_forward(f"{label}.local")
        return canonical.split('.', 1)[0] if canonical else label

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

    async def _avahi_forward(self, name: str) -> Optional[str]:
        """avahi-resolve -n <name> → canonical hostname (original case preserved).

        '.local' is mDNS-only, so this can only be answered by the Mac itself —
        never by the router's '.home' unicast zone.
        """
        out = await self._run_avahi(["avahi-resolve", "-n", name])
        if out:
            parts = out.split()
            if parts:
                return parts[0].rstrip('.')
        return None

    async def _run_avahi(self, args: list) -> Optional[str]:
        """Run an avahi-resolve query; return stripped stdout or None on failure."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self._logger.error("mDNS resolution skipped: avahi-resolve not installed")
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
        """Update state based on connected clients."""
        self.emit_connection_state(
            bool(self.connected_clients),
            extras={"client_names": list(self.connected_clients.values())},
        )
