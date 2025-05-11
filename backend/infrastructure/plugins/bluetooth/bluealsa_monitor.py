"""
Moniteur des connexions Bluetooth via bluealsa-cli monitor
"""
import asyncio
import logging
import re
from typing import Dict, Any, Optional, List, Callable, Awaitable

class BlueAlsaMonitor:
    """Surveille les PCMs BlueALSA via bluealsa-cli monitor"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.process = None
        self.connected_devices = {}
        self._connection_callback = None
        self._disconnection_callback = None
        self._stopped = False
    
    def set_callbacks(self, connection_callback, disconnection_callback):
        """Définit les callbacks pour les événements de connexion/déconnexion"""
        self._connection_callback = connection_callback
        self._disconnection_callback = disconnection_callback
    
    async def start_monitoring(self) -> bool:
        """Démarre la surveillance des PCMs BlueALSA"""
        try:
            self._stopped = False
            
            # Lancer bluealsa-cli monitor
            self.logger.info("Lancement de bluealsa-cli monitor")
            self.process = await asyncio.create_subprocess_exec(
                "bluealsa-cli", "monitor", "-p",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Démarrer la tâche de lecture de la sortie
            asyncio.create_task(self._read_output())
            
            self.logger.info("Surveillance BlueALSA démarrée")
            return True
        except Exception as e:
            self.logger.error(f"Erreur démarrage surveillance BlueALSA: {e}")
            return False
    
    async def stop_monitoring(self) -> None:
        """Arrête la surveillance des PCMs BlueALSA"""
        self._stopped = True
        if self.process:
            self.process.terminate()
            await self.process.wait()
            self.process = None
        self.connected_devices.clear()
    
    async def _read_output(self) -> None:
        """Lit la sortie de bluealsa-cli monitor"""
        if not self.process:
            return
        
        try:
            while not self._stopped and self.process and not self.process.stdout.at_eof():
                line = await self.process.stdout.readline()
                if not line:
                    break
                
                line_str = line.decode().strip()
                self.logger.debug(f"Ligne reçue: {line_str}")
                await self._parse_line(line_str)
        except Exception as e:
            if not self._stopped:
                self.logger.error(f"Erreur lecture sortie bluealsa-cli: {e}")
        finally:
            self.logger.info("Surveillance BlueALSA terminée")
    
    async def _parse_line(self, line: str) -> None:
        """Parse une ligne de sortie de bluealsa-cli monitor"""
        try:
            # PCM ajouté: PCMAdded /org/bluealsa/hci0/dev_XX_XX_XX_XX_XX_XX/a2dpsnk/source
            if line.startswith("PCMAdded"):
                path = line.split("PCMAdded ", 1)[1].strip()
                self.logger.info(f"PCM ajouté: {path}")
                device_info = self._extract_device_info(path)
                
                if device_info:
                    address = device_info["address"]
                    name = await self._get_device_name(address)
                    device_info["name"] = name
                    
                    self.logger.info(f"Appareil détecté: {name} ({address})")
                    
                    # Stocker l'appareil
                    self.connected_devices[address] = device_info
                    
                    # Notifier la connexion
                    if self._connection_callback:
                        await self._connection_callback(address, name)
                else:
                    self.logger.warning(f"Impossible d'extraire les informations de l'appareil depuis: {path}")
            
            # PCM supprimé: PCMRemoved /org/bluealsa/hci0/dev_XX_XX_XX_XX_XX_XX/a2dpsnk/source
            elif line.startswith("PCMRemoved"):
                path = line.split("PCMRemoved ", 1)[1].strip()
                self.logger.info(f"PCM supprimé: {path}")
                device_info = self._extract_device_info(path)
                
                if device_info:
                    address = device_info["address"]
                    if address in self.connected_devices:
                        name = self.connected_devices[address].get("name", "Unknown Device")
                        
                        # Supprimer l'appareil
                        del self.connected_devices[address]
                        
                        # Notifier la déconnexion
                        if self._disconnection_callback:
                            await self._disconnection_callback(address, name)
        except Exception as e:
            self.logger.error(f"Erreur parsing ligne: {e}")
    
    def _extract_device_info(self, path: str) -> Optional[Dict[str, Any]]:
        """Extrait les informations d'appareil à partir d'un chemin PCM"""
        try:
            # Format: /org/bluealsa/hci0/dev_XX_XX_XX_XX_XX_XX/a2dpsnk/source
            parts = path.split("/")
            
            # Extraire toutes les parties nécessaires
            if len(parts) >= 6:
                device_part = parts[-3]  # dev_XX_XX_XX_XX_XX_XX
                profile_part = parts[-2]  # a2dpsnk
                direction_part = parts[-1]  # source
                
                # Vérifier que c'est un PCM A2DP sink et source
                if device_part.startswith("dev_") and "a2dp" in profile_part.lower() and direction_part == "source":
                    address = device_part[4:].replace("_", ":")
                    return {
                        "address": address,
                        "path": path,
                        "type": "a2dp-sink"
                    }
        except Exception as e:
            self.logger.error(f"Erreur extraction infos appareil: {e}")
        
        return None
    
    async def _get_device_name(self, address: str) -> str:
        """Récupère le nom d'un appareil Bluetooth"""
        try:
            # Utiliser bluetoothctl pour récupérer le nom de l'appareil
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl", "info", address,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            
            stdout, _ = await proc.communicate()
            output = stdout.decode()
            
            # Rechercher le nom dans la sortie
            match = re.search(r"Name: (.+)$", output, re.MULTILINE)
            if match:
                return match.group(1).strip()
        except Exception as e:
            self.logger.error(f"Erreur récupération nom appareil: {e}")
        
        return "Unknown Device"
        
    async def get_connected_devices(self) -> List[Dict[str, Any]]:
        """Récupère la liste des appareils connectés"""
        return list(self.connected_devices.values())