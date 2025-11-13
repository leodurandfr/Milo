"""
API routes for the Radio plugin
"""
from fastapi import APIRouter, HTTPException, Query, File, UploadFile, Form
from fastapi.responses import FileResponse, Response
from typing import List, Optional
from pydantic import BaseModel
import aiohttp
import asyncio
import base64
import logging

from backend.config.container import container
from backend.domain.audio_state import AudioSource

# Transparent 1x1 PNG used as a fallback for favicons
TRANSPARENT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/radio", tags=["radio"])


# === Pydantic models for validation ===

class PlayStationRequest(BaseModel):
    """Request to play a station"""
    station_id: str


class FavoriteRequest(BaseModel):
    """Request to manage favorites"""
    station_id: str
    station: Optional[dict] = None  # Full station object (optional) with deduplicated favicon


class MarkBrokenRequest(BaseModel):
    """Request to mark a station as broken"""
    station_id: str


class AddCustomStationRequest(BaseModel):
    """Request to add a custom station"""
    name: str
    url: str
    country: str = "France"
    genre: str = "Variety"
    favicon: str = ""
    bitrate: int = 128
    codec: str = "MP3"


class RemoveCustomStationRequest(BaseModel):
    """Request to remove a custom station"""
    station_id: str


# === Routes ===

@router.get("/stations")
async def search_stations(
    query: str = Query("", description="Search term"),
    country: str = Query("", description="Country filter"),
    genre: str = Query("", description="Genre filter"),
    limit: int = Query(10000, ge=1, le=10000, description="Max number of results"),
    favorites_only: bool = Query(False, description="Favorites only")
):
    """
    Search radio stations

    Args:
        query: Search term (station name or genre)
        country: Country filter (e.g., "France")
        genre: Genre filter (e.g., "Rock")
        limit: Max number of results (1–10000)
        favorites_only: If True, returns favorites only

    Returns:
        Dict with stations and total: {stations: [...], total: int}
    """
    try:
        plugin = container.radio_plugin()

        if favorites_only:
            # Load favorites (now including modified favorites with custom_metadata)
            favorites = await plugin.station_manager.get_favorites_with_metadata()

            # If no favorites, return an empty list
            if not favorites:
                return {"stations": [], "total": 0}

            # Filter if needed
            if query:
                query_lower = query.lower()
                favorites = [
                    s for s in favorites
                    if query_lower in s['name'].lower() or query_lower in s['genre'].lower()
                ]

            if country:
                country_lower = country.lower()
                favorites = [s for s in favorites if country_lower in s['country'].lower()]

            if genre:
                genre_lower = genre.lower()
                favorites = [s for s in favorites if genre_lower in s['genre'].lower()]

            # Enrich with favorite status (already favorites, but ensures is_favorite flag)
            enriched_stations = plugin.station_manager.enrich_with_favorite_status(favorites[:limit])

            return {
                "stations": enriched_stations,
                "total": len(favorites)
            }

        else:
            # Search across all stations
            result = await plugin.radio_api.search_stations(
                query=query,
                country=country,
                genre=genre,
                limit=limit
            )

            # Filter out broken stations
            filtered_stations = plugin.station_manager.filter_broken_stations(result["stations"])

            # Enrich with favorite status
            enriched_stations = plugin.station_manager.enrich_with_favorite_status(filtered_stations)

            return {
                "stations": enriched_stations,
                "total": result["total"]
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur recherche stations: {str(e)}")


@router.get("/station/{station_id}")
async def get_station(station_id: str):
    """
    Retrieve the details of a station by its ID

    Args:
        station_id: Station UUID

    Returns:
        Station details
    """
    try:
        plugin = container.radio_plugin()
        station = await plugin.radio_api.get_station_by_id(station_id)

        if not station:
            raise HTTPException(status_code=404, detail="Station introuvable")

        # Enrich with favorite status
        enriched = plugin.station_manager.enrich_with_favorite_status([station])
        return enriched[0]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur récupération station: {str(e)}")


@router.post("/play")
async def play_station(request: PlayStationRequest):
    """
    Play a radio station

    Args:
        request: Request with station_id

    Returns:
        Command result
    """
    try:
        state_machine = container.audio_state_machine()
        plugin = container.radio_plugin()

        # Check if the plugin is started
        if not await is_plugin_started():
            # Start via state machine (transition to RADIO)
            success = await state_machine.transition_to_source(AudioSource.RADIO)
            if not success:
                raise HTTPException(
                    status_code=500,
                    detail="Impossible de démarrer le plugin Radio"
                )

        # Send the play_station command
        result = await plugin.handle_command("play_station", {"station_id": request.station_id})

        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Échec lecture station")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lecture: {str(e)}")


