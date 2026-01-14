# backend/features/mac/routes.py
"""
FastAPI routes for Mac audio source.

Provides REST API endpoints for:
- Status: Get current Mac source status
- Restart: Restart the ROC service
- Connections: List connected Mac clients

Usage:
    from backend.features.mac import router, MacSource

    source = MacSource(event_bus, config)
    setup_mac_routes(lambda: source)
    app.include_router(router, prefix="/api")
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, Callable, Optional

from backend.features.mac.source import MacSource

router = APIRouter(
    prefix="/mac",
    tags=["mac"],
    responses={404: {"description": "Not found"}},
)

# Source provider function
_source_provider: Optional[Callable[[], MacSource]] = None


def setup_mac_routes(source_provider: Callable[[], MacSource]) -> APIRouter:
    """
    Configure routes with source provider.

    Args:
        source_provider: Function returning MacSource instance

    Returns:
        Configured router
    """
    global _source_provider
    _source_provider = source_provider
    return router


def get_source() -> MacSource:
    """Dependency to get MacSource instance."""
    if _source_provider is None:
        raise HTTPException(
            status_code=500,
            detail="Mac source not initialized. Call setup_mac_routes first."
        )
    return _source_provider()


@router.get("/status")
async def get_status(source: MacSource = Depends(get_source)) -> Dict[str, Any]:
    """
    Get current Mac source status.

    Returns:
        Status dict with state, service status, and connected clients
    """
    try:
        status = await source.status()

        return {
            "status": "ok",
            "state": status.get("state", "unknown"),
            "service_active": status.get("service_active", False),
            "listening": status.get("listening", False),
            "rtp_port": status.get("rtp_port", 10001),
            "rs8m_port": status.get("rs8m_port", 10002),
            "rtcp_port": status.get("rtcp_port", 10003),
            "audio_output": status.get("audio_output", "hw:1,0"),
            "connected": status.get("connected", False),
            "client_names": status.get("client_names", []),
            "client_count": status.get("client_count", 0)
        }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "state": "error"
        }


@router.post("/restart")
async def restart_service(source: MacSource = Depends(get_source)) -> Dict[str, Any]:
    """
    Restart the Mac audio service.

    Returns:
        Result of restart operation
    """
    try:
        result = await source.command("restart", {})

        return {
            "status": "success" if result.get("success") else "error",
            "message": result.get("message", "Service restarted"),
            "error": result.get("error")
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Restart error: {str(e)}"
        }


@router.get("/connections")
async def get_connections(source: MacSource = Depends(get_source)) -> Dict[str, Any]:
    """
    Get list of connected Mac clients.

    Returns:
        Dict with connections (ip -> hostname) and count
    """
    try:
        result = await source.command("get_connections", {})

        if result.get("success"):
            return {
                "status": "success",
                "connections": result.get("connections", {}),
                "connection_count": result.get("connection_count", 0)
            }
        else:
            return {
                "status": "error",
                "message": result.get("error", "Failed to get connections")
            }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Error: {str(e)}"
        }


@router.get("/info")
async def get_info(source: MacSource = Depends(get_source)) -> Dict[str, Any]:
    """
    Get Mac source configuration information.

    Returns:
        Configuration and service details
    """
    try:
        status = await source.status()

        return {
            "status": "ok",
            "configuration": {
                "rtp_port": status.get("rtp_port", 10001),
                "rs8m_port": status.get("rs8m_port", 10002),
                "rtcp_port": status.get("rtcp_port", 10003),
                "audio_output": status.get("audio_output", "hw:1,0")
            },
            "service": {
                "name": source.service_name,
                "active": status.get("service_active", False)
            }
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Error: {str(e)}"
        }
