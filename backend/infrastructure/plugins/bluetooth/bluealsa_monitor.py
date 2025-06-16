# backend/infrastructure/plugins/bluetooth/bluealsa_monitor.py
"""
Moniteur des connexions Bluetooth via bluealsa-cli monitor - Version concise
"""
import asyncio
import logging
import re
from typing import Dict, Any, Optional, List, Callable, Awaitable

class BlueAlsaMonitor:
    """Surveille les PCMs BlueALSA via bluealsa-cli monitor"""
    
    def __init__(self):
        self.logger = logging.getLogger("plugin.bluetooth.monitor")
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
            self.process = await asyncio.create_subprocess_exec(
                "bluealsa-cli", "monitor", "-p",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Démarrer la tâche de lecture de la sortie
            asyncio.create_task(self._read_output())
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur démarrage surveillance: {e}")
            return False
    
    async def stop_monitoring(self) -> None:
        """Arrête la surveillance des PCMs BlueALSA"""
        self._stopped = True
        
        if self.process and self.process.returncode is None:
            try:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            except ProcessLookupError:
                pass
            except Exception as e:
                self.logger.error(f"Error stopping monitor: {e}")
            finally:
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
                await self._parse_line(line_str)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if not self._stopped:
                self.logger.error(f"Erreur lecture: {e}")
    
    async def _parse_line(self, line: str) -> None:
        """Parse une ligne de sortie de bluealsa-cli monitor"""
        try:
            if line.startswith("PCMAdded"):
                await self._handle_pcm_added(line)
            elif line.startswith("PCMRemoved"):
                await self._handle_pcm_removed(line)
        except Exception as e:
            self.logger.error(f"Erreur parsing: {e}")
    
    async def _handle_pcm_added(self, line: str) -> None:
        """Traite un événement PCM ajouté"""
        path = line.split("PCMAdded ", 1)[1].strip()
        device_info = self._extract_device_info(path)
        
        if device_info:
            address = device_info["address"]
            name = await self._get_device_name(address)
            device_info["name"] = name
            
            # Stocker l'appareil
            self.connected_devices[address] = device_info
            
            # Notifier la connexion
            if self._connection_callback:
                await self._connection_callback(address, name)
    
    async def _handle_pcm_removed(self, line: str) -> None:
        """Traite un événement PCM supprimé"""
        path = line.split("PCMRemoved ", 1)[1].strip()
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
    
    def _extract_device_info(self, path: str) -> Optional[Dict[str, Any]]:
        """Extrait les informations d'appareil à partir d'un chemin PCM"""
        parts = path.split("/")
        
        # Vérifier le format du chemin
        if len(parts) < 6:
            return None
            
        device_part = parts[-3]  # dev_XX_XX_XX_XX_XX_XX
        profile_part = parts[-2]  # a2dpsnk
        direction_part = parts[-1]  # source
        
        # Filtrer pour ne garder que les PCM A2DP sink/source
        if device_part.startswith("dev_") and "a2dp" in profile_part.lower() and direction_part == "source":
            address = device_part[4:].replace("_", ":")
            return {
                "address": address,
                "path": path,
                "type": "a2dp-sink"
            }
        
        return None
    
    async def _get_device_name(self, address: str) -> str:
        """Récupère le nom d'un appareil Bluetooth"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl", "info", address,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            
            stdout, _ = await proc.communicate()
            output = stdout.decode()
            
            # Rechercher le nom dans la sortie
            match = re.search(r"Name: (.+)$", output, re.MULTILINE)
            return match.group(1).strip() if match else "Unknown Device"
        except Exception as e:
            self.logger.error(f"Erreur récupération nom: {e}")
            return "Unknown Device"