"""
Plugin ROC pour oakOS - Version OPTIM (simple et efficace)
"""
import asyncio
import re
from typing import Dict, Any

from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.plugin_utils import format_response

class RocPlugin(UnifiedAudioPlugin):
    """Plugin ROC simple avec vérification périodique légère"""

    def __init__(self, event_bus, config: Dict[str, Any]):
        super().__init__(event_bus, "roc")
        self.config = config
        self.service_name = config.get("service_name", "oakos-roc.service")
        
        # Paramètres ROC
        self.rtp_port = config.get("rtp_port", 10001)
        self.rs8m_port = config.get("rs8m_port", 10002)
        self.rtcp_port = config.get("rtcp_port", 10003)
        self.audio_output = config.get("audio_output", "hw:1,0")
        
        # État simple
        self.has_connections = False
        self.connected_ip = None
        self.check_task = None
        self._stopping = False

    async def _do_initialize(self) -> bool:
        """Initialisation du plugin ROC"""
        try:
            # Vérifications de base
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
                    # Démarrer la vérification simple
                    self._stopping = False
                    self.check_task = asyncio.create_task(self._check_connections())
                    
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
        """Arrêt du plugin ROC"""
        try:
            self._stopping = True
            
            # Arrêter la vérification
            if self.check_task:
                self.check_task.cancel()
                try:
                    await self.check_task
                except asyncio.CancelledError:
                    pass
            
            # Arrêter le service
            success = await self.control_service(self.service_name, "stop")
            
            # Reset état
            self.has_connections = False
            self.connected_ip = None
            
            await self.notify_state_change(PluginState.INACTIVE)
            return success
            
        except Exception as e:
            self.logger.error(f"Erreur arrêt ROC: {e}")
            return False

    async def _check_connections(self):
        """Vérification simple et périodique"""
        while not self._stopping:
            try:
                await asyncio.sleep(1)  # 1 seconde
                if not self._stopping:
                    await self._check_activity()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Erreur vérification: {e}")

    async def _check_activity(self):
        """Vérifie l'activité dans les logs récents"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "journalctl", "-u", self.service_name, "-n", "10", "--no-pager", "-o", "short",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            
            if proc.returncode == 0:
                logs = stdout.decode()
                
                # Chercher une connexion active
                has_activity = bool(re.search(r"latency tuner.*e2e_latency=|depacketizer.*loss_ratio=", logs))
                
                # Extraire l'IP si trouvée
                ip_match = re.search(r"address=([0-9\.]+):\d+", logs)
                current_ip = ip_match.group(1) if ip_match else None
                
                # Mettre à jour si changement
                if has_activity != self.has_connections or current_ip != self.connected_ip:
                    self.has_connections = has_activity
                    self.connected_ip = current_ip
                    await self._update_state()
                    
        except Exception as e:
            self.logger.error(f"Erreur vérification activité: {e}")

    async def _update_state(self):
        """Met à jour l'état"""
        if self.has_connections:
            await self.notify_state_change(PluginState.CONNECTED, {
                "listening": True,
                "rtp_port": self.rtp_port,
                "audio_output": self.audio_output,
                "connected": True,
                "client_ip": self.connected_ip,
                "client_name": self.connected_ip  # Simple : juste l'IP
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
            return {
                "service_active": service_status.get("active", False),
                "listening": service_status.get("active", False),
                "rtp_port": self.rtp_port,
                "audio_output": self.audio_output,
                "connected": self.has_connections,
                "client_ip": self.connected_ip
            }
        except Exception as e:
            return {"error": str(e)}

    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Commandes simples"""
        try:
            if command == "restart":
                success = await self.control_service(self.service_name, "restart")
                return format_response(success, "Redémarré" if success else "Échec")
            
            return format_response(False, error=f"Commande inconnue: {command}")
            
        except Exception as e:
            return format_response(False, error=str(e))

    async def get_initial_state(self) -> Dict[str, Any]:
        """État initial"""
        status = await self.get_status()
        return {**status, "plugin_state": self.current_state.value}