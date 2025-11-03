"""
Routes API pour le plugin Radio
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

# PNG transparent 1x1 pixel pour fallback favicons
TRANSPARENT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/radio", tags=["radio"])


# === Modèles Pydantic pour validation ===

class PlayStationRequest(BaseModel):
    """Requête pour jouer une station"""
    station_id: str


class FavoriteRequest(BaseModel):
    """Requête pour gérer les favoris"""
    station_id: str
    station: Optional[dict] = None  # Objet station complet (optionnel) avec favicon dédupliqué


class MarkBrokenRequest(BaseModel):
    """Requête pour marquer une station comme cassée"""
    station_id: str


class AddCustomStationRequest(BaseModel):
    """Requête pour ajouter une station personnalisée"""
    name: str
    url: str
    country: str = "France"
    genre: str = "Variety"
    favicon: str = ""
    bitrate: int = 128
    codec: str = "MP3"


class RemoveCustomStationRequest(BaseModel):
    """Requête pour supprimer une station personnalisée"""
    station_id: str


# === Routes ===

@router.get("/stations")
async def search_stations(
    query: str = Query("", description="Terme de recherche"),
    country: str = Query("", description="Filtre pays"),
    genre: str = Query("", description="Filtre genre"),
    limit: int = Query(10000, ge=1, le=10000, description="Nombre max de résultats"),
    favorites_only: bool = Query(False, description="Seulement les favoris")
):
    """
    Recherche des stations radio

    Args:
        query: Terme de recherche (nom de station ou genre)
        country: Filtre par pays (ex: "France")
        genre: Filtre par genre (ex: "Rock")
        limit: Nombre max de résultats (1-10000)
        favorites_only: Si True, retourne seulement les favoris

    Returns:
        Dict avec stations et total: {stations: [...], total: int}
    """
    try:
        plugin = container.radio_plugin()

        if favorites_only:
            # Charger directement depuis le cache local (radio_data.json)
            # Les favoris sont déjà stockés avec métadonnées complètes
            stations = plugin.station_manager.get_favorites_with_metadata()

            # Si aucun favori, retourner liste vide
            if not stations:
                return {"stations": [], "total": 0}

            # Filtrer si nécessaire
            if query:
                query_lower = query.lower()
                stations = [
                    s for s in stations
                    if query_lower in s['name'].lower() or query_lower in s['genre'].lower()
                ]

            if country:
                country_lower = country.lower()
                stations = [s for s in stations if country_lower in s['country'].lower()]

            if genre:
                genre_lower = genre.lower()
                stations = [s for s in stations if genre_lower in s['genre'].lower()]

            # Enrichir avec statut favori (déjà fait pour cached_stations, mais nécessaire pour fetched)
            enriched_stations = plugin.station_manager.enrich_with_favorite_status(stations[:limit])

            return {
                "stations": enriched_stations,
                "total": len(stations)
            }

        else:
            # Recherche dans toutes les stations
            result = await plugin.radio_api.search_stations(
                query=query,
                country=country,
                genre=genre,
                limit=limit
            )

            # Filtrer stations cassées
            filtered_stations = plugin.station_manager.filter_broken_stations(result["stations"])

            # Enrichir avec statut favori
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
    Récupère les détails d'une station par son ID

    Args:
        station_id: UUID de la station

    Returns:
        Détails de la station
    """
    try:
        plugin = container.radio_plugin()
        station = await plugin.radio_api.get_station_by_id(station_id)

        if not station:
            raise HTTPException(status_code=404, detail="Station introuvable")

        # Enrichir avec statut favori
        enriched = plugin.station_manager.enrich_with_favorite_status([station])
        return enriched[0]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur récupération station: {str(e)}")


@router.post("/play")
async def play_station(request: PlayStationRequest):
    """
    Joue une station radio

    Args:
        request: Requête avec station_id

    Returns:
        Résultat de la commande
    """
    try:
        state_machine = container.audio_state_machine()
        plugin = container.radio_plugin()

        # Vérifier si le plugin est démarré
        if not await is_plugin_started():
            # Démarrer via state machine (transition vers RADIO)
            success = await state_machine.transition_to_source(AudioSource.RADIO)
            if not success:
                raise HTTPException(
                    status_code=500,
                    detail="Impossible de démarrer le plugin Radio"
                )

        # Envoyer la commande play_station
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
    Arrête la lecture en cours

    Returns:
        Résultat de la commande
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
    Ajoute une station aux favoris

    Args:
        request: Requête avec station_id

    Returns:
        Résultat de l'opération
    """
    try:
        plugin = container.radio_plugin()
        # Passer l'objet station s'il est fourni (avec favicon dédupliqué)
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
    Retire une station des favoris

    Args:
        request: Requête avec station_id

    Returns:
        Résultat de l'opération
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
    Récupère la liste des stations favorites

    Returns:
        Liste des stations favorites avec détails
    """
    try:
        plugin = container.radio_plugin()
        favorite_ids = plugin.station_manager.get_favorites()

        if not favorite_ids:
            return []

        # Charger les détails des stations
        stations = []
        for station_id in favorite_ids:
            station = await plugin.radio_api.get_station_by_id(station_id)
            if station:
                station['is_favorite'] = True
                stations.append(station)

        return stations

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur récupération favoris: {str(e)}")


