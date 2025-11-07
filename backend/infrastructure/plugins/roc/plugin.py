# backend/infrastructure/plugins/roc/plugin.py
"""
ROC plugin for Milo - Multi-client support with IPv4/IPv6 and mDNS
Handles multiple Macs connected simultaneously by tracking their IPs and names via ROC logs.
"""
import asyncio
import re
import ipaddress
from typing import Dict, Any, Tuple, Optional

from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.plugin_utils import format_response


# ------------ IP/Port Helpers (IPv4 + IPv6 + scope) -----------------

# Supported examples:
#   address=1.2.3.4:10003
#   address=[2001:db8::1]:10003
#   address=[fe80::a%wlan0]:10003
#   src_addr=...
_IP_PORT_RE = re.compile(
    r'(?:address|src_addr)=\[(?P<ip6>[0-9A-Fa-f:.%]+)\]:(?P<port>\d+)'
    r'|'
    r'(?:address|src_addr)=(?P<ip4>\d{1,3}(?:\.\d{1,3}){3}):(?P<port4>\d+)'
)

def _parse_ip_from_line(line: str) -> Tuple[Optional[str], Optional[int]]:
    """
    Extracts (ip, port) from a ROC log line if present.
    IPv6 may include a %scope (e.g. fe80::1%wlan0).
    """
    m = _IP_PORT_RE.search(line)
    if not m:
        return None, None
    if m.group('ip6'):
        return m.group('ip6'), int(m.group('port'))
    if m.group('ip4'):
        return m.group('ip4'), int(m.group('port4'))
    return None, None

def _normalize_ip_for_storage(ip: Optional[str]) -> Optional[str]:
    """Cleans brackets if present and preserves %scope."""
    if not ip:
        return None
    return ip.strip('[]')


