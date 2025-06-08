# backend/presentation/api/routes/routing.py
"""
Routes API pour la gestion du routage audio - Version étendue avec monitoring Snapcast complet
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from backend.domain.audio_routing import AudioRoutingMode
from backend.domain.audio_state import AudioSource
from backend.domain.events import StandardEvent, EventCategory, EventType

def create_routing_router(routing_service, state_machine, snapcast_service):
    """Crée le router de routage avec les dépendances injectées"""
    router = APIRouter(prefix="/api/routing", tags=["routing"])
    
    # === ROUTES EXISTANTES (conservées) ===
    
    @router.get("/status")
    async def get_routing_status():
        """Récupère l'état actuel du routage"""
        routing_state = routing_service.get_state()
        snapcast_status = await routing_service.get_snapcast_status()
        
        return {
            "routing": routing_state.to_dict(),
            "snapcast": snapcast_status
        }
    
    @router.get("/services")
    async def get_services_status():
        """Récupère l'état de tous les services"""
        services_status = await routing_service.get_available_services()
        return {
            "services": services_status
        }
    
    @router.post("/mode/{mode}")
    async def set_routing_mode(mode: str):
        """Change le mode de routage"""
        try:
            routing_mode = AudioRoutingMode(mode)
            
            # Récupérer le plugin actuellement actif
            current_state = await state_machine.get_current_state()
            active_source = None
            
            if current_state["active_source"] != "none":
                try:
                    active_source = AudioSource(current_state["active_source"])
                except ValueError:
                    pass
            
            # Changer le mode de routage avec le plugin actif
            success = await routing_service.set_routing_mode(routing_mode, active_source)
            if not success:
                return {"status": "error", "message": "Failed to change routing mode"}
            
            # Mettre à jour l'état de la machine à états
            await state_machine.update_routing_mode(mode)
            
            return {
                "status": "success", 
                "mode": mode,
                "active_source": current_state["active_source"] if active_source else "none"
            }
        except ValueError:
            return {"status": "error", "message": f"Invalid mode: {mode}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    # === FONCTION UTILITAIRE WEBSOCKET ===
    
    async def _publish_snapcast_update():
        """Publie une notification de mise à jour Snapcast via le WebSocket oakOS"""
        try:
            event_bus = state_machine.event_bus
            
            event = StandardEvent(
                category=EventCategory.SYSTEM,
                type=EventType.STATE_CHANGED,
                source="snapcast",
                data={
                    "snapcast_update": True,
                    "timestamp": __import__('time').time()
                }
            )
            
            await event_bus.publish_event(event)
        except Exception as e:
            print(f"Error publishing Snapcast update: {e}")
    
    # === ROUTES SNAPCAST EXISTANTES (conservées) ===
    
    @router.get("/snapcast/status")
    async def get_snapcast_status():
        """État de Snapcast"""
        try:
            available = await snapcast_service.is_available()
            clients = await snapcast_service.get_clients() if available else []
            routing_state = routing_service.get_state()
            
            return {
                "available": available,
                "client_count": len(clients),
                "multiroom_active": routing_state.mode.value == "multiroom"
            }
        except Exception as e:
            return {"available": False, "error": str(e)}
    
    @router.get("/snapcast/clients")
    async def get_snapcast_clients():
        """Récupère les clients Snapcast"""
        try:
            routing_state = routing_service.get_state()
            if routing_state.mode.value != "multiroom":
                return {"clients": [], "message": "Multiroom not active"}
            
            clients = await snapcast_service.get_clients()
            return {"clients": clients}
        except Exception as e:
            return {"clients": [], "error": str(e)}
    
    @router.post("/snapcast/client/{client_id}/volume")
    async def set_snapcast_volume(client_id: str, payload: Dict[str, Any]):
        """Change le volume d'un client et notifie les autres devices"""
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
    
    @router.post("/snapcast/client/{client_id}/mute")
    async def set_snapcast_mute(client_id: str, payload: Dict[str, Any]):
        """Mute/unmute un client et notifie les autres devices"""
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
    
    # === NOUVELLES ROUTES POUR MONITORING (Phase 1) ===
    
    @router.get("/snapcast/monitoring")
    async def get_snapcast_monitoring():
        """Récupère les informations détaillées de monitoring Snapcast"""
        try:
            routing_state = routing_service.get_state()
            if routing_state.mode.value != "multiroom":
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
            
            # Récupérer les informations détaillées
            clients = await snapcast_service.get_detailed_clients()
            server_config = await snapcast_service.get_server_config()
            
            return {
                "available": True,
                "clients": clients,
                "server_config": server_config,
                "timestamp": __import__('time').time()
            }
        except Exception as e:
            return {
                "available": False, 
                "error": str(e),
                "clients": [],
                "server_config": {}
            }
    
    @router.get("/snapcast/server-config")
    async def get_snapcast_server_config():
        """Récupère la configuration du serveur Snapcast"""
        try:
            available = await snapcast_service.is_available()
            if not available:
                raise HTTPException(status_code=503, detail="Snapcast server not available")
            
            config = await snapcast_service.get_server_config()
            return {"config": config}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/snapcast/latencies")
    async def get_client_latencies():
        """Récupère les latences de tous les clients"""
        try:
            available = await snapcast_service.is_available()
            if not available:
                return {"latencies": {}, "message": "Snapcast server not available"}
            
            latencies = await snapcast_service.get_client_latencies()
            return {"latencies": latencies}
        except Exception as e:
            return {"latencies": {}, "error": str(e)}
    
    # === NOUVELLES ROUTES POUR CONFIGURATION CLIENT (Phase 2) ===
    
    @router.post("/snapcast/client/{client_id}/latency")
    async def set_client_latency(client_id: str, payload: Dict[str, Any]):
        """Configure la latence d'un client et notifie les autres devices"""
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
    
    @router.post("/snapcast/client/{client_id}/name")
    async def set_client_name(client_id: str, payload: Dict[str, Any]):
        """Configure le nom d'un client et notifie les autres devices"""
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
    
    @router.get("/snapcast/client/{client_id}/details")
    async def get_client_details(client_id: str):
        """Récupère les détails complets d'un client spécifique"""
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
    
    # === ROUTES POUR CONFIGURATION SERVEUR (Phase 3) ===
    
    @router.post("/snapcast/server/config")
    async def update_server_config(payload: Dict[str, Any]):
        """Met à jour la configuration du serveur (nécessite redémarrage)"""
        try:
            # Cette implémentation sera complétée en Phase 3
            config = payload.get("config", {})
            
            # Validation basique des paramètres
            valid_codecs = ["flac", "pcm", "opus", "ogg"]
            if "codec" in config and config["codec"] not in valid_codecs:
                return {"status": "error", "message": f"Invalid codec. Must be one of: {valid_codecs}"}
            
            if "buffer_ms" in config:
                buffer_ms = config["buffer_ms"]
                if not isinstance(buffer_ms, int) or not (100 <= buffer_ms <= 2000):
                    return {"status": "error", "message": "Invalid buffer_ms (100-2000)"}
            
            if "chunk_ms" in config:
                chunk_ms = config["chunk_ms"]
                if not isinstance(chunk_ms, int) or not (10 <= chunk_ms <= 100):
                    return {"status": "error", "message": "Invalid chunk_ms (10-100)"}
            
            # Pour l'instant, on simule le succès (implémentation complète en Phase 3)
            success = await snapcast_service.update_server_config(config)
            
            return {
                "status": "success" if success else "error",
                "message": "Server config update not fully implemented yet (Phase 3)" if not success else "Config updated"
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    return router