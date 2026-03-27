# backend/sources/radio/routes.py
"""
FastAPI routes for Radio audio source.

Provides REST API endpoints for:
- Status: Get current radio source status
- Playback: Play/stop stations
- Favorites: Manage favorite stations
- Custom stations: Create, update, delete custom stations
- Search: Search RadioBrowser API
- Images: Serve station artwork
"""
import asyncio
import base64
import logging
from typing import Dict, Any, Optional

import aiohttp
from fastapi import APIRouter, HTTPException, Query, File, UploadFile, Form, Depends
from backend.api.route_helpers import run_source_command
from fastapi.responses import FileResponse, Response

from backend.api.source_dependency import make_source_dependency
from backend.sources.radio.source import RadioSource
from backend.sources.radio.models import (
    PlayStationRequest,
    FavoriteRequest,
)

# Transparent 1x1 PNG used as a fallback for favicons
TRANSPARENT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/radio",
    tags=["radio"],
    responses={404: {"description": "Not found"}},
)

set_source_provider, get_source = make_source_dependency("Radio")


def setup_radio_routes(source_provider) -> APIRouter:
    """Configure routes with source provider."""
    set_source_provider(source_provider)
    return router


# === Status Routes ===

@router.get("/status")
async def get_status(source: RadioSource = Depends(get_source)) -> Dict[str, Any]:
    """
    Get current Radio source status.

    Returns:
        Current state (service, playback, station, etc.)
    """
    try:
        status = await source.status()
        return {
            "status": "success",
            **status
        }
    except Exception as e:
        logger.error(f"Status error: {e}")
        return {
            "status": "error",
            "message": str(e),
            "service_active": False,
            "mpv_connected": False,
            "is_playing": False
        }


# === Playback Routes ===

@router.post("/play")
async def play_station(
    request: PlayStationRequest,
    source: RadioSource = Depends(get_source)
) -> Dict[str, Any]:
    """
    Play a radio station.

    Args:
        request: Request with station_id

    Returns:
        Command result
    """
    return await run_source_command(
        source, "play_station",
        {"station_id": request.station_id, "station": request.station},
        "Playback"
    )


@router.post("/stop")
async def stop_playback(source: RadioSource = Depends(get_source)) -> Dict[str, Any]:
    """
    Stop current playback.

    Returns:
        Command result
    """
    return await run_source_command(source, "stop_playback", {}, "Stop")


# === Search Routes ===

@router.get("/stations")
async def search_stations(
    query: str = Query("", description="Search term"),
    country: str = Query("", description="Country filter"),
    genre: str = Query("", description="Genre filter"),
    limit: int = Query(300, ge=1, le=1000, description="Max results"),
    favorites_only: bool = Query(False, description="Favorites only"),
    source: RadioSource = Depends(get_source)
) -> Dict[str, Any]:
    """
    Search radio stations.

    Args:
        query: Search term (station name or genre)
        country: Country filter
        genre: Genre filter
        limit: Max number of results
        favorites_only: If True, returns favorites only

    Returns:
        Dict with stations and total
    """
    try:
        if favorites_only:
            favorites = await source.station_data.get_favorites_with_metadata()

            if not favorites:
                return {"stations": [], "total": 0}

            # Filter if needed
            if query:
                query_lower = query.lower()
                favorites = [
                    s for s in favorites
                    if query_lower in s.get('name', '').lower() or query_lower in s.get('genre', '').lower()
                ]

            if country:
                country_lower = country.lower()
                favorites = [s for s in favorites if country_lower in s.get('country', '').lower()]

            if genre:
                genre_lower = genre.lower()
                favorites = [s for s in favorites if genre_lower in s.get('genre', '').lower()]

            enriched_stations = source.station_data.enrich_with_favorite_status(favorites[:limit])

            return {
                "stations": enriched_stations,
                "total": len(favorites)
            }

        else:
            result = await source.radio_api.search_stations(
                query=query,
                country=country,
                genre=genre,
                limit=limit
            )

            enriched_stations = source.station_data.enrich_with_favorite_status(result["stations"])

            response = {
                "stations": enriched_stations,
                "total": result["total"]
            }
            if result.get("network_error"):
                response["network_error"] = True
            return response

    except Exception as e:
        logger.error("Search error: %s", e)
        raise HTTPException(status_code=500, detail=f"Search error: {str(e)}")


@router.get("/countries")
async def get_countries(source: RadioSource = Depends(get_source)):
    """
    Get list of available countries from RadioBrowser API.

    Returns:
        List of countries with station counts
    """
    try:
        return await source.radio_api.get_available_countries()
    except Exception as e:
        logger.error("Countries error: %s", e)
        raise HTTPException(status_code=500, detail=f"Countries error: {str(e)}")