@router.post("/stop")
async def stop_playback():
    """
    Stop current playback

    Returns:
        Command result
    """
    try:
        plugin = container.radio_plugin()
        result = await plugin.handle_command("stop_playback", {})

        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Échec arrêt lecture")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur arrêt: {str(e)}")


@router.post("/favorites/add")
async def add_favorite(request: FavoriteRequest):
    """
    Add a station to favorites

    Args:
        request: Request with station_id

    Returns:
        Operation result
    """
    try:
        plugin = container.radio_plugin()
        # Pass the station object if provided (with deduplicated favicon)
        command_data = {"station_id": request.station_id}
        if request.station:
            command_data["station"] = request.station

        result = await plugin.handle_command("add_favorite", command_data)

        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Échec ajout favori")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur ajout favori: {str(e)}")


@router.post("/favorites/remove")
async def remove_favorite(request: FavoriteRequest):
    """
    Remove a station from favorites

    Args:
        request: Request with station_id

    Returns:
        Operation result
    """
    try:
        plugin = container.radio_plugin()
        result = await plugin.handle_command("remove_favorite", {"station_id": request.station_id})

        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Échec retrait favori")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur retrait favori: {str(e)}")


@router.get("/favorites")
async def get_favorites():
    """
    Retrieve the list of favorite stations

    Returns:
        List of favorite stations with details
    """
    try:
        plugin = container.radio_plugin()

        # Use the new async method that handles priority chain (custom > cache > API)
        favorites = await plugin.station_manager.get_favorites_with_metadata()

        return favorites

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur récupération favoris: {str(e)}")


@router.post("/broken/mark")
async def mark_broken(request: MarkBrokenRequest):
    """
    Mark a station as broken

    Args:
        request: Request with station_id

    Returns:
        Operation result
    """
    try:
        plugin = container.radio_plugin()
        result = await plugin.handle_command("mark_broken", {"station_id": request.station_id})

        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Échec marquage station")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur marquage: {str(e)}")


@router.post("/broken/reset")
async def reset_broken_stations():
    """
    Reset the list of broken stations

    Returns:
        Operation result
    """
    try:
        plugin = container.radio_plugin()
        result = await plugin.handle_command("reset_broken", {})

        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Échec reset stations")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur reset: {str(e)}")


@router.get("/status")
async def get_status():
    """
    Retrieve the Radio plugin status

    Returns:
        Current plugin state (service, current playback, current station, etc.)
    """
    try:
        plugin = container.radio_plugin()
        status = await plugin.get_status()
        return status

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur status: {str(e)}")


@router.get("/stats")
async def get_stats():
    """
    Retrieve statistics (number of favorites, broken stations, etc.)

    Returns:
        Plugin statistics
    """
    try:
        plugin = container.radio_plugin()
        stats = plugin.station_manager.get_stats()
        return stats

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur stats: {str(e)}")


@router.get("/countries")
async def get_countries():
    """
    Retrieve the list of all countries available from the Radio Browser API

    Returns:
        List of countries with name and station count
        Format: [{"name": "France", "stationcount": 2345}, ...]
    """
    try:
        plugin = container.radio_plugin()
        countries = await plugin.radio_api.get_available_countries()
        return countries

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur récupération pays: {str(e)}")


@router.post("/custom/add")
async def add_custom_station(
    name: str = Form(...),
    url: str = Form(...),
    country: str = Form("France"),
    genre: str = Form("Variety"),
    bitrate: int = Form(128),
    codec: str = Form("MP3"),
    image: Optional[UploadFile] = File(None)
):
    """
    Add a custom station with an optional image

    Args:
        name: Station name
        url: Audio stream URL
        country: Country (default: "France")
        genre: Music genre (default: "Variety")
        bitrate: Bitrate in kbps (default: 128)
        codec: Audio codec (default: "MP3")
        image: Image file (optional, max 5MB, formats: JPG, PNG, WEBP, GIF)

    Returns:
        The created station with its ID
    """
    try:
        plugin = container.radio_plugin()
        image_filename = ""

        # If an image is provided, validate and save it
        if image and image.filename:
            # Read file content
            file_content = await image.read()

            # Validate and save the image
            success, saved_filename, error = await plugin.station_manager.image_manager.validate_and_save_image(
                file_content=file_content,
                filename=image.filename
            )

            if not success:
                raise HTTPException(
                    status_code=400,
                    detail=f"Erreur image: {error}"
                )

            image_filename = saved_filename

        # Create the station
        result = await plugin.station_manager.add_custom_station(
            name=name,
            url=url,
            country=country,
            genre=genre,
            image_filename=image_filename,
            bitrate=bitrate,
            codec=codec
        )

        if not result.get("success"):
            # On failure, delete the image that was uploaded
            if image_filename:
                await plugin.station_manager.image_manager.delete_image(image_filename)

            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Échec ajout station personnalisée")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur ajout station personnalisée: {str(e)}")


