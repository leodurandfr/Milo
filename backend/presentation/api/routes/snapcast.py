# backend/presentation/api/routes/snapcast.py
"""
API routes for Snapcast
"""
import time
import logging
from fastapi import APIRouter, HTTPException
from typing import Dict, Any

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

    def _get_volume_service():
        """Gets VolumeService from state_machine"""
        if hasattr(state_machine, 'volume_service') and state_machine.volume_service:
            return state_machine.volume_service
        return None

    def _convert_client_volumes_to_display(clients: list) -> list:
        """Converts client volumes from ALSA to display format"""
        volume_service = _get_volume_service()
        if not volume_service:
            return clients

        converted_clients = []
        for client in clients:
            converted_client = client.copy()
            alsa_volume = client.get("volume", 0)
            display_volume = volume_service.convert_alsa_to_display(alsa_volume)
            converted_client["volume"] = display_volume
            converted_client["volume_alsa"] = alsa_volume
            converted_clients.append(converted_client)

        return converted_clients

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
        """Gets Snapcast clients with converted volumes"""
        try:
            routing_state = routing_service.get_state()
            if not routing_state.get('multiroom_enabled', False):
                return {"clients": [], "message": "Multiroom not active"}

            alsa_clients = await snapcast_service.get_clients()
            display_clients = _convert_client_volumes_to_display(alsa_clients)

            return {"clients": display_clients}
        except Exception as e:
            return {"clients": [], "error": str(e)}

    @router.post("/client/{client_id}/volume")
    async def set_snapcast_volume(client_id: str, payload: Dict[str, Any]):
        """Changes client volume"""
        try:
            display_volume = payload.get("volume")
            if not isinstance(display_volume, int) or not (0 <= display_volume <= 100):
                return {"status": "error", "message": "Invalid volume (0-100%)"}

            volume_service = _get_volume_service()
            if volume_service:
                alsa_volume = volume_service.convert_display_to_alsa(display_volume)
                success = await snapcast_service.set_volume(client_id, alsa_volume)
            else:
                success = await snapcast_service.set_volume(client_id, display_volume)

            if success:
                if volume_service:
                    volume_service.update_client_display_volume(client_id, display_volume)

                clients = await snapcast_service.get_clients()
                client = next((c for c in clients if c.get("id") == client_id), None)
                muted = client.get("muted", False) if client else False

                await _broadcast_client_volume_changed(client_id, display_volume, muted)

            return {"status": "success" if success else "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.post("/client/{client_id}/mute")
    async def set_snapcast_mute(client_id: str, payload: Dict[str, Any]):
        """Mutes/unmutes a client"""
        try:
            muted = payload.get("muted")
            if not isinstance(muted, bool):
                return {"status": "error", "message": "Invalid muted state (true/false)"}

            success = await snapcast_service.set_mute(client_id, muted)

            if success:
                volume_service = _get_volume_service()
                clients = await snapcast_service.get_clients()
                client = next((c for c in clients if c.get("id") == client_id), None)

                if client and volume_service:
                    alsa_volume = client.get("volume", 0)
                    display_volume = volume_service.convert_alsa_to_display(alsa_volume)
                    volume_service.update_client_display_volume(client_id, display_volume)
                else:
                    display_volume = client.get("volume", 0) if client else 0

                await _broadcast_client_mute_changed(client_id, display_volume, muted)

            return {"status": "success" if success else "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.post("/client/{client_id}/name")
    async def set_client_name(client_id: str, payload: Dict[str, Any]):
        """Sets client name"""
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
            
            alsa_clients = await snapcast_service.get_detailed_clients()
            server_config = await snapcast_service.get_server_config()
            display_clients = _convert_client_volumes_to_display(alsa_clients)
            
            return {
                "available": True,
                "clients": display_clients,
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
                raise HTTPException(status_code=503, detail="Snapcast server not available")

            config = await snapcast_service.get_server_config()
            return {"config": config}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # === SERVER CONFIGURATION ROUTES ===

    @router.post("/server/config")
    async def update_server_config(payload: Dict[str, Any]):
        """Updates server configuration"""
        try:
            config = payload.get("config", {})
            success = await snapcast_service.update_server_config(config)

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