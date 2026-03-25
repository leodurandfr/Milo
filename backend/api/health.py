# backend/api/health.py
"""
Health check and initial state endpoints
"""
import time
from fastapi import APIRouter
from typing import Dict, Any

def create_health_router(state_machine, routing_service, snapcast_service,
                         settings_service, wifi_service):
    """Creates health check router"""
    router = APIRouter(prefix="/api", tags=["health"])

    @router.get("/health")
    async def health_check() -> Dict[str, Any]:
        """Simple health endpoint for monitoring"""
        checks = {
            "status": "healthy",
            "timestamp": time.time(),
            "services": {}
        }

        try:
            state = await state_machine.get_current_state()
            checks["services"]["state_machine"] = {
                "healthy": True,
                "active_source": state.get("active_source"),
                "transitioning": state.get("transitioning", False)
            }
        except Exception as e:
            checks["services"]["state_machine"] = {
                "healthy": False,
                "error": str(e)
            }
            checks["status"] = "unhealthy"

        try:
            routing_state = routing_service.get_state()
            checks["services"]["routing"] = {
                "healthy": True,
                "multiroom_enabled": routing_state.get('multiroom_enabled', False)
            }
        except Exception as e:
            checks["services"]["routing"] = {
                "healthy": False,
                "error": str(e)
            }
            checks["status"] = "unhealthy"

        try:
            if routing_state.get('multiroom_enabled', False):
                snapcast_status = await routing_service.get_snapcast_status()
                checks["services"]["snapcast"] = {
                    "healthy": snapcast_status.get("multiroom_available", False),
                    "server_active": snapcast_status.get("server_active", False),
                    "client_active": snapcast_status.get("client_active", False)
                }

                if not snapcast_status.get("multiroom_available", False):
                    checks["status"] = "degraded"
            else:
                checks["services"]["snapcast"] = {
                    "healthy": True,
                    "note": "multiroom disabled"
                }
        except Exception as e:
            checks["services"]["snapcast"] = {
                "healthy": False,
                "error": str(e)
            }
            checks["status"] = "degraded"
        source_status = {}
        for source, instance in state_machine.sources.items():
            if instance:
                try:
                    source_status[source.value] = {
                        "registered": True,
                        "initialized": getattr(instance, '_initialized', False)
                    }
                except Exception as e:
                    source_status[source.value] = {
                        "registered": True,
                        "error": str(e)
                    }

        checks["services"]["sources"] = source_status

        return checks

    @router.get("/ping")
    async def ping() -> Dict[str, str]:
        """Simple endpoint to verify API is responding"""
        return {"status": "success", "message": "pong"}

    @router.get("/initial-state")
    async def get_initial_state() -> Dict[str, Any]:
        """HTTP fallback for initial state (captive portal compatibility).

        Returns the same data as the WebSocket initial_state event,
        for browsers that don't support WebSocket (e.g., macOS captive portal).
        """
        current_state = await state_machine.get_current_state()

        setup_completed = bool(await settings_service.get_setting("setup_completed"))
        hotspot_active = wifi_service.hotspot_active

        return {
            "status": "success",
            "full_state": current_state,
            "setup_completed": setup_completed,
            "hotspot_active": hotspot_active,
        }

    return router
