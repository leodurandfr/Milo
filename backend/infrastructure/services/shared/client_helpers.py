# backend/infrastructure/services/shared/client_helpers.py
"""
Shared helper functions for multiroom client operations.

These helpers eliminate code duplication across volume, DSP, and routing services.
"""
from typing import List, Dict, Any


async def get_available_clients(snapcast_service) -> List[Dict[str, Any]]:
    """
    Get list of available clients with their dsp_id.

    Filters clients to only include those that:
    - Have a valid dsp_id (non-empty)
    - Are currently available

    Args:
        snapcast_service: SnapcastService instance

    Returns:
        List of dicts with 'dsp_id' and 'available' keys for available clients
    """
    clients = await snapcast_service.get_clients()
    return [
        {"dsp_id": client.get("dsp_id", ""), "available": client.get("available", True)}
        for client in clients
        if client.get("dsp_id") and client.get("available", True)
    ]


async def get_available_client_ids(snapcast_service) -> List[str]:
    """
    Get list of available client IDs (dsp_ids).

    Convenience wrapper that returns just the IDs.

    Args:
        snapcast_service: SnapcastService instance

    Returns:
        List of client IDs (dsp_ids) for available clients
    """
    clients = await get_available_clients(snapcast_service)
    return [c["dsp_id"] for c in clients]


def normalize_client_id(hostname: str) -> str:
    """
    Normalize client hostname to standard format.

    Converts 'milo' to 'local' for consistency.
    All other hostnames pass through unchanged.

    Args:
        hostname: Client hostname (e.g., 'milo', 'local', '192.168.1.100')

    Returns:
        Normalized hostname ('local' for main device, hostname otherwise)
    """
    return "local" if hostname in ("local", "milo") else hostname