@router.post("/broken/mark")
async def mark_broken(request: MarkBrokenRequest):
    """
    Marque une station comme cassée

    Args:
        request: Requête avec station_id

    Returns:
        Résultat de l'opération
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
    Réinitialise la liste des stations cassées

    Returns:
        Résultat de l'opération
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
    Récupère le status du plugin Radio

    Returns:
        État actuel du plugin (service, lecture en cours, station actuelle, etc.)
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
    Récupère les statistiques (nombre de favoris, stations cassées, etc.)

    Returns:
        Statistiques du plugin
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
    Récupère la liste de tous les pays disponibles depuis Radio Browser API

    Returns:
        Liste des pays avec nom et nombre de stations
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
    Ajoute une station personnalisée avec image optionnelle

    Args:
        name: Nom de la station
        url: URL du flux audio
        country: Pays (défaut: "France")
        genre: Genre musical (défaut: "Variety")
        bitrate: Bitrate en kbps (défaut: 128)
        codec: Codec audio (défaut: "MP3")
        image: Fichier image (optionnel, max 5MB, formats: JPG, PNG, WEBP, GIF)

    Returns:
        La station créée avec son ID
    """
    try:
        plugin = container.radio_plugin()
        image_filename = ""

        # Si une image est fournie, la valider et la sauvegarder
        if image and image.filename:
            # Lire le contenu du fichier
            file_content = await image.read()

            # Valider et sauvegarder l'image
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

        # Créer la station
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
            # Si échec, supprimer l'image qui a été uploadée
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
    Supprime une station personnalisée

    Args:
        request: Requête avec station_id

    Returns:
        Résultat de l'opération
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
    Met à jour l'image d'une station personnalisée

    Args:
        station_id: ID de la station (doit être une custom station)
        image: Nouveau fichier image (max 10MB, formats: JPG, PNG, WEBP, GIF)

    Returns:
        La station mise à jour
    """
    try:
        plugin = container.radio_plugin()

        # Vérifier que c'est une custom station
        if not station_id.startswith("custom_"):
            raise HTTPException(
                status_code=400,
                detail="Cette route fonctionne uniquement avec les stations personnalisées"
            )

        # Vérifier que la station existe
        station = plugin.station_manager.get_custom_station_by_id(station_id)
        if not station:
            raise HTTPException(status_code=404, detail=f"Station {station_id} introuvable")

        # Lire et valider la nouvelle image
        if not image or not image.filename:
            raise HTTPException(status_code=400, detail="Image requise")

        file_content = await image.read()

        # Valider et sauvegarder la nouvelle image
        success, saved_filename, error = await plugin.station_manager.image_manager.validate_and_save_image(
            file_content=file_content,
            filename=image.filename
        )

        if not success:
            raise HTTPException(
                status_code=400,
                detail=f"Erreur image: {error}"
            )

        # Supprimer l'ancienne image si elle existe
        old_image_filename = station.get('image_filename')
        if old_image_filename:
            await plugin.station_manager.image_manager.delete_image(old_image_filename)

        # Mettre à jour la station
        station['favicon'] = f"/api/radio/images/{saved_filename}"
        station['image_filename'] = saved_filename

        # Trouver et mettre à jour la station dans la liste
        custom_stations = plugin.station_manager.get_custom_stations()
        for custom_station in custom_stations:
            if custom_station['id'] == station_id:
                custom_station['favicon'] = station['favicon']
                custom_station['image_filename'] = saved_filename
                break

        # Sauvegarder
        await plugin.station_manager._save_custom_stations()

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
    Supprime l'image d'une station personnalisée

    Args:
        station_id: ID de la station (doit être une custom station)

    Returns:
        La station mise à jour sans image
    """
    try:
        plugin = container.radio_plugin()

        # Vérifier que c'est une custom station
        if not station_id.startswith("custom_"):
            raise HTTPException(
                status_code=400,
                detail="Cette route fonctionne uniquement avec les stations personnalisées"
            )

        # Vérifier que la station existe
        station = plugin.station_manager.get_custom_station_by_id(station_id)
        if not station:
            raise HTTPException(status_code=404, detail=f"Station {station_id} introuvable")

        # Vérifier qu'une image existe
        image_filename = station.get('image_filename')
        if not image_filename:
            raise HTTPException(status_code=400, detail="Cette station n'a pas d'image")

        # Supprimer le fichier image
        await plugin.station_manager.image_manager.delete_image(image_filename)

        # Mettre à jour la station
        station['favicon'] = ""
        station['image_filename'] = ""

        # Mettre à jour dans la liste
        custom_stations = plugin.station_manager.get_custom_stations()
        for custom_station in custom_stations:
            if custom_station['id'] == station_id:
                custom_station['favicon'] = ""
                custom_station['image_filename'] = ""
                break

        # Sauvegarder
        await plugin.station_manager._save_custom_stations()

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
    Récupère toutes les stations personnalisées

    Returns:
        Liste des stations personnalisées
    """
    try:
        plugin = container.radio_plugin()
        custom_stations = plugin.station_manager.get_custom_stations()

        # Enrichir avec statut favori
        return plugin.station_manager.enrich_with_favorite_status(custom_stations)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur récupération stations personnalisées: {str(e)}")


@router.get("/images/{filename}")
async def get_station_image(filename: str):
    """
    Sert une image de station radio

    Args:
        filename: Nom du fichier image (ex: "abc123.jpg")

    Returns:
        Fichier image
    """
    try:
        plugin = container.radio_plugin()
        image_path = plugin.station_manager.image_manager.get_image_path(filename)

        if not image_path or not image_path.exists():
            raise HTTPException(status_code=404, detail="Image introuvable")

        # Déterminer le media_type basé sur l'extension
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
                "Cache-Control": "public, max-age=31536000",  # Cache 1 an
                "Content-Disposition": f"inline; filename={filename}"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur récupération image: {str(e)}")


@router.get("/favicon")
async def get_favicon_proxy(url: str = Query(..., description="URL du favicon à proxifier")):
    """
    Proxy pour les favicons de stations radio

    Résout les problèmes CORS et gère automatiquement les redirections HTTP→HTTPS
    Retourne une image transparente 1x1 en cas d'erreur (évite les erreurs 404 côté frontend)

    Args:
        url: URL du favicon original

    Returns:
        Image favicon avec headers CORS appropriés, ou PNG transparent si indisponible
    """
    try:
        # Valider que l'URL commence par http:// ou https://
        if not url.startswith(('http://', 'https://')):
            logger.warning(f"URL favicon invalide: {url}")
            return _return_transparent_png()

        # Télécharger le favicon avec gestion des redirections
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=5),
                allow_redirects=True,  # Suit automatiquement les redirections HTTP→HTTPS
                headers={'User-Agent': 'Milo/1.0'}
            ) as resp:
                if resp.status != 200:
                    logger.debug(f"Favicon non disponible (HTTP {resp.status}): {url}")
                    return _return_transparent_png()

                # Lire le contenu
                content = await resp.read()

                # Vérifier que le contenu n'est pas vide
                if not content or len(content) == 0:
                    logger.debug(f"Favicon vide: {url}")
                    return _return_transparent_png()

                # Déterminer le content-type
                content_type = resp.headers.get('Content-Type', 'image/x-icon')

                # Retourner l'image avec headers CORS et cache
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
        logger.debug(f"Timeout téléchargement favicon: {url}")
        return _return_transparent_png()
    except aiohttp.ClientError as e:
        logger.debug(f"Erreur téléchargement favicon {url}: {e}")
        return _return_transparent_png()
    except Exception as e:
        logger.warning(f"Erreur proxy favicon {url}: {e}")
        return _return_transparent_png()


def _return_transparent_png() -> Response:
    """Retourne une image PNG transparente 1x1 pixel"""
    return Response(
        content=TRANSPARENT_PNG,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=3600",  # Cache 1h (moins que succès)
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET"
        }
    )


# === Helpers ===

async def is_plugin_started() -> bool:
    """Vérifie si le plugin Radio est démarré"""
    try:
        state_machine = container.audio_state_machine()
        system_state = state_machine.system_state

        return (
            system_state.active_source == AudioSource.RADIO and
            system_state.plugin_state.value in ["ready", "connected"]
        )
    except Exception:
        return False
