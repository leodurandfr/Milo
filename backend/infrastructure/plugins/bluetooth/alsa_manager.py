"""
Gestionnaire pour l'intégration avec ALSA
"""
import logging
import subprocess
from typing import Dict, Any, List, Optional

class AlsaManager:
    """Gère les interactions avec ALSA pour l'audio Bluetooth"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def get_bluealsa_pcms(self) -> List[Dict[str, Any]]:
        """Récupère la liste des PCMs BlueALSA disponibles"""
        try:
            result = subprocess.run(
                ["bluealsa-aplay", "-L"], 
                capture_output=True, 
                text=True, 
                check=True
            )
            
            lines = result.stdout.strip().split('\n')
            pcms = []
            
            device = None
            for line in lines:
                if not line.startswith(" "):
                    # Ligne de périphérique
                    device = line.strip()
                elif device and line.strip():
                    # Ligne de description
                    description = line.strip()
                    pcms.append({
                        "device": device,
                        "description": description
                    })
            
            return pcms
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération des PCMs BlueALSA: {e}")
            return []
    
    def get_device_volume(self, device: str) -> Optional[int]:
        """Récupère le volume d'un périphérique BlueALSA"""
        try:
            result = subprocess.run(
                ["amixer", "-D", "bluealsa", "sget", device],
                capture_output=True,
                text=True,
                check=True
            )
            
            # Analyser la sortie pour trouver le volume
            for line in result.stdout.split('\n'):
                if "Playback" in line and "%" in line:
                    # Extraire le pourcentage
                    start = line.find("[") + 1
                    end = line.find("%")
                    if start > 0 and end > start:
                        return int(line[start:end])
            
            return None
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération du volume: {e}")
            return None
    
    def set_device_volume(self, device: str, volume: int) -> bool:
        """Définit le volume d'un périphérique BlueALSA"""
        try:
            # Limiter le volume entre 0 et 100
            volume = max(0, min(100, volume))
            
            result = subprocess.run(
                ["amixer", "-D", "bluealsa", "sset", device, f"{volume}%"],
                capture_output=True,
                check=True
            )
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de la définition du volume: {e}")
            return False