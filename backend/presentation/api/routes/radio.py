"""
Routes API pour le plugin Radio
"""
from fastapi import APIRouter, HTTPException, Query, File, UploadFile, Form
from fastapi.responses import FileResponse
from typing import List, Optional
from pydantic import BaseModel

from backend.config.container import container
from backend.domain.audio_state import AudioSource


router = APIRouter(prefix="/radio", tags=["radio"])


# === Modèles Pydantic pour validation ===

class PlayStationRequest(BaseModel):
    """Requête pour jouer une station"""
    station_id: str


class FavoriteRequest(BaseModel):
    """Requête pour gérer les favoris"""
    station_id: str


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
    limit: int = Query(100, ge=1, le=500, description="Nombre max de résultats"),
    favorites_only: bool = Query(False, description="Seulement les favoris")
):
    """
    Recherche des stations radio

    Args:
        query: Terme de recherche (nom de station ou genre)
        country: Filtre par pays (ex: "France")
        genre: Filtre par genre (ex: "Rock")
        limit: Nombre max de résultats (1-500)
        favorites_only: Si True, retourne seulement les favoris

    Returns:
        Liste des stations avec métadonnées
    """
    try:
        plugin = container.radio_plugin()

        if favorites_only:
            # Récupérer les favoris
            favorite_ids = plugin.station_manager.get_favorites()
            if not favorite_ids:
                return []

            # Charger les stations complètes
            stations = []
            for station_id in favorite_ids:
                station = await plugin.radio_api.get_station_by_id(station_id)
                if station:
                    stations.append(station)

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

            # Enrichir avec statut favori
            return plugin.station_manager.enrich_with_favorite_status(stations[:limit])

        else:
            # Recherche dans toutes les stations
            stations = await plugin.radio_api.search_stations(
                query=query,
                country=country,
                genre=genre,
                limit=limit
            )

            # Filtrer stations cassées
            stations = plugin.station_manager.filter_broken_stations(stations)

            # Enrichir avec statut favori
            return plugin.station_manager.enrich_with_favorite_status(stations)

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
        result = await plugin.handle_command("add_favorite", {"station_id": request.station_id})

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
    Met à jour l'image d'une station (personnalisée ou non)

    Args:
        station_id: ID de la station
        image: Nouveau fichier image (max 10MB, formats: JPG, PNG, WEBP, GIF)

    Returns:
        La station mise à jour
    """
    try:
        plugin = container.radio_plugin()

        # Vérifier que la station existe
        station = await plugin.radio_api.get_station_by_id(station_id)
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

        # Supprimer l'ancienne image si elle existe et est locale
        old_image_filename = station.get('image_filename')
        if old_image_filename:
            await plugin.station_manager.image_manager.delete_image(old_image_filename)

        # Mettre à jour la station
        station['favicon'] = f"/api/radio/images/{saved_filename}"
        station['image_filename'] = saved_filename

        # Si c'est une station personnalisée, mettre à jour dans les settings
        if station.get('is_custom'):
            # Trouver et mettre à jour la station dans la liste
            custom_stations = plugin.station_manager.get_custom_stations()
            for custom_station in custom_stations:
                if custom_station['id'] == station_id:
                    custom_station['favicon'] = station['favicon']
                    custom_station['image_filename'] = saved_filename
                    break

            # Sauvegarder
            await plugin.station_manager._save_custom_stations()
        else:
            # Pour les stations non-custom, sauvegarder dans station_images
            await plugin.station_manager.add_station_image(
                station_id=station_id,
                station_name=station.get('name', ''),
                image_filename=saved_filename,
                country=station.get('country', ''),
                genre=station.get('genre', '')
            )

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
    Supprime l'image importée d'une station et revient à l'image originale

    Args:
        station_id: ID de la station

    Returns:
        La station mise à jour sans image personnalisée
    """
    try:
        plugin = container.radio_plugin()

        # Vérifier que la station existe
        station = await plugin.radio_api.get_station_by_id(station_id)
        if not station:
            raise HTTPException(status_code=404, detail=f"Station {station_id} introuvable")

        # Vérifier qu'une image personnalisée existe
        image_filename = station.get('image_filename')
        if not image_filename:
            raise HTTPException(status_code=400, detail="Cette station n'a pas d'image personnalisée")

        # Supprimer le fichier image
        await plugin.station_manager.image_manager.delete_image(image_filename)

        # Mettre à jour la station
        station['image_filename'] = ""

        # Si c'est une station personnalisée, remettre favicon à vide
        # Sinon, restaurer l'URL favicon originale (si elle existe)
        if station.get('is_custom'):
            station['favicon'] = ""

            # Mettre à jour dans les settings
            custom_stations = plugin.station_manager.get_custom_stations()
            for custom_station in custom_stations:
                if custom_station['id'] == station_id:
                    custom_station['favicon'] = ""
                    custom_station['image_filename'] = ""
                    break

            # Sauvegarder
            await plugin.station_manager._save_custom_stations()
        else:
            # Pour les stations non-custom, retirer de station_images
            await plugin.station_manager.remove_station_image(station_id)
            # Essayer de récupérer le favicon original
            station['favicon'] = ""

        return {
            "success": True,
            "message": "Image personnalisée supprimée",
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


@router.get("/stations-with-images")
async def get_stations_with_images():
    """
    Récupère toutes les stations avec des images modifiées
    (custom et non-custom)

    Returns:
        Liste des stations avec images personnalisées
    """
    try:
        plugin = container.radio_plugin()

        # Récupérer les stations non-custom avec images modifiées
        stations_with_images = plugin.station_manager.get_stations_with_images()

        # Récupérer les stations custom qui ont une image
        custom_stations = plugin.station_manager.get_custom_stations()
        custom_with_images = [s for s in custom_stations if s.get('image_filename')]

        # Merger les deux listes
        all_stations = stations_with_images + custom_with_images

        # Enrichir avec statut favori
        return plugin.station_manager.enrich_with_favorite_status(all_stations)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur récupération stations avec images: {str(e)}")


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
