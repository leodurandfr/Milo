# backend/features/mac/source.py
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
import re
import ipaddress
from typing import Dict, Any, Optional, Tuple

from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import PluginState
from backend.shared.decorators import handle_errors


# IPv4/IPv6 parsing regex for ROC log lines
_IP_PORT_RE = re.compile(
    r'(?:address|src_addr)=\[(?P<ip6>[0-9A-Fa-f:.%]+)\]:(?P<port>\d+)'
    r'|'
    r'(?:address|src_addr)=(?P<ip4>\d{1,3}(?:\.\d{1,3}){3}):(?P<port4>\d+)'
)


def _parse_ip_from_line(line: str) -> Tuple[Optional[str], Optional[int]]:
    """Extract (ip, port) from a ROC log line."""
    m = _IP_PORT_RE.search(line)
    if not m:
        return None, None
    if m.group('ip6'):
        return m.group('ip6'), int(m.group('port'))
    if m.group('ip4'):
        return m.group('ip4'), int(m.group('port4'))
    return None, None


def _normalize_ip(ip: Optional[str]) -> Optional[str]:
    """Clean brackets and preserve %scope for IPv6."""
    if not ip:
        return None
    return ip.strip('[]')


class MacSource(BaseAudioSource):
    """
    Mac audio source using ROC toolkit.

    Implements AudioSource Protocol with:
    - start(): Start ROC service and monitoring
    - stop(): Stop service and cleanup
    - restart(): Restart service
    - status(): Get current status with connected clients
    - command(): Handle restart command
    """

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

        # State
        self.connected_clients: Dict[str, str] = {}  # {ip: hostname}
        self._monitor_task: Optional[asyncio.Task] = None
        self._stopping = False

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

            self._stopping = False

            # Check for existing connections
            await self._check_initial_state()

            # Start continuous monitoring
            self._monitor_task = asyncio.create_task(self._monitor_events())

            # Update state based on connections
            self._update_connection_state()

            return True

        except Exception as e:
            self._logger.error("Start failed: %s", e)
            return False

    async def _do_stop(self) -> bool:
        """Stop monitoring and service."""
        self._stopping = True

        # Cancel monitoring task
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

        self._reset_playback_state()

        # Stop service
        return await self._stop_service()

    async def _do_restart(self) -> bool:
        """Restart with service restart."""
        self._stopping = True

        # Cancel monitoring
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

        # Restart service
        if not await self._restart_service_and_wait(settle=1):
            return False

        # Restart monitoring
        self._stopping = False
        self._reset_playback_state()
        await self._check_initial_state()
        self._monitor_task = asyncio.create_task(self._monitor_events())
        self._update_connection_state()

        return True

    async def _get_status(self) -> Dict[str, Any]:
        """Get Mac-specific status."""
        client_names = list(self.connected_clients.values())

        return {
            "listening": self._state != PluginState.ERROR,
            "rtp_port": self.rtp_port,
            "rs8m_port": self.rs8m_port,
            "rtcp_port": self.rtcp_port,
            "audio_output": self.audio_output,
            "connected": len(self.connected_clients) > 0,
            "client_names": client_names,
            "client_count": len(client_names)
        }

    async def _handle_command(self, cmd: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle Mac-specific commands."""
        if cmd == "restart_service":
            success = await self._do_restart()
            return self.success_response("Restarted") if success else self.error_response("Restart failed")

        if cmd == "get_connections":
            return self.success_response(
                connections=dict(self.connected_clients),
                connection_count=len(self.connected_clients)
            )

        return self.error_response(f"Unknown command: {cmd}")

    # === Connection Monitoring ===

    @handle_errors(default=None)
    async def _check_initial_state(self) -> None:
        """Check for existing connections on startup."""
        # Check recent logs (filter out trace logs)
        proc = await asyncio.create_subprocess_shell(
            f"journalctl -u {self.service_name} -n 5000 --no-pager | grep -v '\\[trc\\]' | tail -100",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), 10.0)
        except asyncio.TimeoutError:
            proc.kill()
            self._logger.error("Timeout reading journalctl logs for initial state")
            return

        if proc.returncode == 0:
            for line in stdout.decode().split('\n'):
                if line.strip():
                    await self._process_log_line(line)

        # If no connections found, scan recent logs for active sessions
        if not self.connected_clients:
            await self._detect_active_connections()

    async def _detect_active_connections(self) -> None:
        """Detect existing connections via recent journalctl logs."""
        try:
            self._logger.debug("Detecting active connections via recent logs...")

            proc = await asyncio.create_subprocess_shell(
                f"journalctl -u {self.service_name} --since '10 min ago' --no-pager"
                " | grep -v '\\[trc\\]'",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )

            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return

            for line in stdout.decode('utf-8', errors='ignore').split('\n'):
                if line.strip():
                    await self._process_log_line(line)

        except Exception as e:
            self._logger.debug(f"Active connection detection failed: {e}")

    async def _monitor_events(self) -> None:
        """Monitor journalctl for connection events."""
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "journalctl", "-f", "-u", self.service_name, "-o", "short",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            self._logger.info("Event monitoring started")

            while not self._stopping and proc.returncode is None:
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=1.0)
                    if line:
                        await self._process_log_line(line.decode('utf-8').strip())
                except asyncio.TimeoutError:
                    continue

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._logger.error(f"Monitoring error: {e}")
        finally:
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                    await proc.wait()
                except ProcessLookupError:
                    pass

    @handle_errors(default=None)
    async def _process_log_line(self, line: str) -> None:
        """Process a log line for connection events."""
        # Disconnection
        if "removing route" in line or "removing address" in line:
            ip, _ = _parse_ip_from_line(line)
            if ip:
                ip = _normalize_ip(ip)
                if ip in self.connected_clients:
                    name = self.connected_clients.pop(ip)
                    self._logger.info(f"Disconnected: {name} ({ip})")
                    self._update_connection_state()
            return

        # Connection
        if "session group: creating session" in line:
            ip, _ = _parse_ip_from_line(line)
            if ip:
                ip = _normalize_ip(ip)
                if ip not in self.connected_clients:
                    await self._add_client(ip)
            return

        # Connection via route
        if "creating" in line and "route" in line and "address=" in line:
            ip, _ = _parse_ip_from_line(line)
            if ip:
                ip = _normalize_ip(ip)
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
        """Resolve mDNS hostname for IP."""
        if not ip:
            return "Mac"

        try:
            ip_norm = _normalize_ip(ip)
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

            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), 5.0)
            except asyncio.TimeoutError:
                proc.kill()
                self._logger.debug(f"Timeout resolving mDNS for {ip}")
                return None

            if proc.returncode == 0:
                parts = stdout.decode().strip().split()
                if len(parts) >= 2:
                    return parts[1].rstrip('.').replace(".local", "")

        except Exception as e:
            self._logger.debug(f"mDNS resolution failed for {ip}: {e}")

        return ip

    def _update_connection_state(self) -> None:
        """Update state based on connected clients."""
        base = {"listening": True, "rtp_port": self.rtp_port, "audio_output": self.audio_output}
        self._set_connected_or_ready(
            bool(self.connected_clients),
            {**base, "connected": True,
             "client_names": list(self.connected_clients.values()),
             "client_count": len(self.connected_clients)},
            {**base, "connected": False, "client_names": [], "client_count": 0}
        )
