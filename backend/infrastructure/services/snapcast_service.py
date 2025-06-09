# backend/infrastructure/services/snapcast_service.py
"""
Service Snapcast CORRIGÉ - Parser custom pour snapserver.conf + gestion permissions
"""
import aiohttp
import asyncio
import aiofiles
import logging
import re
from typing import List, Dict, Any, Optional
from pathlib import Path

class SnapcastService:
    """Service Snapcast avec parser custom pour snapserver.conf"""
    
    def __init__(self, host: str = "localhost", port: int = 1780):
        self.base_url = f"http://{host}:{port}/jsonrpc"
        self.logger = logging.getLogger(__name__)
        self._request_id = 0
        
        # Fichier de configuration Snapcast
        self.snapserver_conf = Path("/etc/snapserver.conf")
    
    async def _request(self, method: str, params: dict = None) -> dict:
        """Requête JSON-RPC vers Snapcast"""
        self._request_id += 1
        request = {"id": self._request_id, "jsonrpc": "2.0", "method": method}
        if params:
            request["params"] = params
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.base_url, 
                    json=request, 
                    timeout=aiohttp.ClientTimeout(total=3)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        if "result" in data:
                            return data["result"]
            return {}
        except Exception as e:
            self.logger.error(f"Snapcast request failed: {e}")
            return {}
    
    # === MÉTHODES DE BASE (conservées intégralement) ===
    
    async def get_clients(self) -> List[Dict[str, Any]]:
        """Récupère les clients connectés (filtrés)"""
        try:
            status = await self._request("Server.GetStatus")
            clients = []
            exclude_names = ['snapweb client', 'snapweb']
            
            for group in status.get("server", {}).get("groups", []):
                for client_data in group.get("clients", []):
                    if not client_data["connected"]:
                        continue
                    
                    name = client_data["config"]["name"] or client_data["host"]["name"]
                    if any(exclude in name.lower() for exclude in exclude_names):
                        continue
                    
                    clients.append({
                        "id": client_data["id"],
                        "name": name,
                        "volume": client_data["config"]["volume"]["percent"],
                        "muted": client_data["config"]["volume"]["muted"],
                        "host": client_data["host"]["name"],
                        "ip": client_data["host"]["ip"].replace("::ffff:", "")
                    })
            
            return clients
        except Exception as e:
            self.logger.error(f"Error getting clients: {e}")
            return []
    
    async def set_volume(self, client_id: str, volume: int) -> bool:
        """Change le volume d'un client"""
        try:
            clients = await self.get_clients()
            current_muted = False
            for client in clients:
                if client["id"] == client_id:
                    current_muted = client["muted"]
                    break
            
            result = await self._request("Client.SetVolume", {
                "id": client_id,
                "volume": {"percent": max(0, min(100, volume)), "muted": current_muted}
            })
            return bool(result)
        except:
            return False
    
    async def set_mute(self, client_id: str, muted: bool) -> bool:
        """Mute/unmute un client"""
        try:
            clients = await self.get_clients()
            current_volume = 50
            for client in clients:
                if client["id"] == client_id:
                    current_volume = client["volume"]
                    break
            
            result = await self._request("Client.SetVolume", {
                "id": client_id,
                "volume": {"percent": current_volume, "muted": muted}
            })
            return bool(result)
        except:
            return False
    
    async def set_client_latency(self, client_id: str, latency: int) -> bool:
        """Configure la latence d'un client"""
        try:
            result = await self._request("Client.SetLatency", {
                "id": client_id,
                "latency": max(0, min(1000, latency))
            })
            return bool(result)
        except Exception as e:
            self.logger.error(f"Error setting client latency: {e}")
            return False
    
    async def set_client_name(self, client_id: str, name: str) -> bool:
        """Configure le nom d'un client"""
        try:
            result = await self._request("Client.SetName", {
                "id": client_id,
                "name": name
            })
            return bool(result)
        except Exception as e:
            self.logger.error(f"Error setting client name: {e}")
            return False
    
    async def get_detailed_clients(self) -> List[Dict[str, Any]]:
        """Récupère les clients avec informations détaillées"""
        try:
            status = await self._request("Server.GetStatus")
            clients = []
            exclude_names = ['snapweb client', 'snapweb']
            
            for group in status.get("server", {}).get("groups", []):
                for client_data in group.get("clients", []):
                    if not client_data["connected"]:
                        continue
                    
                    name = client_data["config"]["name"] or client_data["host"]["name"]
                    if any(exclude in name.lower() for exclude in exclude_names):
                        continue
                    
                    last_seen = client_data.get("lastSeen", {})
                    connection_quality = self._calculate_connection_quality(last_seen)
                    
                    clients.append({
                        "id": client_data["id"],
                        "name": name,
                        "volume": client_data["config"]["volume"]["percent"],
                        "muted": client_data["config"]["volume"]["muted"],
                        "host": client_data["host"]["name"],
                        "ip": client_data["host"]["ip"].replace("::ffff:", ""),
                        "mac": client_data["host"]["mac"],
                        "latency": client_data["config"]["latency"],
                        "last_seen": last_seen,
                        "connection_quality": connection_quality,
                        "host_info": {
                            "arch": client_data["host"].get("arch", ""),
                            "os": client_data["host"].get("os", "")
                        },
                        "snapclient_info": client_data.get("snapclient", {}),
                        "group_id": group["id"]
                    })
            
            return clients
        except Exception as e:
            self.logger.error(f"Error getting detailed clients: {e}")
            return []
    
    async def is_available(self) -> bool:
        """Vérifie si Snapcast est disponible"""
        try:
            result = await self._request("Server.GetRPCVersion")
            return bool(result)
        except:
            return False
    
    def _calculate_connection_quality(self, last_seen: Dict[str, Any]) -> str:
        """Calcule la qualité de connexion basée sur lastSeen"""
        if not last_seen:
            return "unknown"
        
        sec = last_seen.get("sec", 0)
        if sec > 0:
            return "good"
        else:
            return "poor"
    
    # === MÉTHODES CONFIGURATION CORRIGÉES ===
    
    async def get_server_config(self) -> Dict[str, Any]:
        """Récupère la configuration CORRIGÉE avec parser custom"""
        try:
            # Lire les paramètres depuis l'API Snapcast
            status = await self._request("Server.GetStatus")
            server_info = status.get("server", {})
            streams = status.get("streams", [])
            
            # Extraire la config du premier stream
            stream_config = {}
            if streams:
                first_stream = streams[0]
                uri = first_stream.get("uri", {})
                query = uri.get("query", {})
                
                stream_config = {
                    "chunk_ms": query.get("chunk_ms", "20"),
                    "codec": query.get("codec", "flac"),
                    "sampleformat": query.get("sampleformat", "48000:16:2")
                }
            
            # Lire le fichier avec parser CUSTOM (gère les sources multiples)
            file_config = await self._read_snapserver_conf_custom()
            
            return {
                "server_info": server_info,
                "stream_config": stream_config,  # Config actuelle du serveur
                "file_config": file_config,      # Config dans le fichier
                "streams": streams,
                "rpc_version": await self._request("Server.GetRPCVersion")
            }
        except Exception as e:
            self.logger.error(f"Error getting server config: {e}")
            return {}
    
    async def _read_snapserver_conf_custom(self) -> Dict[str, Any]:
        """Parser CUSTOM pour snapserver.conf (gère les sources multiples)"""
        try:
            if not self.snapserver_conf.exists():
                return {}
            
            # Lire le fichier ligne par ligne
            async with aiofiles.open(self.snapserver_conf, 'r') as f:
                content = await f.read()
            
            lines = content.split('\n')
            config = {}
            current_section = None
            
            for line in lines:
                line = line.strip()
                
                # Ignorer les commentaires et lignes vides
                if not line or line.startswith('#'):
                    continue
                
                # Détecter les sections [section]
                if line.startswith('[') and line.endswith(']'):
                    current_section = line[1:-1]
                    if current_section not in config:
                        config[current_section] = {}
                    continue
                
                # Parser les clés=valeurs
                if '=' in line and current_section:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    # Gérer les sources multiples (liste)
                    if key == 'source':
                        if 'sources' not in config[current_section]:
                            config[current_section]['sources'] = []
                        config[current_section]['sources'].append(value)
                    else:
                        config[current_section][key] = value
            
            return {
                "parsed_config": config,
                "raw_content": content
            }
            
        except Exception as e:
            self.logger.error(f"Error reading snapserver.conf: {e}")
            return {}
    
    async def update_server_config(self, config: Dict[str, Any]) -> bool:
        """Met à jour la configuration serveur CORRIGÉ avec permissions"""
        try:
            # Validation simple
            if not self._validate_config(config):
                self.logger.error("Config validation failed")
                return False
            
            # S'assurer que sampleformat est toujours fixé à 48000:16:2
            config["sampleformat"] = "48000:16:2"
            
            # Modifier le fichier avec sudo
            success = await self._update_config_file_with_sudo(config)
            if not success:
                return False
            
            # Redémarrer snapserver
            restart_success = await self._restart_snapserver()
            return restart_success
            
        except Exception as e:
            self.logger.error(f"Error updating server config: {e}")
            return False
    
    def _validate_config(self, config: Dict[str, Any]) -> bool:
        """Validation simple des paramètres"""
        # Validation buffer
        if "buffer" in config:
            if not isinstance(config["buffer"], int) or not (100 <= config["buffer"] <= 2000):
                self.logger.error(f"Invalid buffer: {config['buffer']}")
                return False
        
        # Validation codec
        if "codec" in config:
            valid_codecs = ["flac", "pcm", "opus", "ogg"]
            if config["codec"] not in valid_codecs:
                self.logger.error(f"Invalid codec: {config['codec']}")
                return False
        
        # Validation chunk_ms
        if "chunk_ms" in config:
            if not isinstance(config["chunk_ms"], int) or not (10 <= config["chunk_ms"] <= 100):
                self.logger.error(f"Invalid chunk_ms: {config['chunk_ms']}")
                return False
        
        # Note: sampleformat est toujours fixé à 48000:16:2, pas de validation nécessaire
        
        return True
    
    async def _update_config_file_with_sudo(self, config: Dict[str, Any]) -> bool:
        """Met à jour le fichier avec sudo (contourne les permissions)"""
        try:
            if not self.snapserver_conf.exists():
                self.logger.error("snapserver.conf not found")
                return False
            
            # Lire le fichier actuel
            async with aiofiles.open(self.snapserver_conf, 'r') as f:
                content = await f.read()
            
            # Modifier le contenu
            updated_content = self._modify_config_content(content, config)
            
            # Écrire via un fichier temporaire + sudo mv
            temp_file = "/tmp/snapserver_temp.conf"
            
            # Écrire le contenu modifié dans /tmp
            async with aiofiles.open(temp_file, 'w') as f:
                await f.write(updated_content)
            
            # Utiliser sudo pour remplacer le fichier
            proc = await asyncio.create_subprocess_exec(
                "sudo", "mv", temp_file, str(self.snapserver_conf),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                self.logger.error(f"Failed to update config with sudo: {stderr.decode()}")
                return False
            
            self.logger.info("snapserver.conf updated successfully with sudo")
            return True
            
        except Exception as e:
            self.logger.error(f"Error updating config file: {e}")
            return False
    
    def _modify_config_content(self, content: str, config: Dict[str, Any]) -> str:
        """Modifie le contenu du fichier avec les nouveaux paramètres"""
        lines = content.split('\n')
        updated_lines = []
        in_stream_section = False
        
        for line in lines:
            original_line = line
            stripped_line = line.strip()
            
            # Détecter la section [stream]
            if stripped_line == "[stream]":
                in_stream_section = True
                updated_lines.append(original_line)
                continue
            elif stripped_line.startswith("[") and stripped_line != "[stream]":
                in_stream_section = False
            
            # Modifier les paramètres dans la section [stream]
            if in_stream_section and "=" in stripped_line and not stripped_line.startswith("#"):
                key = stripped_line.split("=")[0].strip()
                
                if key == "buffer" and "buffer" in config:
                    updated_lines.append(f"buffer = {config['buffer']}")
                elif key == "codec" and "codec" in config:
                    updated_lines.append(f"codec = {config['codec']}")
                elif key == "chunk_ms" and "chunk_ms" in config:
                    updated_lines.append(f"chunk_ms = {config['chunk_ms']}")
                elif key == "sampleformat":
                    # Toujours forcer sampleformat à 48000:16:2
                    updated_lines.append(f"sampleformat = 48000:16:2")
                else:
                    # Garder la ligne originale pour les autres paramètres
                    updated_lines.append(original_line)
            else:
                # Garder toutes les autres lignes inchangées
                updated_lines.append(original_line)
        
        return '\n'.join(updated_lines)
    
    async def _restart_snapserver(self) -> bool:
        """Redémarre le serveur Snapcast"""
        try:
            self.logger.info("Restarting snapserver...")
            
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", "restart", "oakos-snapserver-multiroom.service",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            
            _, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                self.logger.error(f"Failed to restart snapserver: {stderr.decode()}")
                return False
            
            # Attendre que le service soit prêt
            self.logger.info("Waiting for snapserver to be ready...")
            await asyncio.sleep(3)
            
            # Vérifier que le service est de nouveau disponible
            for i in range(10):  # Attendre jusqu'à 10 secondes
                if await self.is_available():
                    self.logger.info("Snapserver restarted successfully")
                    return True
                await asyncio.sleep(1)
            
            self.logger.warning("Snapserver restarted but API not available yet")
            return False
            
        except Exception as e:
            self.logger.error(f"Error restarting snapserver: {e}")
            return False
    
    async def get_simple_health(self) -> Dict[str, Any]:
        """Diagnostic de santé simple"""
        try:
            # Test de base
            api_available = await self.is_available()
            
            if not api_available:
                return {
                    "status": "error",
                    "message": "Snapcast API non disponible",
                    "details": {
                        "api": False,
                        "clients": 0,
                        "config_file": self.snapserver_conf.exists()
                    }
                }
            
            # Compter les clients
            clients = await self.get_clients()
            
            return {
                "status": "ok",
                "message": f"Snapcast opérationnel ({len(clients)} client(s))",
                "details": {
                    "api": True,
                    "clients": len(clients),
                    "config_file": self.snapserver_conf.exists()
                }
            }
            
        except Exception as e:
            return {
                "status": "error",
                "message": f"Erreur: {str(e)}",
                "details": {"error": str(e)}
            }