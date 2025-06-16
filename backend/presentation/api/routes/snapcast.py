# backend/presentation/api/routes/snapcast.py
"""
Routes API pour Snapcast - Version refactorisée avec multiroom_enabled
"""
import time
from fastapi import APIRouter, HTTPException
from typing import Dict, Any

def create_snapcast_router(routing_service, snapcast_service, state_machine):
    """Crée le router Snapcast - Version refactorisée"""
    router = APIRouter(prefix="/api/routing/snapcast", tags=["snapcast"])
    
    # === FONCTION UTILITAIRE WEBSOCKET ===
    
    async def _publish_snapcast_update():
        """Publie une notification de mise à jour Snapcast via WebSocket oakOS"""
        try:
            await state_machine.broadcast_event("system", "state_changed", {
                "snapcast_update": True,
                "source": "snapcast"
            })
        except Exception as e:
            print(f"Error publishing Snapcast update: {e}")
    
    # === ROUTES DE BASE ===
    
    @router.get("/status")
    async def get_snapcast_status():
        """État de Snapcast - Version refactorisée"""
        try:
            available = await snapcast_service.is_available()
            clients = await snapcast_service.get_clients() if available else []
            routing_state = routing_service.get_state()
            
            return {
                "available": available,
                "client_count": len(clients),
                "multiroom_active": routing_state.multiroom_enabled  # Refactorisé
            }
        except Exception as e:
            return {"available": False, "error": str(e)}
    
    @router.get("/clients")
    async def get_snapcast_clients():
        """Récupère les clients Snapcast - Version refactorisée"""
        try:
            routing_state = routing_service.get_state()
            if not routing_state.multiroom_enabled:  # Refactorisé
                return {"clients": [], "message": "Multiroom not active"}
            
            clients = await snapcast_service.get_clients()
            return {"clients": clients}
        except Exception as e:
            return {"clients": [], "error": str(e)}
    
    @router.post("/client/{client_id}/volume")
    async def set_snapcast_volume(client_id: str, payload: Dict[str, Any]):
        """Change le volume d'un client"""
        try:
            volume = payload.get("volume")
            if not isinstance(volume, int) or not (0 <= volume <= 100):
                return {"status": "error", "message": "Invalid volume (0-100)"}
            
            success = await snapcast_service.set_volume(client_id, volume)
            
            if success:
                await _publish_snapcast_update()
            
            return {"status": "success" if success else "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    @router.post("/client/{client_id}/mute")
    async def set_snapcast_mute(client_id: str, payload: Dict[str, Any]):
        """Mute/unmute un client"""
        try:
            muted = payload.get("muted")
            if not isinstance(muted, bool):
                return {"status": "error", "message": "Invalid muted state (true/false)"}
            
            success = await snapcast_service.set_mute(client_id, muted)
            
            if success:
                await _publish_snapcast_update()
            
            return {"status": "success" if success else "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    # === ROUTES MONITORING ===
    
    @router.get("/monitoring")
    async def get_snapcast_monitoring():
        """Récupère les informations de monitoring Snapcast - Version refactorisée"""
        try:
            routing_state = routing_service.get_state()
            if not routing_state.multiroom_enabled:  # Refactorisé
                return {
                    "available": False, 
                    "message": "Multiroom not active",
                    "clients": [],
                    "server_config": {}
                }
            
            available = await snapcast_service.is_available()
            if not available:
                return {
                    "available": False,
                    "message": "Snapcast server not available",
                    "clients": [],
                    "server_config": {}
                }
            
            # Récupérer les informations
            clients = await snapcast_service.get_detailed_clients()
            server_config = await snapcast_service.get_server_config()
            
            return {
                "available": True,
                "clients": clients,
                "server_config": server_config,
                "timestamp": time.time()
            }
        except Exception as e:
            return {
                "available": False, 
                "error": str(e),
                "clients": [],
                "server_config": {}
            }
    
    @router.get("/server-config")
    async def get_snapcast_server_config():
        """Récupère la configuration du serveur (depuis /etc/snapserver.conf)"""
        try:
            available = await snapcast_service.is_available()
            if not available:
                raise HTTPException(status_code=503, detail="Snapcast server not available")
            
            config = await snapcast_service.get_server_config()
            return {"config": config}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    # === ROUTES CONFIGURATION CLIENT ===
    
    @router.post("/client/{client_id}/latency")
    async def set_client_latency(client_id: str, payload: Dict[str, Any]):
        """Configure la latence d'un client"""
        try:
            latency = payload.get("latency")
            if not isinstance(latency, int) or not (0 <= latency <= 1000):
                return {"status": "error", "message": "Invalid latency (0-1000ms)"}
            
            success = await snapcast_service.set_client_latency(client_id, latency)
            
            if success:
                await _publish_snapcast_update()
            
            return {"status": "success" if success else "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    @router.post("/client/{client_id}/name")
    async def set_client_name(client_id: str, payload: Dict[str, Any]):
        """Configure le nom d'un client"""
        try:
            name = payload.get("name")
            if not isinstance(name, str) or not name.strip():
                return {"status": "error", "message": "Invalid name"}
            
            success = await snapcast_service.set_client_name(client_id, name.strip())
            
            if success:
                await _publish_snapcast_update()
            
            return {"status": "success" if success else "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    @router.get("/client/{client_id}/details")
    async def get_client_details(client_id: str):
        """Récupère les détails d'un client spécifique"""
        try:
            clients = await snapcast_service.get_detailed_clients()
            client = next((c for c in clients if c["id"] == client_id), None)
            
            if not client:
                raise HTTPException(status_code=404, detail="Client not found")
            
            return {"client": client}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    # === ROUTES CONFIGURATION SERVEUR ===
    
    @router.post("/server/config")
    async def update_server_config(payload: Dict[str, Any]):
        """Met à jour la configuration serveur (modifie /etc/snapserver.conf)"""
        try:
            config = payload.get("config", {})
            
            # Appliquer la configuration (validation incluse)
            success = await snapcast_service.update_server_config(config)
            
            if success:
                await _publish_snapcast_update()
                return {
                    "status": "success",
                    "message": "Configuration mise à jour et serveur redémarré"
                }
            else:
                return {"status": "error", "message": "Échec de la mise à jour"}
                
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    @router.get("/health")
    async def get_snapcast_health():
        """Diagnostic de santé simple"""
        try:
            health = await snapcast_service.get_simple_health()
            return health
        except Exception as e:
            return {
                "status": "error",
                "message": f"Erreur diagnostic: {str(e)}"
            }
    
    return router