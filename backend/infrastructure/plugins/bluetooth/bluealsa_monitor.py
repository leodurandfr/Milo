# backend/infrastructure/plugins/bluetooth/bluealsa_monitor.py
"""
Connection monitor Bluetooth via bluealsa-cli monitor - Version concise corrigée
"""
import asyncio
import logging
import re
from typing import Dict, Any, Optional, List, Callable, Awaitable

class BlueAlsaMonitor:
    """Monitors BlueALSA PCMs via bluealsa-cli monitor"""
    
    def __init__(self):
        self.logger = logging.getLogger("plugin.bluetooth.monitor")
        self.process = None
        self.connected_devices = {}
        self._connection_callback = None
        self._disconnection_callback = None
        self._stopped = False
    
    def set_callbacks(self, connection_callback, disconnection_callback):
        """Sets callbacks pour les événements de connexion/déconnexion"""
        self._connection_callback = connection_callback
        self._disconnection_callback = disconnection_callback
    
    async def start_monitoring(self) -> bool:
        """Starts monitoring des PCMs BlueALSA"""
        try:
            self._stopped = False
            
            # Launch bluealsa-cli monitor
            self.process = await asyncio.create_subprocess_exec(
                "bluealsa-cli", "monitor", "-p",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Start la tâche de lecture de la sortie
            asyncio.create_task(self._read_output())
            
            return True
        except Exception as e:
            self.logger.error(f"Error démarrage surveillance: {e}")
            return False
    
    async def stop_monitoring(self) -> None:
        """Stoppinge la surveillance des PCMs BlueALSA proprement"""
        self._stopped = True
        
        if self.process and self.process.returncode is None:
            try:
                # Terminate cleanly le processus
                self.process.terminate()
                
                # Wait la terminaison (avec timeout raisonnable)
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    # Si timeout, kill définitivement
                    self.process.kill()
                    await self.process.wait()
                    
            except ProcessLookupError:
                # Process already terminated
                pass
            except Exception as e:
                self.logger.error(f"Error stopping monitor process: {e}")
            finally:
                self.process = None
        
        self.connected_devices.clear()
    
    async def _read_output(self) -> None:
        """Reads output de bluealsa-cli monitor"""
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
                self.logger.error(f"Error lecture: {e}")
    
    async def _parse_line(self, line: str) -> None:
        """Parses a line de sortie de bluealsa-cli monitor"""
        try:
            if line.startswith("PCMAdded"):
                await self._handle_pcm_added(line)
            elif line.startswith("PCMRemoved"):
                await self._handle_pcm_removed(line)
        except Exception as e:
            self.logger.error(f"Error parsing: {e}")
    
    async def _handle_pcm_added(self, line: str) -> None:
        """Processes PCM added event"""
        path = line.split("PCMAdded ", 1)[1].strip()
        device_info = self._extract_device_info(path)
        
        if device_info:
            address = device_info["address"]
            name = await self._get_device_name(address)
            device_info["name"] = name
            
            # Store device
            self.connected_devices[address] = device_info
            
            # Notify connection
            if self._connection_callback:
                await self._connection_callback(address, name)
    
    async def _handle_pcm_removed(self, line: str) -> None:
        """Processes PCM removed event"""
        path = line.split("PCMRemoved ", 1)[1].strip()
        device_info = self._extract_device_info(path)
        
        if device_info:
            address = device_info["address"]
            if address in self.connected_devices:
                name = self.connected_devices[address].get("name", "Unknown Device")
                
                # Remove device
                del self.connected_devices[address]
                
                # Notify disconnection
                if self._disconnection_callback:
                    await self._disconnection_callback(address, name)
    
    def _extract_device_info(self, path: str) -> Optional[Dict[str, Any]]:
        """Extracts information d'appareil à partir d'un chemin PCM"""
        parts = path.split("/")
        
        # Verify le format du chemin
        if len(parts) < 6:
            return None
            
        device_part = parts[-3]  # dev_XX_XX_XX_XX_XX_XX
        profile_part = parts[-2]  # a2dpsnk
        direction_part = parts[-1]  # source
        
        # Filter pour ne garder que les PCM A2DP sink/source
        if device_part.startswith("dev_") and "a2dp" in profile_part.lower() and direction_part == "source":
            address = device_part[4:].replace("_", ":")
            return {
                "address": address,
                "path": path,
                "type": "a2dp-sink"
            }
        
        return None
    
    async def _get_device_name(self, address: str) -> str:
        """Gets name d'un appareil Bluetooth - Version non-bloquante"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl", "info", address,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            
            # Timeout court pour éviter les blocages
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
                output = stdout.decode()
                
                # Search name dans la sortie
                match = re.search(r"Name: (.+)$", output, re.MULTILINE)
                return match.group(1).strip() if match else f"Device {address}"
                
            except asyncio.TimeoutError:
                # Kill process et retourner l'adresse
                proc.kill()
                await proc.wait()
                return f"Device {address}"
                
        except Exception as e:
            self.logger.debug(f"Could not get device name for {address}: {e}")
            return f"Device {address}"