class RocPlugin(UnifiedAudioPlugin):
    """
    ROC plugin with simultaneous multi-client support.

    Features:
    - Tracking multiple connected Macs via dict {ip: display_name}
    - Log monitoring (journalctl) for connection/disconnection detection with IPs
    - mDNS resolution (avahi-resolve) to get Mac names
    - IPv4/IPv6 support (including link-local with %scope)
    """

    def __init__(self, config: Dict[str, Any], state_machine=None):
        super().__init__("roc", state_machine)
        self.config = config
        self.service_name = config.get("service_name", "milo-roc.service")

        # ROC parameters
        self.rtp_port = config.get("rtp_port", 10001)
        self.rs8m_port = config.get("rs8m_port", 10002)
        self.rtcp_port = config.get("rtcp_port", 10003)
        self.audio_output = config.get("audio_output", "hw:1,0")

        # Useful option for IPv6 link-local (fe80::/64)
        # Example: "wlan0" or "eth0" - helps mDNS resolution if no %scope
        self.network_interface = config.get("network_interface")

        # State - Tracking multiple connected clients
        self.connected_clients: Dict[str, str] = {}  # {ip: display_name}
        self.monitor_task: Optional[asyncio.Task] = None
        self._stopping = False
        self._current_device = "milo_roc"

    async def _do_initialize(self) -> bool:
        """ROC plugin initialization"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "list-unit-files", self.service_name,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()

            if proc.returncode != 0 or self.service_name not in stdout.decode():
                raise RuntimeError(f"Service {self.service_name} not found")

            self.logger.info("Plugin ROC initialisé")
            return True

        except Exception as e:
            self.logger.error(f"ROC initialization error: {e}")
            return False

    async def _do_start(self) -> bool:
        """Starting ROC service"""
        try:
            success = await self.control_service(self.service_name, "start")

            if success:
                await asyncio.sleep(1)
                is_active = await self.service_manager.is_active(self.service_name)

                if is_active:
                    # Démarrer la surveillance événementielle (journalctl)
                    self._stopping = False
                    self.monitor_task = asyncio.create_task(self._monitor_events())

                    await self.notify_state_change(PluginState.READY, {
                        "listening": True,
                        "rtp_port": self.rtp_port,
                        "audio_output": self.audio_output
                    })
                    return True

            return False

        except Exception as e:
            self.logger.error(f"ROC start error: {e}")
            return False

    async def stop(self) -> bool:
        """Simple ROC plugin stop"""
        self.logger.info("Simple ROC plugin stop")
        self._stopping = True

        # Cleanup des tâches
        if self.monitor_task:
            self.monitor_task.cancel()
            self.monitor_task = None

        # Stop the service
        success = await self.control_service(self.service_name, "stop")

        # Reset state
        self.connected_clients.clear()
        await self.notify_state_change(PluginState.INACTIVE)

        self.logger.info(f"ROC stop completed: {success}")
        return success

    async def _detect_active_connections(self):
        """
        Detects active connections via tcpdump si Mac déjà connecté avant démarrage ROC.
        Capture brièvement les paquets sur les ports ROC pour extraire les IPs sources.
        """
        try:
            self.logger.info("Launching tcpdump to detect active connections...")

            # Lancer tcpdump : capture max 15 paquets ou timeout 3s
            proc = await asyncio.create_subprocess_exec(
                "sudo", "tcpdump",
                "-i", "any",           # Toutes les interfaces
                "-n",                  # Pas de résolution DNS
                "-l",                  # Line buffered
                "-c", "15",            # Max 15 paquets
                f"udp and dst port ({self.rtp_port} or {self.rs8m_port} or {self.rtcp_port})",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )

            # Timeout de 3 secondes max
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return

            output = stdout.decode('utf-8', errors='ignore')

            # Parser les IPs sources (IPv4 et IPv6)
            # Format: "21:47:59.614123 IP 192.168.1.172.54421 > ..."
            # Format IPv6: "21:47:59.614123 IP6 fe80::1.54421 > ..."
            ip_pattern = re.compile(r'IP6?\s+([0-9a-fA-F:.]+)\.\d+\s+>')

            detected_ips = set()
            for line in output.split('\n'):
                match = ip_pattern.search(line)
                if match:
                    ip = match.group(1)
                    detected_ips.add(ip)

            if detected_ips:
                self.logger.info(f"Active connections detected via tcpdump: {detected_ips}")
                for ip in detected_ips:
                    await self._add_client(ip)
            else:
                self.logger.info("No active connections detected via tcpdump")

        except Exception as e:
            self.logger.warning(f"tcpdump detection error (non-bloquant): {e}")

    async def _monitor_events(self):
        """Pure event monitoring with journalctl -f"""
        proc = None
        try:
            # Read initial state
            await self._check_initial_state()

            # Real-time monitoring
            proc = await asyncio.create_subprocess_exec(
                "journalctl", "-f", "-u", self.service_name, "-o", "short",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            self.logger.info("ROC event monitoring active")

            while not self._stopping and proc.returncode is None:
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=1.0)
                    if line:
                        log_line = line.decode('utf-8').strip()
                        await self._process_log_line(log_line)
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    self.logger.error(f"Log processing error: {e}")
                    break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Monitoring error: {e}")
        finally:
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                    await proc.wait()
                except ProcessLookupError:
                    pass  # Process already terminated

    async def _check_initial_state(self):
        """Checks initial state en analysant les derniers logs"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "journalctl", "-u", self.service_name, "-n", "100", "--no-pager",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()

            if proc.returncode == 0:
                lines = stdout.decode().split('\n')
                # Traiter tous les logs dans l'ordre chronologique
                for line in lines:
                    if line.strip():
                        await self._process_log_line(line)

            # Si aucune connexion trouvée dans les logs, utiliser tcpdump
            # pour détecter les Mac déjà connectés (cas où Mac connecté avant démarrage ROC)
            if not self.connected_clients:
                self.logger.info("No connection in logs, recherche active avec tcpdump...")
                await self._detect_active_connections()

        except Exception as e:
            self.logger.error(f"Initial state error: {e}")

    async def _process_log_line(self, line: str):
        """Processes a log line - Détection connexion ET déconnexion avec IP"""
        try:
            # DÉCONNEXION - Extraire l'IP depuis les logs de déconnexion
            # Pattern: "session router: removing route: ... address=192.168.1.172:54421"
            # Pattern: "rtcp reporter: removing address: remote_addr=192.168.1.172:54421"
            if "removing route" in line or "removing address" in line:
                ip, _ = _parse_ip_from_line(line)
                if ip:
                    ip = _normalize_ip_for_storage(ip)
                    if ip in self.connected_clients:
                        client_name = self.connected_clients[ip]
                        self.logger.info(f"DISCONNECTION detected: {client_name} ({ip})")
                        del self.connected_clients[ip]
                        await self._update_state()
                else:
                    # Si on ne trouve pas l'IP, on log un warning
                    self.logger.warning(f"Disconnection detected without IP: {line[:100]}")
                return

            # CONNEXION - Pattern: "session group: creating session"
            if "session group: creating session" in line:
                # Chercher l'IP dans cette ligne ou les suivantes
                ip, _ = _parse_ip_from_line(line)
                if ip:
                    ip = _normalize_ip_for_storage(ip)
                    if ip not in self.connected_clients:
                        self.logger.info(f"CONNECTION detected: {ip}")
                        await self._add_client(ip)
                return

            # CONNEXION via route - Pattern: "creating.*route.*address="
            if "creating" in line and "route" in line and "address=" in line:
                ip, _ = _parse_ip_from_line(line)
                if ip:
                    ip = _normalize_ip_for_storage(ip)
                    if ip not in self.connected_clients:
                        self.logger.info(f"CONNECTION detected (route): {ip}")
                        await self._add_client(ip)
                return

        except Exception as e:
            self.logger.error(f"Process line error: {e}")

    async def _add_client(self, ip: str):
        """Adds a new client to tracking and resolves its name"""
        if ip in self.connected_clients:
            return

        # Résoudre le hostname
        display_name = await self._resolve_hostname(ip)
        self.connected_clients[ip] = display_name
        self.logger.info(f"Client added: {display_name} ({ip})")

        # Update state
        await self._update_state()

    async def _resolve_hostname(self, ip: str) -> str:
        """
        Resolves mDNS hostname for IPv4 or IPv6.
        - Preserves potential %scope on IPv6 (essential for fe80::/64).
        - Adds %scope if IP is fe80::/64 and self.network_interface is defined.
        - Forces -6 for IPv6.
        """
        if not ip:
            return "Mac connecté"

        try:
            ip_norm = _normalize_ip_for_storage(ip)
            scope = None

            # Separate potential scope sur IPv6
            if '%' in ip_norm:
                ip_only, scope = ip_norm.split('%', 1)
            else:
                ip_only = ip_norm

            addr = ipaddress.ip_address(ip_only)

            # Add scope if link-local IPv6 et qu'on a une interface connue
            if addr.version == 6 and addr.is_link_local and scope is None and self.network_interface:
                ip_norm = f"{ip_only}%{self.network_interface}"

            # Build command avahi-resolve
            args = ["avahi-resolve", "-a", ip_norm]
            if addr.version == 6:
                args.insert(1, "-6")

            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            stdout, _ = await proc.communicate()

            if proc.returncode == 0:
                output = stdout.decode().strip()
                # Format: "<ip>\t<hostname>"
                parts = output.split()
                if len(parts) >= 2:
                    host = parts[1].rstrip('.')
                    return host.replace(".local", "")

        except Exception as e:
            self.logger.debug(f"mDNS resolve failed for {ip}: {e}")

        return ip

    async def _update_state(self):
        """Updates state with connected clients list"""
        if self.connected_clients:
            # List of client names
            client_names = list(self.connected_clients.values())

            await self.notify_state_change(PluginState.CONNECTED, {
                "listening": True,
                "rtp_port": self.rtp_port,
                "audio_output": self.audio_output,
                "connected": True,
                "client_names": client_names,  # Liste des noms
                "client_count": len(client_names)
            })
        else:
            await self.notify_state_change(PluginState.READY, {
                "listening": True,
                "rtp_port": self.rtp_port,
                "audio_output": self.audio_output,
                "connected": False,
                "client_names": [],
                "client_count": 0
            })

    async def get_status(self) -> Dict[str, Any]:
        """Current state with connected clients list"""
        try:
            service_status = await self.service_manager.get_status(self.service_name)
            client_names = list(self.connected_clients.values())

            return {
                "service_active": service_status.get("active", False),
                "listening": service_status.get("active", False),
                "rtp_port": self.rtp_port,
                "audio_output": self.audio_output,
                "connected": len(self.connected_clients) > 0,
                "client_names": client_names,
                "client_count": len(client_names),
                "current_device": self._current_device
            }
        except Exception as e:
            return {"error": str(e), "current_device": self._current_device}

    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Simple commands"""
        try:
            if command == "restart":
                success = await self.control_service(self.service_name, "restart")
                if success:
                    # Restart monitoring
                    if self.monitor_task:
                        self.monitor_task.cancel()
                        try:
                            await self.monitor_task
                        except asyncio.CancelledError:
                            pass

                    await asyncio.sleep(1)
                    self.monitor_task = asyncio.create_task(self._monitor_events())

                return format_response(success, "Restarted" if success else "Failed")

            return format_response(False, error=f"Unknown command: {command}")

        except Exception as e:
            return format_response(False, error=str(e))

    async def get_initial_state(self) -> Dict[str, Any]:
        """Initial state"""
        status = await self.get_status()
        return {**status, "plugin_state": self.current_state.value}