"""
Gestionnaire de lecture audio via bluealsa-aplay - Version concise
"""
import asyncio
import logging

class BlueAlsaPlayback:
    """Gère la lecture audio avec bluealsa-aplay"""
    
    def __init__(self):
        self.logger = logging.getLogger("plugin.bluetooth.playback")
        self.playback_processes = {}
    
    async def start_playback(self, address: str) -> bool:
        """Démarre la lecture audio pour un appareil Bluetooth"""
        await self.stop_playback(address)
        
        try:
            process = await asyncio.create_subprocess_exec(
                "bluealsa-aplay", "--profile-a2dp", address,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            
            self.playback_processes[address] = process
            return True
        except Exception as e:
            self.logger.error(f"Erreur démarrage lecture: {e}")
            return False
    
    async def stop_playback(self, address: str) -> bool:
        """Arrête la lecture audio pour un appareil Bluetooth"""
        if address not in self.playback_processes:
            return True
            
        try:
            process = self.playback_processes[address]
            
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 2.0)
            except asyncio.TimeoutError:
                process.kill()
            
            del self.playback_processes[address]
            return True
        except Exception as e:
            self.logger.error(f"Erreur arrêt lecture: {e}")
            return False
    
    async def stop_all_playback(self) -> None:
        """Arrête toute lecture audio"""
        addresses = list(self.playback_processes.keys())
        for address in addresses:
            await self.stop_playback(address)
    
    def is_playing(self, address: str) -> bool:
        """Vérifie si la lecture est active pour un appareil"""
        return (address in self.playback_processes and 
                self.playback_processes[address].returncode is None)