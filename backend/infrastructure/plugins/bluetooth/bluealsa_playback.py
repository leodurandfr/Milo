"""
Gestionnaire de lecture audio via systemd - Version oakOS
"""
import asyncio
import logging

class BlueAlsaPlayback:
    """Gère la lecture audio avec oakos-bluealsa-aplay.service"""
    
    def __init__(self):
        self.logger = logging.getLogger("plugin.bluetooth.playback")
        self.service_name = "oakos-bluealsa-aplay.service"
    
    async def start_playback(self, address: str) -> bool:
        """Démarre la lecture audio via le service systemd"""
        try:
            # Le service est configuré pour auto-détecter les appareils
            # On vérifie juste qu'il est actif
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", self.service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            stdout, _ = await proc.communicate()
            
            if stdout.decode().strip() != "active":
                # Démarrer le service s'il n'est pas actif
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "systemctl", "start", self.service_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE
                )
                await proc.wait()
                return proc.returncode == 0
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur démarrage lecture: {e}")
            return False
    
    async def stop_playback(self, address: str) -> bool:
        """Arrête la lecture audio - optionnel car le service gère automatiquement"""
        # On pourrait arrêter le service, mais il est configuré pour
        # gérer automatiquement les connexions/déconnexions
        return True
    
    async def stop_all_playback(self) -> None:
        """Arrête le service de lecture"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "stop", self.service_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
        except Exception as e:
            self.logger.error(f"Erreur arrêt service: {e}")
    
    def is_playing(self, address: str) -> bool:
        """Vérifie si le service est actif"""
        try:
            import subprocess
            result = subprocess.run(
                ["systemctl", "is-active", self.service_name],
                capture_output=True,
                text=True
            )
            return result.stdout.strip() == "active"
        except Exception:
            return False