@router.post("/custom/remove")
async def remove_custom_station(request: RemoveCustomStationRequest):
    """
    Remove a custom station

    Args:
        request: Request with station_id

    Returns:
        Operation result
    """
    try:
        plugin = container.radio_plugin()
        success = await plugin.station_manager.remove_custom_station(request.station_id)

        if not success:
            raise HTTPException(
                status_code=400,
                detail="Échec suppression station personnalisée"
            )

        return {"success": True, "message": "Station personnalisée supprimée"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur suppression station personnalisée: {str(e)}")


@router.post("/custom/update-image")
async def update_station_image(
    station_id: str = Form(...),
    image: UploadFile = File(...)
):
    """
    Update the image of a favorite station

    Args:
        station_id: Station ID (must be a favorite station)
        image: New image file (max 10MB, formats: JPG, PNG, WEBP, GIF)

    Returns:
        The updated station
    """
    try:
        plugin = container.radio_plugin()

        # Ensure it is a favorite station
        if not plugin.station_manager.is_favorite(station_id):
            raise HTTPException(
                status_code=400,
                detail="Seules les stations favorites peuvent avoir leur image modifiée"
            )

        # Read and validate the new image
        if not image or not image.filename:
            raise HTTPException(status_code=400, detail="Image requise")

        file_content = await image.read()

        # Validate and save the new image
        success, saved_filename, error = await plugin.station_manager.image_manager.validate_and_save_image(
            file_content=file_content,
            filename=image.filename
        )

        if not success:
            raise HTTPException(
                status_code=400,
                detail=f"Erreur image: {error}"
            )

        # Update the favorite's image
        update_success = await plugin.station_manager.update_favorite_image(station_id, saved_filename)

        if not update_success:
            raise HTTPException(status_code=500, detail="Échec de la mise à jour de l'image")

        # Retrieve the updated station
        favorites = await plugin.station_manager.get_favorites_with_metadata()
        station = next((f for f in favorites if f.get('id') == station_id), None)

        return {
            "success": True,
            "message": "Image mise à jour",
            "station": station
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur mise à jour image: {str(e)}")


@router.post("/custom/remove-image")
async def remove_station_image(
    station_id: str = Form(...)
):
    """
    Remove the image of a favorite station

    Args:
        station_id: Station ID (must be a favorite station)

    Returns:
        The updated station without image
    """
    try:
        plugin = container.radio_plugin()

        # Ensure it is a favorite station
        if not plugin.station_manager.is_favorite(station_id):
            raise HTTPException(
                status_code=400,
                detail="Seules les stations favorites peuvent avoir leur image modifiée"
            )

        # Remove the favorite's image
        remove_success = await plugin.station_manager.remove_favorite_image(station_id)

        if not remove_success:
            raise HTTPException(status_code=500, detail="Échec de la suppression de l'image")

        # Retrieve the updated station
        favorites = await plugin.station_manager.get_favorites_with_metadata()
        station = next((f for f in favorites if f.get('id') == station_id), None)

        return {
            "success": True,
            "message": "Image supprimée",
            "station": station
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur suppression image: {str(e)}")


@router.get("/custom")
async def get_custom_stations():
    """
    Retrieve all custom stations (both modified metadata and manually created stations)

    Returns:
        Dict of station_id → metadata (merged from modified_metadata and manual_stations)
    """
    try:
        plugin = container.radio_plugin()

        # Get both dictionaries
        modified_metadata = plugin.station_manager.get_modified_metadata()
        manual_stations = plugin.station_manager.get_manual_stations()

        # Merge both dictionaries (manual_stations will override if same key)
        all_custom_stations = {**modified_metadata, **manual_stations}

        return all_custom_stations

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur récupération stations personnalisées: {str(e)}")


@router.put("/custom/update")
async def update_custom_station(
    station_id: str = Form(...),
    name: str = Form(...),
    url: str = Form(...),
    country: str = Form("France"),
    genre: str = Form("Variety"),
    image: Optional[UploadFile] = File(None),
    remove_image: str = Form("false")
):
    """
    Update an existing custom station

    Args:
        station_id: Station ID to update (must be a custom station)
        name: New station name
        url: New audio stream URL
        country: New country
        genre: New music genre
        image: New image file (optional)
        remove_image: "true" to remove current image

    Returns:
        The updated station
    """
    try:
        plugin = container.radio_plugin()

        # Verify it's a custom station
        if not station_id.startswith("custom_"):
            raise HTTPException(
                status_code=400,
                detail="Seules les stations personnalisées peuvent être modifiées"
            )

        # Check if station exists
        existing_station = plugin.station_manager.get_custom_station_by_id(station_id)
        if not existing_station:
            raise HTTPException(status_code=404, detail="Station personnalisée introuvable")

        image_filename = None
        should_remove_image = remove_image.lower() == "true"

        # If a new image is provided, validate and save it
        if image and image.filename:
            # Read file content
            file_content = await image.read()

            # Validate and save the image
            success, saved_filename, error = await plugin.station_manager.image_manager.validate_and_save_image(
                file_content=file_content,
                filename=image.filename
            )

            if not success:
                raise HTTPException(
                    status_code=400,
                    detail=f"Erreur image: {error}"
                )

            image_filename = saved_filename

        # Update the station
        result = await plugin.station_manager.update_custom_station(
            station_id=station_id,
            name=name,
            url=url,
            country=country,
            genre=genre,
            image_filename=image_filename,
            remove_image=should_remove_image
        )

        if not result.get("success"):
            # On failure, delete the newly uploaded image if any
            if image_filename:
                await plugin.station_manager.image_manager.delete_image(image_filename)

            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Échec modification station personnalisée")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur modification station personnalisée: {str(e)}")


@router.post("/custom/from-favorite")
async def create_custom_from_favorite(
    station_id: str = Form(...),
    name: str = Form(...),
    url: str = Form(...),
    country: str = Form("France"),
    genre: str = Form("Variety"),
    image: Optional[UploadFile] = File(None),
    remove_image: str = Form("false")
):
    """
    Create a custom station from a favorite station
    This allows "editing" favorite stations by creating a custom version

    Args:
        station_id: Original favorite station ID
        name: Station name (can be modified)
        url: Audio stream URL (can be modified)
        country: Country (can be modified)
        genre: Music genre (can be modified)
        image: New image file (optional)
        remove_image: "true" to not include original image

    Returns:
        The created custom station
    """
    try:
        plugin = container.radio_plugin()

        # Verify it's a favorite station
        if not plugin.station_manager.is_favorite(station_id):
            raise HTTPException(
                status_code=400,
                detail="Seules les stations favorites peuvent être converties"
            )

        image_filename = None

        # If a new image is provided, validate and save it
        if image and image.filename:
            # Read file content
            file_content = await image.read()

            # Validate and save the image
            success, saved_filename, error = await plugin.station_manager.image_manager.validate_and_save_image(
                file_content=file_content,
                filename=image.filename
            )

            if not success:
                raise HTTPException(
                    status_code=400,
                    detail=f"Erreur image: {error}"
                )

            image_filename = saved_filename

        # Create the custom station
        result = await plugin.station_manager.create_custom_from_favorite(
            station_id=station_id,
            name=name,
            url=url,
            country=country,
            genre=genre,
            image_filename=image_filename
        )

        if not result.get("success"):
            # On failure, delete the newly uploaded image if any
            if image_filename:
                await plugin.station_manager.image_manager.delete_image(image_filename)

            raise HTTPException(
                status_code=400,
                detail=result.get("error", "Échec création station personnalisée")
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur création station personnalisée: {str(e)}")


@router.post("/favorites/modify-metadata")
async def modify_favorite_metadata(
    station_id: str = Form(...),
    name: str = Form(...),
    url: str = Form(...),
    country: str = Form("France"),
    genre: str = Form("Variety"),
    image: Optional[UploadFile] = File(None),
    remove_image: str = Form("false")
):
    """
    Modifies metadata of a favorite station while preserving its score

    Unlike creating a custom station, this directly modifies the favorite
    and preserves votes/clickcount/score from RadioBrowserAPI
    """
    try:
        plugin = container.radio_plugin()

        image_filename = None
        should_remove_image = remove_image.lower() == "true"

        # If a new image is provided, validate and save it
        if image and image.filename:
            # Read file content
            file_content = await image.read()

            # Validate and save the image
            success, saved_filename, error = await plugin.station_manager.image_manager.validate_and_save_image(
                file_content=file_content,
                filename=image.filename
            )

            if not success:
                raise HTTPException(
                    status_code=400,
                    detail=f"Erreur image: {error}"
                )

            image_filename = saved_filename

        # Modify favorite metadata
        result = await plugin.station_manager.modify_favorite_metadata(
            station_id=station_id,
            name=name,
            url=url,
            country=country,
            genre=genre,
            image_filename=image_filename if image_filename else ("" if should_remove_image else None)
        )

        if result["success"]:
            return {
                "success": True,
                "station": result["station"]
            }
        else:
            # On failure, delete the newly uploaded image if any
            if image_filename:
                await plugin.station_manager.image_manager.delete_image(image_filename)

            raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error modifying favorite: {str(e)}")


@router.post("/favorites/restore-metadata")
async def restore_favorite_metadata(station_id: str = Form(...)):
    """
    Restores original metadata of a modified favorite station
    """
    try:
        plugin = container.radio_plugin()
        result = await plugin.station_manager.restore_favorite_metadata(
            station_id=station_id,
            radio_api=plugin.radio_api
        )

        if result["success"]:
            return {"success": True}
        else:
            raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error restoring favorite: {str(e)}")


@router.get("/images/{filename}")
async def get_station_image(filename: str):
    """
    Serve a radio station image

    Args:
        filename: Image filename (e.g., "abc123.jpg")

    Returns:
        Image file
    """
    try:
        plugin = container.radio_plugin()
        image_path = plugin.station_manager.image_manager.get_image_path(filename)

        if not image_path or not image_path.exists():
            raise HTTPException(status_code=404, detail="Image introuvable")

        # Determine media_type based on extension
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
                "Cache-Control": "public, max-age=31536000",  # Cache 1 year
                "Content-Disposition": f"inline; filename={filename}"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur récupération image: {str(e)}")


@router.get("/favicon")
async def get_favicon_proxy(url: str = Query(..., description="Favicon URL to proxy")):
    """
    Proxy for radio station favicons

    Solves CORS issues and automatically handles HTTP→HTTPS redirects.
    Returns a 1x1 transparent image on error (prevents 404s on the frontend).

    Args:
        url: Original favicon URL

    Returns:
        Favicon image with appropriate CORS headers, or a transparent PNG if unavailable
    """
    try:
        # Validate that the URL starts with http:// or https://
        if not url.startswith(('http://', 'https://')):
            logger.warning(f"Invalid favicon URL: {url}")
            return _return_transparent_png()

        # Download the favicon with redirect handling
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=5),
                allow_redirects=True,  # Automatically follows HTTP→HTTPS redirects
                headers={'User-Agent': 'Milo/1.0'}
            ) as resp:
                if resp.status != 200:
                    logger.debug(f"Favicon not available (HTTP {resp.status}): {url}")
                    return _return_transparent_png()

                # Read content
                content = await resp.read()

                # Ensure content is not empty
                if not content or len(content) == 0:
                    logger.debug(f"Empty favicon: {url}")
                    return _return_transparent_png()

                # Determine content-type
                content_type = resp.headers.get('Content-Type', 'image/x-icon')

                # Return image with CORS and cache headers
                return Response(
                    content=content,
                    media_type=content_type,
                    headers={
                        "Cache-Control": "public, max-age=86400",  # Cache 24h
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "GET"
                    }
                )

    except asyncio.TimeoutError:
        logger.debug(f"Favicon download timeout: {url}")
        return _return_transparent_png()
    except aiohttp.ClientError as e:
        logger.debug(f"Favicon download error {url}: {e}")
        return _return_transparent_png()
    except Exception as e:
        logger.warning(f"Favicon proxy error {url}: {e}")
        return _return_transparent_png()


def _return_transparent_png() -> Response:
    """Return a 1x1 transparent PNG image"""
    return Response(
        content=TRANSPARENT_PNG,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=3600",  # Cache 1h (shorter than success)
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET"
        }
    )


# === Helpers ===

async def is_plugin_started() -> bool:
    """Check whether the Radio plugin is started"""
    try:
        state_machine = container.audio_state_machine()
        system_state = state_machine.system_state

        return (
            system_state.active_source == AudioSource.RADIO and
            system_state.plugin_state.value in ["ready", "connected"]
        )
    except Exception:
        return False