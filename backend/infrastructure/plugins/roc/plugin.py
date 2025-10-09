# backend/infrastructure/plugins/roc/plugin.py
"""
Plugin ROC pour Milo - Version avec support IPv4/IPv6, mDNS robuste et sniffer RTCP
"""
import asyncio
import re
import socket
import ipaddress
from typing import Dict, Any, Tuple, Optional

from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.plugin_utils import format_response


# ------------ Helpers IP/Port (IPv4 + IPv6 + scope) -----------------

# Exemples gérés :
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
    Extrait (ip, port) depuis une ligne de log ROC si présent.
    IPv6 peut inclure un %scope (ex: fe80::1%wlan0).
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
    """Nettoie les crochets éventuels et conserve le %scope si présent."""
    if not ip:
        return None
    return ip.strip('[]')


# ------------ Sniffer RTCP (optionnel, partage le port avec ROC) -----------------

class _RtcpSniffer(asyncio.DatagramProtocol):
    """
    Petit sniffer RTCP pour obtenir l'IP/port du pair, IPv4/IPv6.
    Utilisé en parallèle (SO_REUSEPORT) sur le port RTCP du receiver.
    """
    def __init__(self, on_peer, logger):
        self.on_peer = on_peer
        self.logger = logger

    def datagram_received(self, data, addr):
        # addr:
        #   IPv4 -> (ip, port)
        #   IPv6 -> (ip, port, flowinfo, scopeid)
        try:
            if len(addr) == 2:
                ip, port = addr
                scope = None
            else:
                ip, port, _flow, scopeid = addr
                try:
                    scope = socket.if_indextoname(scopeid)
                except Exception:
                    scope = None

            # Ajouter %scope pour IPv6 link-local si kernel nous donne scopeid
            if scope and '%' not in ip and ':' in ip:
                ip = f"{ip}%{scope}"

            self.on_peer(_normalize_ip_for_storage(ip), port)
        except Exception as e:
            self.logger.debug(f"rtcp sniffer parse error: {e}")