# === Favorites Routes ===

@router.post("/favorites/add")
async def add_favorite(
    request: FavoriteRequest,
    source: RadioSource = Depends(get_source)
) -> Dict[str, Any]:
    """
    Add station to favorites.

    Args:
        request: Request with station_id

    Returns:
        Operation result
    """
    command_data = {"station_id": request.station_id}
    if request.station:
        command_data["station"] = request.station
    return await run_source_command(source, "add_favorite", command_data, "Add favorite")


@router.delete("/favorites/{station_id}")
async def remove_favorite(
    station_id: str,
    source: RadioSource = Depends(get_source)
) -> Dict[str, Any]:
    """
    Remove station from favorites.

    Args:
        station_id: Station UUID

    Returns:
        Operation result
    """
    return await run_source_command(
        source, "remove_favorite", {"station_id": station_id}, "Remove favorite"
    )


@router.post("/favorites/modify-metadata")
async def modify_favorite_metadata(
    station_id: str = Form(...),
    name: str = Form(...),
    url: str = Form(...),
    country: str = Form(""),
    genre: str = Form(""),
    codec: str = Form(""),
    bitrate: int = Form(0),
    image: Optional[UploadFile] = File(None),
    remove_image: str = Form("false"),
    source: RadioSource = Depends(get_source)
) -> Dict[str, Any]:
    """
    Modify metadata of a favorite station.
    """
    try:
        image_filename = None
        should_remove_image = remove_image.lower() == "true"

        if image and image.filename:
            file_content = await image.read()
            success, saved_filename, error = await source.station_data.image_manager.validate_and_save_image(
                file_content=file_content,
                filename=image.filename
            )

            if not success:
                raise HTTPException(status_code=400, detail=f"Image error: {error}")

            image_filename = saved_filename

        result = await source.station_data.modify_favorite_metadata(
            station_id=station_id,
            name=name,
            url=url,
            country=country,
            genre=genre,
            codec=codec,
            bitrate=bitrate,
            image_filename=image_filename if image_filename else ("" if should_remove_image else None)
        )

        if result["success"]:
            return {"success": True, "station": result["station"]}
        else:
            if image_filename:
                await source.station_data.image_manager.delete_image(image_filename)
            raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Modify favorite error: %s", e)
        raise HTTPException(status_code=500, detail=f"Modify favorite error: {str(e)}")


@router.post("/favorites/restore-metadata")
async def restore_favorite_metadata(
    station_id: str = Form(...),
    source: RadioSource = Depends(get_source)
) -> Dict[str, Any]:
    """
    Restore original metadata of a modified favorite station.
    """
    try:
        result = await source.station_data.restore_favorite_metadata(
            station_id=station_id,
            radio_api=source.radio_api
        )

        if result["success"]:
            return {"success": True}
        else:
            raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Restore favorite error: %s", e)
        raise HTTPException(status_code=500, detail=f"Restore favorite error: {str(e)}")


# === Custom Stations Routes ===

@router.get("/custom")
async def get_custom_stations(source: RadioSource = Depends(get_source)) -> Dict[str, Dict[str, Any]]:
    """
    Get all custom stations (modified metadata and manually created).

    Returns:
        Dict of station_id → metadata
    """
    try:
        modified_metadata = source.station_data.get_modified_metadata()
        manual_stations = source.station_data.get_manual_stations()
        return {**modified_metadata, **manual_stations}
    except Exception as e:
        logger.error("Custom stations error: %s", e)
        raise HTTPException(status_code=500, detail=f"Custom stations error: {str(e)}")


@router.post("/custom/add")
async def add_custom_station(
    name: str = Form(...),
    url: str = Form(...),
    country: str = Form(""),
    genre: str = Form(""),
    bitrate: int = Form(0),
    codec: str = Form(""),
    image: Optional[UploadFile] = File(None),
    source: RadioSource = Depends(get_source)
) -> Dict[str, Any]:
    """
    Add a custom station with optional image.

    Args:
        name: Station name
        url: Audio stream URL
        country: Country (optional)
        genre: Music genre (optional)
        bitrate: Bitrate in kbps (optional)
        codec: Audio codec (optional)
        image: Image file (optional, max 5MB)

    Returns:
        Created station with ID
    """
    try:
        image_filename = ""

        if image and image.filename:
            file_content = await image.read()
            success, saved_filename, error = await source.station_data.image_manager.validate_and_save_image(
                file_content=file_content,
                filename=image.filename
            )

            if not success:
                raise HTTPException(status_code=400, detail=f"Image error: {error}")

            image_filename = saved_filename

        result = await source.station_data.add_custom_station(
            name=name,
            url=url,
            country=country,
            genre=genre,
            image_filename=image_filename,
            bitrate=bitrate,
            codec=codec
        )

        if not result.get("success"):
            if image_filename:
                await source.station_data.image_manager.delete_image(image_filename)
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Add custom station failed")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Add custom station error: %s", e)
        raise HTTPException(status_code=500, detail=f"Add custom station error: {str(e)}")


