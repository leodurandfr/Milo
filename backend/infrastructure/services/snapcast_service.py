# backend/infrastructure/services/snapcast_service.py
"""
Service Snapcast minimal pour oakOS - Intégration cohérente
"""
import aiohttp
import logging
from typing import List, Dict, Any

class SnapcastService:
    """Service minimal pour contrôler Snapcast via JSON-RPC"""
    
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
            # Préserver l'état mute actuel
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
            # Préserver le volume actuel
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
        """Vérifie si Snapcast est disponible"""
        try:
            result = await self._request("Server.GetRPCVersion")
            return bool(result)
        except:
            return False