class RocPlugin(UnifiedAudioPlugin):
    """Plugin ROC avec surveillance log + sniffer RTCP + résolution mDNS IPv4/IPv6"""

    def __init__(self, config: Dict[str, Any], state_machine=None):
        super().__init__("roc", state_machine)
        self.config = config
        self.service_name = config.get("service_name", "milo-roc.service")

        # Paramètres ROC
        self.rtp_port = config.get("rtp_port", 10001)
        self.rs8m_port = config.get("rs8m_port", 10002)
        self.rtcp_port = config.get("rtcp_port", 10003)
        self.audio_output = config.get("audio_output", "hw:1,0")

        # Option utile pour IPv6 link-local (fe80::/64)
        # Exemple: "wlan0" ou "eth0" — aide la résolution mDNS si pas de %scope
        self.network_interface = config.get("network_interface")

        # État
        self.has_connections = False
        self.connected_ip: Optional[str] = None
        self.monitor_task: Optional[asyncio.Task] = None
        self._stopping = False
        self._current_device = "milo_roc"

        # Sniffer RTCP (transport/protocol)
        self._rtcp_sniffer = None

    async def _do_initialize(self) -> bool:
        """Initialisation du plugin ROC"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "list-unit-files", self.service_name,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()

            if proc.returncode != 0 or self.service_name not in stdout.decode():
                raise RuntimeError(f"Service {self.service_name} non trouvé")

            self.logger.info("Plugin ROC initialisé")
            return True

        except Exception as e:
            self.logger.error(f"Erreur initialisation ROC: {e}")
            return False

    async def _do_start(self) -> bool:
        """Démarrage du service ROC"""
        try:
            success = await self.control_service(self.service_name, "start")

            if success:
                await asyncio.sleep(1)
                is_active = await self.service_manager.is_active(self.service_name)

                if is_active:
                    # Démarrer la surveillance événementielle (journalctl)
                    self._stopping = False
                    self.monitor_task = asyncio.create_task(self._monitor_events())

                    # Démarrer le sniffer RTCP (fiabilise l'IP/IPv6)
                    try:
                        self._rtcp_sniffer = await self._start_rtcp_sniffer()
                        self.logger.info("RTCP sniffer démarré (SO_REUSEPORT)")
                    except Exception as e:
                        self.logger.warning(f"RTCP sniffer non démarré: {e}")

                    await self.notify_state_change(PluginState.READY, {
                        "listening": True,
                        "rtp_port": self.rtp_port,
                        "audio_output": self.audio_output
                    })
                    return True

            return False

        except Exception as e:
            self.logger.error(f"Erreur démarrage ROC: {e}")
            return False

    async def stop(self) -> bool:
        """Arrêt ultra-simple du plugin ROC"""
        self.logger.info("Arrêt simple du plugin ROC")
        self._stopping = True

        # Cleanup des tâches
        if self.monitor_task:
            self.monitor_task.cancel()
            self.monitor_task = None

        # Arrêter le sniffer RTCP
        if getattr(self, "_rtcp_sniffer", None):
            transport = self._rtcp_sniffer.get("transport")
            if transport:
                try:
                    transport.close()
                except Exception:
                    pass
            self._rtcp_sniffer = None

        # Arrêter le service (sans vérification complexe)
        success = await self.control_service(self.service_name, "stop")

        # Reset état
        self.has_connections = False
        self.connected_ip = None
        await self.notify_state_change(PluginState.INACTIVE)

        self.logger.info(f"Arrêt ROC terminé: {success}")
        return success

    async def change_audio_device(self, new_device: str) -> bool:
        """Change le device audio de ROC - Version simplifiée pour ALSA dynamique"""
        if self._current_device == new_device:
            self.logger.info(f"ROC device already set to {new_device}")
            return True

        try:
            self.logger.info(f"Changing ROC device from {self._current_device} to {new_device}")
            self._current_device = new_device
            return True
        except Exception as e:
            self.logger.error(f"Error changing ROC device: {e}")
            return False

    async def _start_rtcp_sniffer(self):
        """
        Démarre un listener UDP partagé sur le port RTCP, dual-stack si possible.
        """
        loop = asyncio.get_running_loop()

        # Essai dual-stack sur '::' (sur beaucoup de kernels, ça accepte IPv4/IPv6)
        # On active reuse_port pour partager le port avec roc-toolkit.
        try:
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: _RtcpSniffer(self._on_rtcp_peer, self.logger),
                local_addr=('::', self.rtcp_port),
                reuse_port=True
            )
            return {"transport": transport, "protocol": protocol}
        except Exception:
            # Fallback IPv4 seulement
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: _RtcpSniffer(self._on_rtcp_peer, self.logger),
                local_addr=('0.0.0.0', self.rtcp_port),
                reuse_port=True
            )
            return {"transport": transport, "protocol": protocol}

    def _on_rtcp_peer(self, ip: Optional[str], port: int):
        """
        Callback du sniffer RTCP : met à jour l'IP connectée et déclenche un update.
        """
        if not ip:
            return
        if not self.has_connections:
            self.has_connections = True
        if ip != self.connected_ip:
            self.connected_ip = ip
            asyncio.create_task(self._update_state())

    async def _monitor_events(self):
        """Surveillance événementielle pure avec journalctl -f"""
        proc = None
        try:
            # Lire l'état initial
            await self._check_initial_state()

            # Surveillance temps réel
            proc = await asyncio.create_subprocess_exec(
                "journalctl", "-f", "-u", self.service_name, "-o", "short",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            self.logger.info("Surveillance événementielle ROC active")

            while not self._stopping and proc.returncode is None:
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=1.0)
                    if line:
                        log_line = line.decode('utf-8').strip()
                        await self._process_log_line(log_line)
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    self.logger.error(f"Erreur traitement log: {e}")
                    break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Erreur surveillance: {e}")
        finally:
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                    await proc.wait()
                except ProcessLookupError:
                    pass  # Le processus est déjà terminé

    async def _check_initial_state(self):
        """Vérifie l'état initial rapidement"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "journalctl", "-u", self.service_name, "-n", "50", "--no-pager",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()

            if proc.returncode == 0:
                lines = stdout.decode().split('\n')

                # Chercher les connexions récentes
                for line in reversed(lines):  # Plus récent en premier
                    if line.strip():
                        await self._process_log_line(line)

                # Si on a trouvé une activité, vérifier qu'il n'y a pas de déconnexion après
                if self.has_connections:
                    for line in lines:  # Dans l'ordre chronologique
                        if ("removing session" in line) or ("session router: removing route" in line):
                            self.logger.info("Déconnexion détectée dans l'état initial")
                            self.has_connections = False
                            self.connected_ip = None
                            break

        except Exception as e:
            self.logger.error(f"Erreur état initial: {e}")

    async def _process_log_line(self, line: str):
        """Traite une ligne de log - Détection connexion ET déconnexion"""
        try:
            # DÉCONNEXION - Patterns spécifiques de ROC (priorité)
            disconnection_patterns = [
                r"session group: removing session",
                r"session router: removing route",
                r"rtcp reporter: removing address"
            ]

            for pattern in disconnection_patterns:
                if re.search(pattern, line):
                    if self.has_connections:
                        self.logger.info(f"DÉCONNEXION détectée via log: {pattern}")
                        self.has_connections = False
                        self.connected_ip = None
                        await self._update_state()
                    return  # Important: sortir après détection

            # CONNEXION - Patterns d'activité
            activity_patterns = [
                r"depacketizer.*got first packet",
                r"delayed reader.*queue.*packets=",
                r"fec reader.*update",
                r"udp port.*recv=\d+.*send=\d+",
                r"session group: creating session",   # Nouvelle connexion explicite
                r"creating.*route.*address="          # Nouvelle route
            ]

            for pattern in activity_patterns:
                if re.search(pattern, line):
                    if not self.has_connections:
                        self.logger.info(f"CONNEXION détectée via log: {pattern}")
                        self.has_connections = True

                        # Essayer d'extraire IP/Port (IPv4/IPv6)
                        ip, _ = _parse_ip_from_line(line)
                        if ip:
                            self.connected_ip = _normalize_ip_for_storage(ip)
                            self.logger.info(f"IP trouvée dans la ligne: {self.connected_ip}")
                        elif not self.connected_ip:
                            await self._find_connection_ip()

                        await self._update_state()
                    return  # Important: sortir après détection

        except Exception as e:
            self.logger.error(f"Erreur process line: {e}")

    async def _find_connection_ip(self):
        """Trouve l'IP de connexion dans les logs récents (IPv4/IPv6)"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "journalctl", "-u", self.service_name, "-n", "100", "--no-pager",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()

            if proc.returncode == 0:
                logs = stdout.decode()
                m = _IP_PORT_RE.search(logs)
                if m:
                    ip = m.group('ip6') or m.group('ip4')
                    if ip:
                        self.connected_ip = _normalize_ip_for_storage(ip)
                        self.logger.info(f"IP trouvée: {self.connected_ip}")

        except Exception as e:
            self.logger.error(f"Erreur recherche IP: {e}")

    async def _resolve_hostname(self, ip: str) -> str:
        """
        Résout le hostname mDNS pour IPv4 ou IPv6.
        - Conserve un éventuel %scope sur IPv6 (indispensable pour fe80::/64).
        - Ajoute un %scope si l'IP est fe80::/64 et que self.network_interface est défini.
        - Force -6 pour IPv6.
        """
        if not ip:
            return "Mac connecté"

        try:
            ip_norm = _normalize_ip_for_storage(ip)
            scope = None

            # Séparer un scope éventuel sur IPv6
            if '%' in ip_norm:
                ip_only, scope = ip_norm.split('%', 1)
            else:
                ip_only = ip_norm

            addr = ipaddress.ip_address(ip_only)

            # Ajouter scope si link-local IPv6 et qu'on a une interface connue
            if addr.version == 6 and addr.is_link_local and scope is None and self.network_interface:
                ip_norm = f"{ip_only}%{self.network_interface}"

            # Construire la commande avahi-resolve
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
        """Met à jour l'état"""
        if self.has_connections:
            display_name = await self._resolve_hostname(self.connected_ip)

            await self.notify_state_change(PluginState.CONNECTED, {
                "listening": True,
                "rtp_port": self.rtp_port,
                "audio_output": self.audio_output,
                "connected": True,
                "client_ip": self.connected_ip,
                "client_name": display_name
            })
        else:
            await self.notify_state_change(PluginState.READY, {
                "listening": True,
                "rtp_port": self.rtp_port,
                "audio_output": self.audio_output,
                "connected": False
            })

    async def get_status(self) -> Dict[str, Any]:
        """État actuel"""
        try:
            service_status = await self.service_manager.get_status(self.service_name)
            client_name = await self._resolve_hostname(self.connected_ip) if self.connected_ip else None

            return {
                "service_active": service_status.get("active", False),
                "listening": service_status.get("active", False),
                "rtp_port": self.rtp_port,
                "audio_output": self.audio_output,
                "connected": self.has_connections,
                "client_ip": self.connected_ip,
                "client_name": client_name,
                "current_device": self._current_device
            }
        except Exception as e:
            return {"error": str(e), "current_device": self._current_device}

    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Commandes simples"""
        try:
            if command == "restart":
                success = await self.control_service(self.service_name, "restart")
                if success:
                    # Redémarrer la surveillance
                    if self.monitor_task:
                        self.monitor_task.cancel()
                        try:
                            await self.monitor_task
                        except asyncio.CancelledError:
                            pass

                    # Redémarrer le sniffer RTCP
                    if getattr(self, "_rtcp_sniffer", None):
                        transport = self._rtcp_sniffer.get("transport")
                        if transport:
                            try:
                                transport.close()
                            except Exception:
                                pass
                        self._rtcp_sniffer = None

                    await asyncio.sleep(1)
                    self.monitor_task = asyncio.create_task(self._monitor_events())

                    try:
                        self._rtcp_sniffer = await self._start_rtcp_sniffer()
                        self.logger.info("RTCP sniffer redémarré")
                    except Exception as e:
                        self.logger.warning(f"RTCP sniffer non redémarré: {e}")

                return format_response(success, "Redémarré" if success else "Échec")

            return format_response(False, error=f"Commande inconnue: {command}")

        except Exception as e:
            return format_response(False, error=str(e))

    async def get_initial_state(self) -> Dict[str, Any]:
        """État initial"""
        status = await self.get_status()
        return {**status, "plugin_state": self.current_state.value}