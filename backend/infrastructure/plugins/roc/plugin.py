# backend/infrastructure/plugins/roc/plugin.py
"""
Plugin ROC pour Milo - Version nettoyée sans EventBus
"""
import asyncio
import re
from typing import Dict, Any

from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.plugin_utils import format_response

class RocPlugin(UnifiedAudioPlugin):
    """Plugin ROC avec surveillance événementielle et détection précise"""

    def __init__(self, config: Dict[str, Any], state_machine=None):
        super().__init__("roc", state_machine)
        self.config = config
        self.service_name = config.get("service_name", "milo-roc.service")
        
        # Paramètres ROC
        self.rtp_port = config.get("rtp_port", 10001)
        self.rs8m_port = config.get("rs8m_port", 10002)
        self.rtcp_port = config.get("rtcp_port", 10003)
        self.audio_output = config.get("audio_output", "hw:1,0")
        
        # État
        self.has_connections = False
        self.connected_ip = None
        self.monitor_task = None
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
                    # Démarrer la surveillance événementielle
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
            
            # Mettre à jour juste le device (ALSA se charge du routage dynamique)
            self._current_device = new_device
            
            return True
        except Exception as e:
            self.logger.error(f"Error changing ROC device: {e}")
            return False

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
            # Ne pas re-raise ici, laisser le finally faire le cleanup
            pass
        except Exception as e:
            self.logger.error(f"Erreur surveillance: {e}")
        finally:
            # Terminer le processus une seule fois s'il existe
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
                        if "removing session" in line or "session router: removing route" in line:
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
                r"session group: creating session",  # Nouvelle connexion explicite
                r"creating.*route.*address="         # Nouvelle route
            ]
            
            for pattern in activity_patterns:
                if re.search(pattern, line):
                    if not self.has_connections:
                        self.logger.info(f"CONNEXION détectée via log: {pattern}")
                        self.has_connections = True
                        
                        # Chercher l'IP dans cette ligne ou les logs récents
                        ip_match = re.search(r"address=([0-9\.]+):\d+", line)
                        if ip_match:
                            self.connected_ip = ip_match.group(1)
                            self.logger.info(f"IP trouvée dans la ligne: {self.connected_ip}")
                        elif not self.connected_ip:
                            await self._find_connection_ip()
                        
                        await self._update_state()
                    return  # Important: sortir après détection
                
        except Exception as e:
            self.logger.error(f"Erreur process line: {e}")

    async def _find_connection_ip(self):
        """Trouve l'IP de connexion dans les logs récents"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "journalctl", "-u", self.service_name, "-n", "100", "--no-pager",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            
            if proc.returncode == 0:
                logs = stdout.decode()
                ip_patterns = [
                    r"address=([0-9\.]+):\d+",
                    r"src_addr=([0-9\.]+):\d+"
                ]
                
                for pattern in ip_patterns:
                    ip_match = re.search(pattern, logs)
                    if ip_match:
                        self.connected_ip = ip_match.group(1)
                        self.logger.info(f"IP trouvée: {self.connected_ip}")
                        break
                        
        except Exception as e:
            self.logger.error(f"Erreur recherche IP: {e}")

    async def _resolve_hostname(self, ip: str) -> str:
        """Résout le hostname mDNS"""
        if not ip:
            return "Mac connecté"
        
        try:
            proc = await asyncio.create_subprocess_exec(
                "avahi-resolve", "-a", ip,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            stdout, _ = await proc.communicate()
            
            if proc.returncode == 0:
                output = stdout.decode().strip()
                parts = output.split()
                if len(parts) >= 2:
                    return parts[1].rstrip('.')
        except:
            pass
        
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