@router.delete("/custom/{station_id}")
async def remove_custom_station(
    station_id: str,
    source: RadioSource = Depends(get_source)
) -> Dict[str, Any]:
    """
    Remove a custom station.

    Args:
        station_id: Custom station ID

    Returns:
        Operation result
    """
    try:
        success = await source.station_data.remove_custom_station(station_id)

        if not success:
            raise HTTPException(status_code=400, detail="Remove custom station failed")

        return {"success": True, "message": "Custom station removed"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Remove custom station error: %s", e)
        raise HTTPException(status_code=500, detail=f"Remove custom station error: {str(e)}")


@router.delete("/custom/{station_id}/image")
async def remove_station_image(
    station_id: str,
    source: RadioSource = Depends(get_source)
) -> Dict[str, Any]:
    """
    Remove the image of a favorite station.

    Args:
        station_id: Station ID (must be a favorite)

    Returns:
        Updated station without image
    """
    try:
        if not source.station_data.is_favorite(station_id):
            raise HTTPException(status_code=400, detail="Only favorites can have their image modified")

        remove_success = await source.station_data.remove_favorite_image(station_id)

        if not remove_success:
            raise HTTPException(status_code=500, detail="Image removal failed")

        favorites = await source.station_data.get_favorites_with_metadata()
        station = next((f for f in favorites if f.get('id') == station_id), None)

        return {
            "success": True,
            "message": "Image removed",
            "station": station
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Remove image error: %s", e)
        raise HTTPException(status_code=500, detail=f"Remove image error: {str(e)}")


# === Image Routes ===

@router.get("/images/{filename}")
async def get_station_image(
    filename: str,
    source: RadioSource = Depends(get_source)
) -> FileResponse:
    """
    Serve a radio station image.

    Args:
        filename: Image filename

    Returns:
        Image file
    """
    try:
        image_path = source.station_data.image_manager.get_image_path(filename)

        if not image_path or not image_path.exists():
            raise HTTPException(status_code=404, detail="Image not found")

        ext = image_path.suffix.lower()
        media_type_map = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.webp': 'image/webp',
            '.gif': 'image/gif'
        }
        media_type = media_type_map.get(ext, 'application/octet-stream')

        return FileResponse(
            path=str(image_path),
            media_type=media_type,
            headers={
                "Cache-Control": "public, max-age=31536000",
                "Content-Disposition": f"inline; filename={filename}"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Image error: %s", e)
        raise HTTPException(status_code=500, detail=f"Image error: {str(e)}")


@router.get("/favicon")
async def get_favicon_proxy(url: str = Query(..., description="Favicon URL to proxy")) -> Response:
    """
    Proxy for radio station favicons.

    Solves CORS issues and handles HTTP→HTTPS redirects.
    Returns a 1x1 transparent image on error.

    Args:
        url: Original favicon URL

    Returns:
        Favicon image with CORS headers, or transparent PNG if unavailable
    """
    try:
        if not url.startswith(('http://', 'https://')):
            return _return_transparent_png()

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=5),
                allow_redirects=True,
                headers={'User-Agent': 'Milo/1.0'}
            ) as resp:
                if resp.status != 200:
                    return _return_transparent_png()

                content = await resp.read()

                if not content or len(content) == 0:
                    return _return_transparent_png()

                content_type = resp.headers.get('Content-Type', 'image/x-icon')

                return Response(
                    content=content,
                    media_type=content_type,
                    headers={
                        "Cache-Control": "public, max-age=86400",
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "GET"
                    }
                )

    except asyncio.TimeoutError:
        return _return_transparent_png()
    except aiohttp.ClientError:
        return _return_transparent_png()
    except Exception:
        return _return_transparent_png()


def _return_transparent_png() -> Response:
    """Return a 1x1 transparent PNG image."""
    return Response(
        content=TRANSPARENT_PNG,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET"
        }
    )
