# backend/infrastructure/services/snapcast_service.py
"""
Service Snapcast étendu avec monitoring et configuration avancée pour oakOS
"""
import aiohttp
import logging
from typing import List, Dict, Any, Optional

class SnapcastService:
    """Service étendu pour contrôler et monitorer Snapcast via JSON-RPC"""
    
    def __init__(self, host: str = "localhost", port: int = 1780):
        self.base_url = f"http://{host}:{port}/jsonrpc"
        self.logger = logging.getLogger(__name__)
        self._request_id = 0
    
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
    
    # === MÉTHODES EXISTANTES (conservées) ===
    
    async def get_clients(self) -> List[Dict[str, Any]]:
        """Récupère les clients connectés (filtrés) - Version existante"""
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
        """Change le volume d'un client - Version existante"""
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
        """Mute/unmute un client - Version existante"""
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
    
    async def is_available(self) -> bool:
        """Vérifie si Snapcast est disponible - Version existante"""
        try:
            result = await self._request("Server.GetRPCVersion")
            return bool(result)
        except:
            return False
    
    # === NOUVELLES MÉTHODES POUR MONITORING ===
    
    async def get_detailed_clients(self) -> List[Dict[str, Any]]:
        """Récupère les clients avec informations détaillées pour monitoring"""
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
                    
                    # Calcul de la qualité de connexion basée sur lastSeen
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
                        
                        # Informations de monitoring
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
    
    async def get_server_config(self) -> Dict[str, Any]:
        """Récupère la configuration du serveur Snapcast"""
        try:
            status = await self._request("Server.GetStatus")
            server_info = status.get("server", {})
            
            # Extraire les informations des streams pour obtenir les paramètres
            streams = status.get("streams", [])
            stream_config = {}
            
            if streams:
                # Prendre la configuration du premier stream comme référence
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
                "streams": streams,
                "rpc_version": await self._request("Server.GetRPCVersion")
            }
        except Exception as e:
            self.logger.error(f"Error getting server config: {e}")
            return {}
    
    async def get_client_latencies(self) -> Dict[str, int]:
        """Récupère les latences configurées pour tous les clients"""
        try:
            clients = await self.get_detailed_clients()
            return {client["id"]: client["latency"] for client in clients}
        except Exception as e:
            self.logger.error(f"Error getting client latencies: {e}")
            return {}
    
    async def set_client_latency(self, client_id: str, latency: int) -> bool:
        """Configure la latence d'un client"""
        try:
            result = await self._request("Client.SetLatency", {
                "id": client_id,
                "latency": max(0, min(1000, latency))  # Limiter entre 0-1000ms
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
    
    def _calculate_connection_quality(self, last_seen: Dict[str, Any]) -> str:
        """Calcule la qualité de connexion basée sur lastSeen"""
        if not last_seen:
            return "unknown"
        
        # Pour l'instant, on considère que si on a un lastSeen récent, c'est bon
        # Dans une version future, on pourrait comparer avec l'heure actuelle
        sec = last_seen.get("sec", 0)
        usec = last_seen.get("usec", 0)
        
        if sec > 0:
            return "good"  # Client récemment vu
        else:
            return "poor"  # Pas d'informations récentes
    
    # === MÉTHODES POUR CONFIGURATION AVANCÉE (Phase 3) ===
    
    async def update_server_config(self, config: Dict[str, Any]) -> bool:
        """Met à jour la configuration du serveur (nécessite redémarrage)"""
        # Cette méthode sera implémentée en Phase 3
        # Elle devra modifier le fichier snapserver.conf et redémarrer le service
        self.logger.info("Server config update not implemented yet (Phase 3)")
        return False