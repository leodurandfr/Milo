"""
Gestionnaire de lecture audio via bluealsa-aplay
"""
import asyncio
import logging
from typing import Dict

class BlueAlsaPlayback:
    """Gère la lecture audio avec bluealsa-aplay"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.playback_processes = {}
    
    async def start_playback(self, address: str) -> bool:
        """Démarre la lecture audio pour un appareil Bluetooth"""
        # Arrêter d'abord si déjà en cours
        await self.stop_playback(address)
        
        try:
            self.logger.info(f"Démarrage lecture audio pour {address}")
            
            # Lancer bluealsa-aplay avec l'adresse de l'appareil
            process = await asyncio.create_subprocess_exec(
                "bluealsa-aplay", "--profile-a2dp", address,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Ajouter au dictionnaire des processus
            self.playback_processes[address] = process
            
            self.logger.info(f"Lecture audio démarrée pour {address}")
            return True
        except Exception as e:
            self.logger.error(f"Erreur démarrage lecture audio: {e}")
            return False
    
    async def stop_playback(self, address: str) -> bool:
        """Arrête la lecture audio pour un appareil Bluetooth"""
        if address in self.playback_processes:
            try:
                process = self.playback_processes[address]
                self.logger.info(f"Arrêt lecture audio pour {address}")
                
                # Terminer le processus proprement
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 2.0)
                except asyncio.TimeoutError:
                    process.kill()
                
                # Supprimer du dictionnaire
                del self.playback_processes[address]
                
                return True
            except Exception as e:
                self.logger.error(f"Erreur arrêt lecture audio: {e}")
                return False
        
        return True  # Déjà arrêté
    
    async def stop_all_playback(self) -> None:
        """Arrête toute lecture audio"""
        addresses = list(self.playback_processes.keys())
        for address in addresses:
            await self.stop_playback(address)
    
    def is_playing(self, address: str) -> bool:
        """Vérifie si la lecture est active pour un appareil"""
        return address in self.playback_processes and self.playback_processes[address].returncode is None