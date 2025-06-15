"""
Gestionnaire pour les services systemd.
"""
import asyncio
import logging
from typing import Dict, Any

class SystemdServiceManager:
    """Gestionnaire générique pour les services systemd."""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    async def start(self, service: str) -> bool:
        """Démarre un service systemd."""
        return await self._control_service(service, "start")
    
    async def stop(self, service: str) -> bool:
        """Arrête un service systemd."""
        return await self._control_service(service, "stop")
    
    async def restart(self, service: str) -> bool:
        """Redémarre un service systemd."""
        return await self._control_service(service, "restart")
    
    async def is_active(self, service: str) -> bool:
        """Vérifie si un service est actif."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", service,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            stdout, _ = await proc.communicate()
            return stdout.decode().strip() == "active"
        except Exception as e:
            self.logger.error(f"Erreur vérification service {service}: {e}")
            return False
    
    async def get_status(self, service: str) -> Dict[str, Any]:
        """Récupère le statut détaillé d'un service."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "show", service, 
                "--property=ActiveState,SubState,ExecMainStatus",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                self.logger.error(f"Erreur lors de la récupération du statut: {stderr.decode().strip()}")
                return {"error": "Impossible de récupérer le statut"}
            
            lines = stdout.decode().strip().split('\n')
            status = {}
            
            for line in lines:
                if '=' in line:
                    key, value = line.split('=', 1)
                    status[key] = value
            
            return {
                "active": status.get("ActiveState") == "active",
                "running": status.get("SubState") == "running",
                "exit_code": int(status.get("ExecMainStatus", "0")),
                "state": status.get("ActiveState", "unknown"),
                "substate": status.get("SubState", "unknown")
            }
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération du statut: {e}")
            return {"error": str(e)}
    
    async def _control_service(self, service: str, action: str) -> bool:
        """Contrôle un service systemd."""
        try:
            self.logger.info(f"{action.capitalize()} du service {service}")
            
            # Utiliser sudo pour avoir les permissions nécessaires
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", action, service,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            
            _, stderr = await asyncio.wait_for(proc.communicate(), 10.0)
            
            if proc.returncode != 0:
                error_msg = stderr.decode().strip() if stderr else "No error details"
                self.logger.error(f"Failed to {action} {service} (exit code {proc.returncode}): {error_msg}")
                return False
            
            # Attendre que le service soit dans l'état souhaité
            expected_active = action != "stop"
            for i in range(5):
                await asyncio.sleep(0.5)
                active = await self.is_active(service)
                if active == expected_active:
                    return True
            
            # Message d'erreur plus explicite si l'état attendu n'est pas atteint
            actual_state = "active" if await self.is_active(service) else "inactive"
            expected_state = "active" if expected_active else "inactive"
            self.logger.error(f"Service {service} is {actual_state} but expected {expected_state} after {action}")
            return False
            
        except asyncio.TimeoutError:
            self.logger.error(f"Timeout ({action} {service} took more than 10 seconds)")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error during {action} {service}: {e}")
            return False