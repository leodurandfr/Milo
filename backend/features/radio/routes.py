# backend/features/radio/routes.py
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
from typing import Dict, Any, Callable, Optional

import aiohttp
from fastapi import APIRouter, HTTPException, Query, File, UploadFile, Form, Depends
from fastapi.responses import FileResponse, Response

from backend.features.radio.source import RadioSource
from backend.features.radio.models import (
    PlayStationRequest,
    FavoriteRequest,
    MarkBrokenRequest,
    RemoveCustomStationRequest
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

# Source provider function
_source_provider: Optional[Callable[[], RadioSource]] = None


def setup_radio_routes(source_provider: Callable[[], RadioSource]) -> APIRouter:
    """
    Configure routes with source provider.

    Args:
        source_provider: Function returning RadioSource instance

    Returns:
        Configured router
    """
    global _source_provider
    _source_provider = source_provider
    return router


def get_source() -> RadioSource:
    """Dependency to get RadioSource instance."""
    if _source_provider is None:
        raise HTTPException(
            status_code=500,
            detail="Radio source not initialized. Call setup_radio_routes first."
        )
    return _source_provider()


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
            "status": "ok",
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


@router.get("/stats")
async def get_stats(source: RadioSource = Depends(get_source)) -> Dict[str, int]:
    """
    Get statistics (favorites, broken stations, etc.)

    Returns:
        Statistics dict
    """
    try:
        return source.station_data.get_stats() if source.station_data else {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stats error: {str(e)}")


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
    try:
        command_data = {"station_id": request.station_id, "station": request.station}
        result = await source.command("play_station", command_data)

        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Playback failed")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Playback error: {str(e)}")


@router.post("/stop")
async def stop_playback(source: RadioSource = Depends(get_source)) -> Dict[str, Any]:
    """
    Stop current playback.

    Returns:
        Command result
    """
    try:
        result = await source.command("stop_playback", {})

        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Stop failed")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stop error: {str(e)}")


# === Search Routes ===

@router.get("/stations")
async def search_stations(
    query: str = Query("", description="Search term"),
    country: str = Query("", description="Country filter"),
    genre: str = Query("", description="Genre filter"),
    limit: int = Query(10000, ge=1, le=10000, description="Max results"),
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

            filtered_stations = source.station_data.filter_broken_stations(result["stations"])
            enriched_stations = source.station_data.enrich_with_favorite_status(filtered_stations)

            return {
                "stations": enriched_stations,
                "total": result["total"]
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search error: {str(e)}")


@router.get("/station/{station_id}")
async def get_station(
    station_id: str,
    source: RadioSource = Depends(get_source)
) -> Dict[str, Any]:
    """
    Get station details by ID.

    Args:
        station_id: Station UUID

    Returns:
        Station details
    """
    try:
        station = await source.radio_api.get_station_by_id(station_id)

        if not station:
            raise HTTPException(status_code=404, detail="Station not found")

        enriched = source.station_data.enrich_with_favorite_status([station])
        return enriched[0]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Station error: {str(e)}")


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
        raise HTTPException(status_code=500, detail=f"Countries error: {str(e)}")


# === Favorites Routes ===

@router.get("/favorites")
async def get_favorites(source: RadioSource = Depends(get_source)):
    """
    Get list of favorite stations with details.

    Returns:
        List of favorite stations
    """
    try:
        return await source.station_data.get_favorites_with_metadata()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Favorites error: {str(e)}")


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
    try:
        command_data = {"station_id": request.station_id}
        if request.station:
            command_data["station"] = request.station

        result = await source.command("add_favorite", command_data)

        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Add favorite failed")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Add favorite error: {str(e)}")


@router.post("/favorites/remove")
async def remove_favorite(
    request: FavoriteRequest,
    source: RadioSource = Depends(get_source)
) -> Dict[str, Any]:
    """
    Remove station from favorites.

    Args:
        request: Request with station_id

    Returns:
        Operation result
    """
    try:
        result = await source.command("remove_favorite", {"station_id": request.station_id})

        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Remove favorite failed")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Remove favorite error: {str(e)}")


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
        raise HTTPException(status_code=500, detail=f"Restore favorite error: {str(e)}")


# === Broken Stations Routes ===

@router.post("/broken/mark")
async def mark_broken(
    request: MarkBrokenRequest,
    source: RadioSource = Depends(get_source)
) -> Dict[str, Any]:
    """
    Mark a station as broken.

    Args:
        request: Request with station_id

    Returns:
        Operation result
    """
    try:
        result = await source.command("mark_broken", {"station_id": request.station_id})

        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Mark broken failed")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Mark broken error: {str(e)}")


