# backend/infrastructure/services/snapcast_service.py
"""
Service Snapcast OPTIMISÉ - Réduction redondances + gestion erreurs améliorée
"""
import aiohttp
import asyncio
import aiofiles
import logging
import re
from typing import List, Dict, Any, Optional
from pathlib import Path

class SnapcastService:
    """Service Snapcast optimisé avec réduction des redondances"""
    
    def __init__(self, host: str = "localhost", port: int = 1780):
        self.base_url = f"http://{host}:{port}/jsonrpc"
        self.logger = logging.getLogger(__name__)
        self._request_id = 0
        self.snapserver_conf = Path("/etc/snapserver.conf")
        
        # Cache pour éviter des requêtes répétées
        self._client_cache = {}
        self._cache_timestamp = 0
        self._cache_ttl = 1  # 1 seconde de cache
    
    async def _request(self, method: str, params: dict = None) -> dict:
        """Requête JSON-RPC optimisée vers Snapcast"""
        self._request_id += 1
        request = {"id": self._request_id, "jsonrpc": "2.0", "method": method}
        if params:
            request["params"] = params
        
        try:
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.base_url, json=request) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("result", {})
            return {}
        except Exception as e:
            self.logger.error(f"Snapcast request failed: {e}")
            return {}
    
    # === MÉTHODES CLIENT OPTIMISÉES ===
    
    async def get_clients(self) -> List[Dict[str, Any]]:
        """Récupère les clients avec cache intelligent"""
        current_time = asyncio.get_event_loop().time()
        
        # Utiliser le cache si disponible et récent
        if (self._client_cache and 
            current_time - self._cache_timestamp < self._cache_ttl):
            return self._client_cache
        
        try:
            status = await self._request("Server.GetStatus")
            clients = self._extract_clients(status)
            
            # Mettre à jour le cache
            self._client_cache = clients
            self._cache_timestamp = current_time
            
            return clients
            
        except Exception as e:
            self.logger.error(f"Error getting clients: {e}")
            return []
    
    def _extract_clients(self, status: dict) -> List[Dict[str, Any]]:
        """Extrait et filtre les clients du statut serveur"""
        clients = []
        exclude_names = {'snapweb client', 'snapweb'}
        
        for group in status.get("server", {}).get("groups", []):
            for client_data in group.get("clients", []):
                if not client_data.get("connected"):
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
    
    async def get_detailed_clients(self) -> List[Dict[str, Any]]:
        """Récupère les clients avec informations détaillées"""
        try:
            status = await self._request("Server.GetStatus")
            clients = []
            exclude_names = {'snapweb client', 'snapweb'}
            
            for group in status.get("server", {}).get("groups", []):
                for client_data in group.get("clients", []):
                    if not client_data.get("connected"):
                        continue
                    
                    name = client_data["config"]["name"] or client_data["host"]["name"]
                    if any(exclude in name.lower() for exclude in exclude_names):
                        continue
                    
                    last_seen = client_data.get("lastSeen", {})
                    
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
                        "connection_quality": self._calculate_connection_quality(last_seen),
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
    
    # === MÉTHODES CONTRÔLE CLIENT OPTIMISÉES ===
    
    async def set_volume(self, client_id: str, volume: int) -> bool:
        """Change le volume d'un client - Version optimisée"""
        try:
            # Invalider le cache
            self._client_cache = {}
            
            # Récupérer l'état mute actuel depuis le cache ou API
            current_muted = False
            if self._client_cache:
                for client in self._client_cache:
                    if client["id"] == client_id:
                        current_muted = client["muted"]
                        break
            
            result = await self._request("Client.SetVolume", {
                "id": client_id,
                "volume": {"percent": max(0, min(100, volume)), "muted": current_muted}
            })
            return bool(result)
            
        except Exception as e:
            self.logger.error(f"Error setting volume: {e}")
            return False
    
    async def set_mute(self, client_id: str, muted: bool) -> bool:
        """Mute/unmute un client - Version optimisée FIXÉE"""
        try:
            # Récupérer le volume actuel AVANT d'invalider le cache
            current_volume = 50  # Valeur par défaut
            
            # Essayer de récupérer depuis le cache actuel
            if self._client_cache:
                for client in self._client_cache:
                    if client["id"] == client_id:
                        current_volume = client["volume"]
                        break
            else:
                # Si pas de cache, récupérer via API
                clients = await self.get_clients()
                for client in clients:
                    if client["id"] == client_id:
                        current_volume = client["volume"]
                        break
            
            # Maintenant invalider le cache pour la prochaine fois
            self._client_cache = {}
            
            result = await self._request("Client.SetVolume", {
                "id": client_id,
                "volume": {"percent": current_volume, "muted": muted}
            })
            return bool(result)
            
        except Exception as e:
            self.logger.error(f"Error setting mute: {e}")
            return False
    
    async def set_client_latency(self, client_id: str, latency: int) -> bool:
        """Configure la latence d'un client"""
        try:
            self._client_cache = {}  # Invalider le cache
            
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
            self._client_cache = {}  # Invalider le cache
            
            result = await self._request("Client.SetName", {
                "id": client_id,
                "name": name.strip()
            })
            return bool(result)
            
        except Exception as e:
            self.logger.error(f"Error setting client name: {e}")
            return False
    
    # === MÉTHODES UTILITAIRES ===
    
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
        return "good" if sec > 0 else "poor"
    
    # === CONFIGURATION SERVEUR OPTIMISÉE ===
    
    async def get_server_config(self) -> Dict[str, Any]:
        """Récupère la configuration avec parser optimisé"""
        try:
            # Récupérer les infos API en parallèle avec la lecture fichier
            api_task = self._request("Server.GetStatus")
            file_task = self._read_snapserver_conf_optimized()
            
            status, file_config = await asyncio.gather(api_task, file_task)
            
            # Traiter les données API
            server_info = status.get("server", {})
            streams = status.get("streams", [])
            
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
            
            return {
                "server_info": server_info,
                "stream_config": stream_config,
                "file_config": file_config,
                "streams": streams,
                "rpc_version": await self._request("Server.GetRPCVersion")
            }
            
        except Exception as e:
            self.logger.error(f"Error getting server config: {e}")
            return {}
    
    async def _read_snapserver_conf_optimized(self) -> Dict[str, Any]:
        """Parser optimisé pour snapserver.conf"""
        try:
            if not self.snapserver_conf.exists():
                return {}
            
            async with aiofiles.open(self.snapserver_conf, 'r') as f:
                content = await f.read()
            
            config = {}
            current_section = None
            
            for line in content.split('\n'):
                line = line.strip()
                
                if not line or line.startswith('#'):
                    continue
                
                if line.startswith('[') and line.endswith(']'):
                    current_section = line[1:-1]
                    config.setdefault(current_section, {})
                    continue
                
                if '=' in line and current_section:
                    key, value = line.split('=', 1)
                    key, value = key.strip(), value.strip()
                    
                    if key == 'source':
                        config[current_section].setdefault('sources', []).append(value)
                    else:
                        config[current_section][key] = value
            
            return {"parsed_config": config, "raw_content": content}
            
        except Exception as e:
            self.logger.error(f"Error reading snapserver.conf: {e}")
            return {}
    
    async def update_server_config(self, config: Dict[str, Any]) -> bool:
        """Met à jour la configuration serveur avec validation optimisée"""
        try:
            if not self._validate_config_optimized(config):
                return False
            
            # Forcer sampleformat
            config["sampleformat"] = "48000:16:2"
            
            success = await self._update_config_file_optimized(config)
            if not success:
                return False
            
            return await self._restart_snapserver()
            
        except Exception as e:
            self.logger.error(f"Error updating server config: {e}")
            return False
    
    def _validate_config_optimized(self, config: Dict[str, Any]) -> bool:
        """Validation optimisée des paramètres"""
        validators = {
            "buffer": lambda x: isinstance(x, int) and 100 <= x <= 2000,
            "codec": lambda x: x in ["flac", "pcm", "opus", "ogg"],
            "chunk_ms": lambda x: isinstance(x, int) and 10 <= x <= 100
        }
        
        for key, validator in validators.items():
            if key in config and not validator(config[key]):
                self.logger.error(f"Invalid {key}: {config[key]}")
                return False
        
        return True
    
    async def _update_config_file_optimized(self, config: Dict[str, Any]) -> bool:
        """Met à jour le fichier avec optimisation"""
        try:
            if not self.snapserver_conf.exists():
                self.logger.error("snapserver.conf not found")
                return False
            
            async with aiofiles.open(self.snapserver_conf, 'r') as f:
                content = await f.read()
            
            updated_content = self._modify_config_content_optimized(content, config)
            
            # Écriture atomique via fichier temporaire
            temp_file = "/tmp/snapserver_temp.conf"
            async with aiofiles.open(temp_file, 'w') as f:
                await f.write(updated_content)
            
            # Remplacement avec sudo
            proc = await asyncio.create_subprocess_exec(
                "sudo", "mv", temp_file, str(self.snapserver_conf),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            _, stderr = await proc.communicate()
            
            if proc.returncode == 0:
                self.logger.info("snapserver.conf updated successfully")
                return True
            else:
                self.logger.error(f"Failed to update config: {stderr.decode()}")
                return False
            
        except Exception as e:
            self.logger.error(f"Error updating config file: {e}")
            return False
    
    def _modify_config_content_optimized(self, content: str, config: Dict[str, Any]) -> str:
        """Modification optimisée du contenu fichier"""
        lines = content.split('\n')
        updated_lines = []
        in_stream_section = False
        
        # Mappage des paramètres à modifier
        param_mapping = {
            "buffer": "buffer",
            "codec": "codec", 
            "chunk_ms": "chunk_ms",
            "sampleformat": "sampleformat"
        }
        
        for line in lines:
            stripped_line = line.strip()
            
            if stripped_line == "[stream]":
                in_stream_section = True
                updated_lines.append(line)
                continue
            elif stripped_line.startswith("[") and stripped_line != "[stream]":
                in_stream_section = False
            
            if (in_stream_section and "=" in stripped_line and 
                not stripped_line.startswith("#")):
                
                key = stripped_line.split("=")[0].strip()
                
                if key in param_mapping and param_mapping[key] in config:
                    param_key = param_mapping[key]
                    if param_key == "sampleformat":
                        updated_lines.append(f"sampleformat = 48000:16:2")
                    else:
                        updated_lines.append(f"{key} = {config[param_key]}")
                else:
                    updated_lines.append(line)
            else:
                updated_lines.append(line)
        
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
            
            # Vérifier la disponibilité
            await asyncio.sleep(3)
            for _ in range(10):
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
        """Diagnostic de santé optimisé"""
        try:
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