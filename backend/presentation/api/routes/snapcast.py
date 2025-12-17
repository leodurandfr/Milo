# backend/presentation/api/routes/snapcast.py
"""
API routes for Snapcast
"""
import time
import logging
from fastapi import APIRouter, HTTPException

from backend.presentation.api.models import (
    SnapcastVolumeRequest,
    SnapcastClientMuteRequest,
    SnapcastClientNameRequest,
    SnapcastServerConfigRequest
)

logger = logging.getLogger(__name__)

def create_snapcast_router(routing_service, snapcast_service, state_machine):
    """Creates Snapcast router"""
    router = APIRouter(prefix="/api/routing/snapcast", tags=["snapcast"])

    # === WEBSOCKET UTILITY FUNCTIONS ===

    async def _publish_snapcast_update():
        """Publishes Snapcast update notification via WebSocket"""
        try:
            await state_machine.broadcast_event("system", "state_changed", {
                "snapcast_update": True,
                "source": "snapcast"
            })
        except Exception as e:
            logger.error("Error publishing Snapcast update: %s", e)

    async def _broadcast_client_volume_changed(client_id: str, volume: int, muted: bool):
        """Broadcasts client volume change event"""
        try:
            await state_machine.broadcast_event("snapcast", "client_volume_changed", {
                "client_id": client_id,
                "volume": volume,
                "muted": muted,
                "source": "api"
            })
        except Exception as e:
            logger.error("Error broadcasting client volume changed: %s", e)

    async def _broadcast_client_mute_changed(client_id: str, volume: int, muted: bool):
        """Broadcasts client mute change event"""
        try:
            await state_machine.broadcast_event("snapcast", "client_mute_changed", {
                "client_id": client_id,
                "volume": volume,
                "muted": muted,
                "source": "api"
            })
        except Exception as e:
            logger.error("Error broadcasting client mute changed: %s", e)


    # === BASE ROUTES ===

    @router.get("/status")
    async def get_snapcast_status():
        """Snapcast status"""
        try:
            available = await snapcast_service.is_available()
            clients = await snapcast_service.get_clients() if available else []
            routing_state = routing_service.get_state()
            
            return {
                "available": available,
                "client_count": len(clients),
                "multiroom_active": routing_state.get('multiroom_enabled', False)
            }
        except Exception as e:
            return {"available": False, "error": str(e)}
    
    @router.get("/clients")
    async def get_snapcast_clients():
        """Gets Snapcast clients (volume always 100% passthrough, real volume via CamillaDSP)"""
        try:
            routing_state = routing_service.get_state()
            if not routing_state.get('multiroom_enabled', False):
                return {"clients": [], "message": "Multiroom not active"}

            clients = await snapcast_service.get_clients()
            return {"clients": clients}
        except Exception as e:
            return {"clients": [], "error": str(e)}

    @router.post("/client/{client_id}/volume")
    async def set_snapcast_volume(client_id: str, payload: SnapcastVolumeRequest):
        """Sets Snapcast client volume (deprecated: use CamillaDSP for volume control)"""
        try:
            # Snapcast volume should always be 100% (passthrough)
            # Real volume is controlled via CamillaDSP on each client
            volume = payload.volume
            success = await snapcast_service.set_volume(client_id, volume)

            if success:
                clients = await snapcast_service.get_clients()
                client = next((c for c in clients if c.get("id") == client_id), None)
                muted = client.get("muted", False) if client else False
                await _broadcast_client_volume_changed(client_id, volume, muted)

            return {"status": "success" if success else "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.post("/client/{client_id}/mute")
    async def set_snapcast_mute(client_id: str, payload: SnapcastClientMuteRequest):
        """Mutes/unmutes a client"""
        try:
            muted = payload.muted
            success = await snapcast_service.set_mute(client_id, muted)

            if success:
                clients = await snapcast_service.get_clients()
                client = next((c for c in clients if c.get("id") == client_id), None)
                volume = client.get("volume", 100) if client else 100
                await _broadcast_client_mute_changed(client_id, volume, muted)

            return {"status": "success" if success else "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.post("/client/{client_id}/name")
    async def set_client_name(client_id: str, payload: SnapcastClientNameRequest):
        """Sets client name"""
        try:
            success = await snapcast_service.set_client_name(client_id, payload.name)

            if success:
                await _publish_snapcast_update()

            return {"status": "success" if success else "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # === MONITORING ROUTES ===

    @router.get("/monitoring")
    async def get_snapcast_monitoring():
        """Gets Snapcast monitoring information"""
        try:
            routing_state = routing_service.get_state()
            if not routing_state.get('multiroom_enabled', False):
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
        """Gets server configuration"""
        try:
            available = await snapcast_service.is_available()
            if not available:
                return {"config": None, "error": "Snapcast server not available"}

            config = await snapcast_service.get_server_config()
            return {"config": config}
        except Exception as e:
            logger.error(f"Error getting server config: {e}")
            return {"config": None, "error": str(e)}

    # === SERVER CONFIGURATION ROUTES ===

    @router.post("/server/config")
    async def update_server_config(payload: SnapcastServerConfigRequest):
        """Updates server configuration"""
        try:
            success = await snapcast_service.update_server_config(payload.config)

            if success:
                await _publish_snapcast_update()
                return {
                    "status": "success",
                    "message": "Configuration updated and server restarted"
                }
            else:
                return {"status": "error", "message": "Update failed"}

        except Exception as e:
            return {"status": "error", "message": str(e)}

    return router