@router.post("/broken/reset")
async def reset_broken_stations(source: RadioSource = Depends(get_source)) -> Dict[str, Any]:
    """
    Reset the list of broken stations.

    Returns:
        Operation result
    """
    try:
        result = await source.command("reset_broken", {})

        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Reset failed")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reset error: {str(e)}")


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
        raise HTTPException(status_code=500, detail=f"Add custom station error: {str(e)}")


@router.post("/custom/remove")
async def remove_custom_station(
    request: RemoveCustomStationRequest,
    source: RadioSource = Depends(get_source)
) -> Dict[str, Any]:
    """
    Remove a custom station.

    Args:
        request: Request with station_id

    Returns:
        Operation result
    """
    try:
        success = await source.station_data.remove_custom_station(request.station_id)

        if not success:
            raise HTTPException(status_code=400, detail="Remove custom station failed")

        return {"success": True, "message": "Custom station removed"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Remove custom station error: {str(e)}")


@router.put("/custom/update")
async def update_custom_station(
    station_id: str = Form(...),
    name: str = Form(...),
    url: str = Form(...),
    country: str = Form(""),
    genre: str = Form(""),
    image: Optional[UploadFile] = File(None),
    remove_image: str = Form("false"),
    source: RadioSource = Depends(get_source)
) -> Dict[str, Any]:
    """
    Update an existing custom station.

    Args:
        station_id: Station ID to update
        name: New station name
        url: New audio stream URL
        country: New country
        genre: New music genre
        image: New image file (optional)
        remove_image: "true" to remove current image

    Returns:
        Updated station
    """
    try:
        if not station_id.startswith("custom_"):
            raise HTTPException(status_code=400, detail="Only custom stations can be modified")

        existing_station = source.station_data.get_custom_station_by_id(station_id)
        if not existing_station:
            raise HTTPException(status_code=404, detail="Custom station not found")

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

        result = await source.station_data.update_custom_station(
            station_id=station_id,
            name=name,
            url=url,
            country=country,
            genre=genre,
            image_filename=image_filename,
            remove_image=should_remove_image
        )

        if not result.get("success"):
            if image_filename:
                await source.station_data.image_manager.delete_image(image_filename)
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Update custom station failed")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Update custom station error: {str(e)}")


@router.post("/custom/update-image")
async def update_station_image(
    station_id: str = Form(...),
    image: UploadFile = File(...),
    source: RadioSource = Depends(get_source)
) -> Dict[str, Any]:
    """
    Update the image of a favorite station.

    Args:
        station_id: Station ID (must be a favorite)
        image: New image file

    Returns:
        Updated station
    """
    try:
        if not source.station_data.is_favorite(station_id):
            raise HTTPException(status_code=400, detail="Only favorites can have their image modified")

        if not image or not image.filename:
            raise HTTPException(status_code=400, detail="Image required")

        file_content = await image.read()
        success, saved_filename, error = await source.station_data.image_manager.validate_and_save_image(
            file_content=file_content,
            filename=image.filename
        )

        if not success:
            raise HTTPException(status_code=400, detail=f"Image error: {error}")

        update_success = await source.station_data.update_favorite_image(station_id, saved_filename)

        if not update_success:
            raise HTTPException(status_code=500, detail="Image update failed")

        favorites = await source.station_data.get_favorites_with_metadata()
        station = next((f for f in favorites if f.get('id') == station_id), None)

        return {
            "success": True,
            "message": "Image updated",
            "station": station
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Update image error: {str(e)}")


@router.post("/custom/remove-image")
async def remove_station_image(
    station_id: str = Form(...),
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
        raise HTTPException(status_code=500, detail=f"Remove image error: {str(e)}")


@router.post("/custom/from-favorite")
async def create_custom_from_favorite(
    station_id: str = Form(...),
    name: str = Form(...),
    url: str = Form(...),
    country: str = Form(""),
    genre: str = Form(""),
    image: Optional[UploadFile] = File(None),
    remove_image: str = Form("false"),
    source: RadioSource = Depends(get_source)
) -> Dict[str, Any]:
    """
    Create a custom station from a favorite station.

    This allows "editing" favorites by creating a custom version.
    """
    try:
        if not source.station_data.is_favorite(station_id):
            raise HTTPException(status_code=400, detail="Only favorites can be converted")

        image_filename = None

        if image and image.filename:
            file_content = await image.read()
            success, saved_filename, error = await source.station_data.image_manager.validate_and_save_image(
                file_content=file_content,
                filename=image.filename
            )

            if not success:
                raise HTTPException(status_code=400, detail=f"Image error: {error}")

            image_filename = saved_filename

        # Create using add_custom_station since create_custom_from_favorite is not in our simplified data.py
        result = await source.station_data.add_custom_station(
            name=name,
            url=url,
            country=country,
            genre=genre,
            image_filename=image_filename or ""
        )

        if not result.get("success"):
            if image_filename:
                await source.station_data.image_manager.delete_image(image_filename)
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Create custom station failed")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Create from favorite error: {str(e)}")


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
