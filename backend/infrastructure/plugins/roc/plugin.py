# backend/infrastructure/plugins/roc/plugin.py
"""
Plugin ROC pour Milo - Support multi-clients avec IPv4/IPv6 et mDNS
Gère plusieurs Mac connectés simultanément en trackant leurs IPs et noms via les logs ROC.
"""
import asyncio
import re
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


class RocPlugin(UnifiedAudioPlugin):
    """
    Plugin ROC avec support multi-clients simultanés.

    Features:
    - Tracking de plusieurs Mac connectés via dict {ip: display_name}
    - Surveillance des logs (journalctl) pour détection connexion/déconnexion avec IPs
    - Résolution mDNS (avahi-resolve) pour obtenir les noms des Mac
    - Support IPv4/IPv6 (incluant link-local avec %scope)
    """

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

        # État - Tracking de plusieurs clients connectés
        self.connected_clients: Dict[str, str] = {}  # {ip: display_name}
        self.monitor_task: Optional[asyncio.Task] = None
        self._stopping = False
        self._current_device = "milo_roc"

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

        # Arrêter le service
        success = await self.control_service(self.service_name, "stop")

        # Reset état
        self.connected_clients.clear()
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

    async def _detect_active_connections(self):
        """
        Détecte les connexions actives via tcpdump si Mac déjà connecté avant démarrage ROC.
        Capture brièvement les paquets sur les ports ROC pour extraire les IPs sources.
        """
        try:
            self.logger.info("Lancement tcpdump pour détecter connexions actives...")

            # Lancer tcpdump : capture max 15 paquets ou timeout 3s
            proc = await asyncio.create_subprocess_exec(
                "sudo", "tcpdump",
                "-i", "any",           # Toutes les interfaces
                "-n",                  # Pas de résolution DNS
                "-l",                  # Line buffered
                "-c", "15",            # Max 15 paquets
                f"udp and (port {self.rtp_port} or port {self.rs8m_port} or port {self.rtcp_port})",
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
                self.logger.info(f"Connexions actives détectées via tcpdump: {detected_ips}")
                for ip in detected_ips:
                    await self._add_client(ip)
            else:
                self.logger.info("Aucune connexion active détectée via tcpdump")

        except Exception as e:
            self.logger.warning(f"Erreur détection tcpdump (non-bloquant): {e}")

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
        """Vérifie l'état initial en analysant les derniers logs"""
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
                self.logger.info("Aucune connexion dans les logs, recherche active avec tcpdump...")
                await self._detect_active_connections()

        except Exception as e:
            self.logger.error(f"Erreur état initial: {e}")

    async def _process_log_line(self, line: str):
        """Traite une ligne de log - Détection connexion ET déconnexion avec IP"""
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
                        self.logger.info(f"DÉCONNEXION détectée: {client_name} ({ip})")
                        del self.connected_clients[ip]
                        await self._update_state()
                else:
                    # Si on ne trouve pas l'IP, on log un warning
                    self.logger.warning(f"Déconnexion détectée sans IP: {line[:100]}")
                return

            # CONNEXION - Pattern: "session group: creating session"
            if "session group: creating session" in line:
                # Chercher l'IP dans cette ligne ou les suivantes
                ip, _ = _parse_ip_from_line(line)
                if ip:
                    ip = _normalize_ip_for_storage(ip)
                    if ip not in self.connected_clients:
                        self.logger.info(f"CONNEXION détectée: {ip}")
                        await self._add_client(ip)
                return

            # CONNEXION via route - Pattern: "creating.*route.*address="
            if "creating" in line and "route" in line and "address=" in line:
                ip, _ = _parse_ip_from_line(line)
                if ip:
                    ip = _normalize_ip_for_storage(ip)
                    if ip not in self.connected_clients:
                        self.logger.info(f"CONNEXION détectée (route): {ip}")
                        await self._add_client(ip)
                return

        except Exception as e:
            self.logger.error(f"Erreur process line: {e}")

    async def _add_client(self, ip: str):
        """Ajoute un nouveau client au tracking et résout son nom"""
        if ip in self.connected_clients:
            return

        # Résoudre le hostname
        display_name = await self._resolve_hostname(ip)
        self.connected_clients[ip] = display_name
        self.logger.info(f"Client ajouté: {display_name} ({ip})")

        # Mettre à jour l'état
        await self._update_state()

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
        """Met à jour l'état avec la liste des clients connectés"""
        if self.connected_clients:
            # Liste des noms de clients
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
        """État actuel avec liste des clients connectés"""
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

                    await asyncio.sleep(1)
                    self.monitor_task = asyncio.create_task(self._monitor_events())

                return format_response(success, "Redémarré" if success else "Échec")

            return format_response(False, error=f"Commande inconnue: {command}")

        except Exception as e:
            return format_response(False, error=str(e))

    async def get_initial_state(self) -> Dict[str, Any]:
        """État initial"""
        status = await self.get_status()
        return {**status, "plugin_state": self.current_state.value}