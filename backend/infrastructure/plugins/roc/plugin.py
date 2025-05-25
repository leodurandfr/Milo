"""
Plugin ROC pour oakOS - Récepteur audio réseau
"""
import asyncio
from typing import Dict, Any

from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.plugin_utils import format_response

class RocPlugin(UnifiedAudioPlugin):
    """Plugin pour réception audio ROC"""

    def __init__(self, event_bus, config: Dict[str, Any]):
        super().__init__(event_bus, "roc")
        self.config = config
        self.service_name = config.get("service_name", "oakos-roc.service")
        
        # Paramètres ROC
        self.rtp_port = config.get("rtp_port", 10001)
        self.rs8m_port = config.get("rs8m_port", 10002)
        self.rtcp_port = config.get("rtcp_port", 10003)
        self.audio_output = config.get("audio_output", "hw:1,0")

    async def _do_initialize(self) -> bool:
        """Initialisation du plugin ROC"""
        try:
            # Vérifier que le service systemd existe
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "list-unit-files", self.service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            
            if proc.returncode != 0 or self.service_name not in stdout.decode():
                raise RuntimeError(f"Service {self.service_name} non trouvé")
            
            # Vérifier que roc-recv est disponible
            proc = await asyncio.create_subprocess_exec(
                "which", "roc-recv",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            
            if proc.returncode != 0:
                raise RuntimeError("roc-recv non trouvé dans le PATH")
                
            self.logger.info("Plugin ROC initialisé avec succès")
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur initialisation ROC: {e}")
            return False

    async def _do_start(self) -> bool:
        """Démarrage du service ROC"""
        try:
            # Démarrer le service systemd
            success = await self.control_service(self.service_name, "start")
            
            if success:
                # Attendre un peu pour que le service démarre
                await asyncio.sleep(1)
                
                # Vérifier que le service est actif
                is_active = await self.service_manager.is_active(self.service_name)
                if is_active:
                    await self.notify_state_change(PluginState.READY, {
                        "listening": True,
                        "rtp_port": self.rtp_port,
                        "audio_output": self.audio_output
                    })
                    return True
                else:
                    self.logger.error("Service démarré mais non actif")
                    return False
            
            return False
            
        except Exception as e:
            self.logger.error(f"Erreur démarrage ROC: {e}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            return False

    async def stop(self) -> bool:
        """Arrêt du plugin ROC"""
        try:
            # Arrêter le service systemd
            success = await self.control_service(self.service_name, "stop")
            await self.notify_state_change(PluginState.INACTIVE)
            return success
            
        except Exception as e:
            self.logger.error(f"Erreur arrêt ROC: {e}")
            return False

    async def get_status(self) -> Dict[str, Any]:
        """Récupère l'état actuel du plugin"""
        try:
            service_status = await self.service_manager.get_status(self.service_name)
            
            return {
                "service_active": service_status.get("active", False),
                "service_running": service_status.get("running", False),
                "service_state": service_status.get("state", "unknown"),
                "listening": service_status.get("active", False),
                "rtp_port": self.rtp_port,
                "rs8m_port": self.rs8m_port,
                "rtcp_port": self.rtcp_port,
                "audio_output": self.audio_output
            }
            
        except Exception as e:
            self.logger.error(f"Erreur status ROC: {e}")
            return {"error": str(e)}

    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite les commandes du plugin"""
        try:
            if command == "restart":
                success = await self.control_service(self.service_name, "restart")
                return format_response(
                    success,
                    message="Service ROC redémarré" if success else None,
                    error="Échec du redémarrage" if not success else None
                )
            
            elif command == "get_logs":
                # Récupérer les logs du service
                proc = await asyncio.create_subprocess_exec(
                    "journalctl", "-u", self.service_name, "-n", "20", "--no-pager",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                
                return format_response(
                    True,
                    logs=stdout.decode().strip().split('\n')
                )
            
            return format_response(False, error=f"Commande inconnue: {command}")
            
        except Exception as e:
            self.logger.error(f"Erreur commande {command}: {e}")
            return format_response(False, error=str(e))

    async def get_initial_state(self) -> Dict[str, Any]:
        """État initial pour les nouvelles connexions WebSocket"""
        status = await self.get_status()
        return {
            **status,
            "is_active": self.current_state != PluginState.INACTIVE,
            "plugin_state": self.current_state.value
        }